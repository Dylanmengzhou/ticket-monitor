import os
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from flight_mcp import provider, worker
from flight_mcp.locking import profile_lock
from flight_mcp.service import FlightService
from flight_mcp.store import Store


@pytest.mark.parametrize("action,status,saved", [("complete", "saved", True), ("cancel", "cancelled", False)])
def test_official_login_completion_and_cancel(settings, monkeypatch, action, status, saved):
    store = Store(settings)
    identifier = str(uuid.uuid4())
    store.create_login(identifier)
    store.update_login(identifier, action=action)
    state = {"saved": False}

    class Page:
        def goto(self, url, **kwargs):
            assert url == "https://www.ctrip.com/"
        def is_closed(self):
            return False
        def wait_for_timeout(self, value):
            pass

    class Crawler:
        def login_url(self):
            return "https://www.ctrip.com/"
        def new_page(self, context):
            return Page()
        @contextmanager
        def browser(self, **kwargs):
            assert kwargs["headless"] is False
            assert kwargs["save_session"] == "login"
            yield object()
            state["saved"] = True

    monkeypatch.setattr(provider, "crawler_for", lambda settings: Crawler())
    worker.login(settings, identifier)
    assert store.login(identifier)["status"] == status
    assert state["saved"] is saved
    assert bool(store.metadata("session_saved_at")) is saved


def test_login_while_profile_busy_returns_actionable_failure(settings, monkeypatch):
    store = Store(settings)
    identifier = str(uuid.uuid4())
    store.create_login(identifier)
    monkeypatch.setattr(provider, "crawler_for", lambda settings: pytest.fail("profile is busy"))
    with profile_lock(settings.lock_path):
        worker.login(settings, identifier)
    assert store.login(identifier)["status"] == "failed"
    assert "会话正在" in store.login(identifier)["error"]


def test_start_login_reuses_active_window(settings, monkeypatch):
    service = FlightService(settings)
    identifier = str(uuid.uuid4())
    service.store.create_login(identifier)
    service.store.update_login(identifier, status="active", worker_pid=os.getpid())
    result = service.start_login()
    assert result["login_id"] == identifier
    assert result["status"] == "active"
