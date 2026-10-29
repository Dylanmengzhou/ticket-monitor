import json
import os
import subprocess
import sys
import threading
import time
import uuid

import pytest

from flight_mcp import worker
from flight_mcp.locking import ProfileBusy, profile_lock
from flight_mcp.service import FlightService
from flight_mcp.settings import Settings
from flight_mcp.store import Store, pid_alive


def create_job(settings, request_data, legs):
    store = Store(settings)
    identifier = str(uuid.uuid4())
    store.create_job(identifier, request_data.model_dump(mode="json"), legs)
    return store, identifier


def test_import_is_private_and_does_not_claim_verified(settings, tmp_path):
    source = tmp_path / "legacy"
    source.mkdir()
    payload = {"saved_at": 1, "cookies": [{"name": "secret", "value": "SYNTHETIC_COOKIE"}]}
    (source / "session-state.json").write_text(json.dumps(payload))
    (source / "Default").mkdir()
    (source / "Default/Cookies").write_text("SYNTHETIC_LOCAL_BROWSER_DATA")
    service = FlightService(settings)
    result = service.import_session(source)
    assert result["status"] == "saved_unverified"
    state = service.get_session_status()
    assert state["session_saved"] is True
    assert state["status"] == "saved_unverified"
    assert "SYNTHETIC_COOKIE" not in json.dumps(state)
    assert (source / "session-state.json").read_text() == json.dumps(payload)
    if os.name != "nt":
        assert settings.profile_path.stat().st_mode & 0o777 == 0o700
        assert (settings.profile_path / "session-state.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="已有"):
        service.import_session(source)


def test_new_login_invalidates_older_auth_evidence(settings):
    service = FlightService(settings)
    settings.profile_path.mkdir()
    (settings.profile_path / "session-state.json").write_text("{}")
    service.store.set_metadata("session_saved_at", "2026-10-28T08:00:00+00:00")
    service.store.set_metadata("session_evidence", {"domestic": {
        "state": "login_required", "at": "2026-10-28T07:00:00+00:00"}})
    assert service.get_session_status()["status"] == "saved_unverified"
    service.store.set_metadata("session_evidence", {"international": {
        "state": "verified", "at": "2026-10-28T09:00:00+00:00"}})
    assert service.get_session_status()["status"] == "verified"


def test_profile_lock_excludes_other_operations(settings):
    settings.prepare()
    with profile_lock(settings.lock_path):
        with pytest.raises(ProfileBusy):
            with profile_lock(settings.lock_path):
                pass
    with profile_lock(settings.lock_path):
        pass


def test_cancel_queued_job_never_starts_browser(settings, request_data, leg, monkeypatch):
    store, identifier = create_job(settings, request_data, [leg, leg])
    store.cancel(identifier)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: pytest.fail("cancelled job must not fetch"))
    worker.run_job(settings, identifier)
    result = store.job(identifier)["result"]
    assert result["status"] == "cancelled"
    assert all(item["status"] == "cancelled" for item in result["legs"])


def test_real_hung_child_is_terminated_and_lock_released(tmp_path, request_data, leg, monkeypatch):
    settings = Settings(tmp_path / "timeout", fetch_timeout=1)
    store, identifier = create_job(settings, request_data, [leg])
    monkeypatch.setattr(worker, "worker_command", lambda *a: [sys.executable, "-c", "import time; time.sleep(30)"])
    started = time.monotonic()
    worker.run_job(settings, identifier)
    assert time.monotonic() - started < 6
    result = store.job(identifier)["result"]
    assert result["status"] == "failed"
    assert result["legs"][0]["status"] == "timeout"
    with profile_lock(settings.lock_path):
        pass


def test_cancel_running_job_terminates_child(settings, request_data, leg, monkeypatch):
    store, identifier = create_job(settings, request_data, [leg, leg])
    monkeypatch.setattr(worker, "worker_command", lambda *a: [sys.executable, "-c", "import time; time.sleep(30)"])
    cancel = threading.Timer(0.5, lambda: store.cancel(identifier))
    cancel.start()
    try:
        worker.run_job(settings, identifier)
    finally:
        cancel.join()
    result = store.job(identifier)["result"]
    assert result["status"] == "cancelled"
    assert all(item["status"] == "cancelled" for item in result["legs"])


def test_pid_probe_does_not_stop_a_live_process():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert pid_alive(process.pid)
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert not pid_alive(process.pid)
    assert not pid_alive(None)
    assert not pid_alive(-1)


def test_partial_failure_preserves_successful_leg(settings, request_data, leg, monkeypatch):
    store, identifier = create_job(settings, request_data, [leg, leg])

    class FinishedProcess:
        returncode = 0
        pid = 99999999
        def __init__(self, command, **kwargs):
            index = int(command[-1])
            result = {**leg, "status": "ok" if index == 0 else "provider_error",
                      "flights": [{"flight_number": "TEST123"}] if index == 0 else [], "scope": "international"}
            (settings.work_dir / identifier / f"leg-{index}.json").write_text(json.dumps(result))
        def poll(self):
            return 0

    monkeypatch.setattr(worker.subprocess, "Popen", FinishedProcess)
    worker.run_job(settings, identifier)
    result = store.job(identifier)["result"]
    assert result["status"] == "partial"
    assert result["legs"][0]["flights"][0]["flight_number"] == "TEST123"
    assert result["legs"][1]["status"] == "provider_error"
    # A new server instance can read the durable result without the old MCP process.
    reconnected = FlightService(settings).get_search_result(identifier)
    assert reconnected["status"] == "partial"
    assert reconnected["progress"] == {"finished": 2, "total": 2}


def test_missing_and_stopped_workers_are_reported(settings, request_data, leg):
    store, identifier = create_job(settings, request_data, [leg])
    with store.connection() as c:
        c.execute("UPDATE jobs SET created_at=?,worker_pid=? WHERE id=?", ("2000-01-01T00:00:00+00:00", 99999999, identifier))
    service = FlightService(settings)
    assert service.get_search_result(identifier)["legs"][0]["status"] == "worker_stopped"
    assert service.get_search_result(str(uuid.uuid4()))["status"] == "not_found"
    with pytest.raises(ValueError):
        service.get_search_result("../../private")


def test_cancel_while_waiting_for_profile_is_prompt(tmp_path, request_data, leg):
    settings = Settings(tmp_path / "queue", lock_timeout=2)
    store, identifier = create_job(settings, request_data, [leg])
    cancel = threading.Timer(0.3, lambda: store.cancel(identifier))
    with profile_lock(settings.lock_path):
        cancel.start()
        start = time.monotonic()
        worker.run_job(settings, identifier)
        elapsed = time.monotonic() - start
    cancel.join()
    assert store.job(identifier)["status"] == "cancelled"
    assert elapsed < 1.5
