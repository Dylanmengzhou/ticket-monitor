import json
import os
import threading
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from flight_mcp import bark, worker
from flight_mcp.provider import normalize_offers
from flight_mcp.store import Store


@contextmanager
def receiver(code=200):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"code": code}).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def completed_job(settings, request_data, leg, offer):
    store = Store(settings)
    identifier = str(uuid.uuid4())
    request = request_data.model_dump(mode="json")
    flights, _ = normalize_offers([offer], leg, request, "2026-10-29T00:00:00+00:00", "TEST")
    store.create_job(identifier, request, [{**leg, "provider": "ctrip"}])
    result = store.job(identifier)["result"]
    result.update(status="completed", finished_at="2026-10-29T00:00:00+00:00")
    result["legs"][0].update(status="ok", flights=flights)
    store.save_result(identifier, result)
    return store, identifier


def test_bark_configuration_is_private_and_does_not_send(settings):
    with receiver() as (url, received):
        view = bark.configure(settings, push_url=url+"/SYNTHETIC_DEVICE_KEY")
        assert view["notify_on_completion"] is True
        assert "SYNTHETIC_DEVICE_KEY" not in json.dumps(view)
        assert "SYNTHETIC_DEVICE_KEY" not in json.dumps(bark.status(settings))
        assert received == []
        if os.name != "nt":
            assert settings.bark_path.stat().st_mode & 0o777 == 0o600
        assert bark.send_test(settings)["status"] == "sent"
        assert received[0][0] == "/push"
        assert received[0][1]["device_key"] == "SYNTHETIC_DEVICE_KEY"


def test_completed_result_sent_once_with_observed_time_and_tax(settings, request_data, leg, offer):
    store, identifier = completed_job(settings, request_data, leg, offer)
    with receiver() as (url, received):
        bark.configure(settings, device_key="SYNTHETIC_KEY", server_url=url)
        assert bark.notify_job(settings, identifier, automatic=True)["status"] == "sent"
        assert bark.notify_job(settings, identifier)["duplicate_prevented"] is True
        assert len(received) == 1
        body = received[0][1]["body"]
        assert "首尔→成都" in body and "含税" in body and "2026-10-29T00:00:00" in body
        assert "SYNTHETIC_KEY" not in str(store.notification(identifier))


def test_manual_only_setting_does_not_block_explicit_push(settings, request_data, leg, offer):
    _, identifier = completed_job(settings, request_data, leg, offer)
    with receiver() as (url, received):
        bark.configure(settings, device_key="SYNTHETIC_KEY", server_url=url, notify_on_completion=False)
        assert bark.notify_job(settings, identifier, automatic=True)["status"] == "disabled"
        assert received == []
        assert bark.notify_job(settings, identifier)["status"] == "sent"


def test_declined_delivery_is_not_success_and_can_be_retried(settings, request_data, leg, offer):
    store, identifier = completed_job(settings, request_data, leg, offer)
    with receiver(400) as (url, received):
        bark.configure(settings, device_key="SYNTHETIC_KEY", server_url=url)
        assert bark.notify_job(settings, identifier)["status"] == "failed"
        assert store.notification(identifier)["status"] == "failed"


def test_unknown_delivery_does_not_repost_or_leak_errors(settings, request_data, leg, offer, monkeypatch):
    store, identifier = completed_job(settings, request_data, leg, offer)
    bark.configure(settings, device_key="SYNTHETIC_KEY")
    def fail(*a, **k):
        raise httpx.ReadTimeout("SYNTHETIC_KEY must not leak")
    monkeypatch.setattr(httpx.Client, "post", fail)
    result = bark.notify_job(settings, identifier)
    assert result["status"] == "unknown" and "SYNTHETIC_KEY" not in str(result)
    assert bark.notify_job(settings, identifier)["duplicate_prevented"] is True


def test_delivery_claim_is_atomic_across_connections(settings, request_data, leg, offer):
    store, identifier = completed_job(settings, request_data, leg, offer)
    second = Store(settings)
    outcomes = []
    threads = [threading.Thread(target=lambda s=s: outcomes.append(s.claim_notification(identifier))) for s in (store, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == [False, True]


def test_worker_sends_completion_after_query_even_without_mcp_polling(settings, request_data, leg, monkeypatch):
    store = Store(settings)
    identifier = str(uuid.uuid4())
    store.create_job(identifier, request_data.model_dump(mode="json"), [])
    notifications = []
    monkeypatch.setattr(bark, "notify_job", lambda *a, **k: notifications.append((a[1], k["automatic"])))
    worker.run_job(settings, identifier)
    assert notifications == [(identifier, True)]
    assert store.job(identifier)["status"] == "completed"


def test_notify_false_and_cancel_skip_bark(settings, request_data, monkeypatch):
    store = Store(settings)
    request_data.notify = False
    identifier = str(uuid.uuid4())
    store.create_job(identifier, request_data.model_dump(mode="json"), [])
    monkeypatch.setattr(bark, "notify_job", lambda *a, **k: pytest.fail("notification was not requested"))
    worker.run_job(settings, identifier)
    request_data.notify = True
    cancelled = str(uuid.uuid4())
    store.create_job(cancelled, request_data.model_dump(mode="json"), [])
    store.cancel(cancelled)
    worker.run_job(settings, cancelled)
    assert store.job(cancelled)["status"] == "cancelled"


@pytest.mark.parametrize("url", ["file:///secret", "https://user:pass@example.com", "https://example.com?secret=abc", "https://example.com:invalid"])
def test_unsafe_server_config_is_rejected_without_key_echo(settings, url):
    with pytest.raises(ValueError):
        bark.configure(settings, device_key="SYNTHETIC_KEY", server_url=url)


def test_corrupt_bark_config_can_be_reconfigured(settings):
    settings.prepare()
    settings.bark_path.write_text("{invalid json", encoding="utf8")
    assert bark.status(settings)["status"] == "configuration_error"
    assert bark.configure(settings, device_key="SYNTHETIC_KEY")["configured"] is True
