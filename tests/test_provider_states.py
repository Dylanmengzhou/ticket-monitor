import json
from types import SimpleNamespace

import pytest

from flight_mcp import provider


@pytest.mark.parametrize("paths,rows,status", [
    ([{"path": "PC", "status": "login"}], [], "login_required"),
    ([{"path": "PC", "status": "blocked"}], [], "verification_required"),
    ([{"path": "日历", "status": "calendar"}], [{"price": 700}], "calendar_only"),
    ([{"path": "PC", "status": "empty"}], [], "no_data"),
    ([{"path": "PC", "status": "error"}], [], "provider_error"),
])
def test_failures_do_not_claim_no_flights(settings, leg, request_data, monkeypatch, paths, rows, status):
    fake = SimpleNamespace(paths=paths, fetch=lambda *a: [SimpleNamespace(extra=json.dumps(rows))])
    monkeypatch.setattr(provider, "crawler_for", lambda settings: fake)
    result = provider.fetch_leg(settings, leg, request_data.model_dump(mode="json"))
    assert result["status"] == status
    assert result["flights"] == []


def test_results_filtered_out_are_not_provider_failure(settings, leg, request_data, offer, monkeypatch):
    fake = SimpleNamespace(paths=[{"path": "PC", "status": "ok"}],
                           fetch=lambda *a: [SimpleNamespace(extra=json.dumps([offer]))])
    monkeypatch.setattr(provider, "crawler_for", lambda settings: fake)
    request_data.max_price = 1000
    result = provider.fetch_leg(settings, leg, request_data.model_dump(mode="json"))
    assert result["status"] == "no_matching_flights"
    assert result["counts"]["raw_offers"] == 1
