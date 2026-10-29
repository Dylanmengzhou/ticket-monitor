import json
import os
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from flight_mcp import provider, service, worker
from flight_mcp.channels import PROVIDERS, metadata_key
from flight_mcp.models import SearchRequest
from flight_mcp.results import comparisons
from flight_mcp.service import FlightService
from flight_mcp.store import Store


def test_five_provider_default_and_isolated_work_units(settings, request_data, monkeypatch):
    request_data.providers = list(PROVIDERS)
    monkeypatch.setattr(service, "spawn_worker", lambda *a: SimpleNamespace(pid=os.getpid()))
    app = FlightService(settings)
    result = app.search_flights(request_data)
    legs = app.store.job(result["job_id"])["result"]["legs"]
    assert [leg["provider"] for leg in legs] == list(PROVIDERS)
    assert len({app.settings.profile_for(p) for p in PROVIDERS}) == 5
    assert app.settings.lock_for("fliggy_h5") == app.settings.lock_for("fliggy")
    assert app.settings.lock_for("qunar") != app.settings.lock_for("ctrip")
    assert SearchRequest(routes=request_data.routes).providers == list(PROVIDERS)
    with pytest.raises(ValidationError):
        SearchRequest(routes=request_data.routes, providers=["unknown"])


@pytest.mark.parametrize("source", PROVIDERS)
def test_provider_output_uses_the_real_channel(source, settings, leg, request_data, offer, monkeypatch):
    fake = SimpleNamespace(paths=[{"path": "test", "status": "ok"}],
        fetch=lambda *a: [SimpleNamespace(extra=json.dumps([offer]))])
    monkeypatch.setattr(provider, "crawler_for", lambda settings, provider="ctrip": fake)
    result = provider.fetch_leg(settings, {**leg, "provider": source}, request_data.model_dump(mode="json"))
    assert result["status"] == "ok"
    flight = result["flights"][0]
    assert flight["source"] == source
    assert flight["booking_url"] == result["query_url"]
    assert {"ctrip": "ctrip.com", "qunar": "qunar.com", "fliggy": "fliggy.com",
            "tongcheng": "ly.com", "tuniu": "tuniu.com"}[source] in result["query_url"]


@pytest.mark.parametrize("source", PROVIDERS)
def test_vendored_crawlers_work_without_original_project(source, settings):
    crawler = provider.crawler_for(settings, source)
    assert crawler.name == source
    assert crawler.user_data_dir == str(settings.profile_for(source))
    assert crawler.login_url().startswith("https://")
    assert "flight_mcp.vendor" in type(crawler).__module__


def test_five_channels_do_not_mix_tax_bases(offer, leg, request_data):
    request = request_data.model_dump(mode="json")
    ctrip = provider.normalize_offers([offer], leg, request, "OBSERVED", "PC")[0][0]
    bare = provider.normalize_offers([dict(offer, intl=0, price=500)], {**leg, "provider": "fliggy"}, request, "OBSERVED", "PC")[0][0]
    assert bare["price"]["tax_included"] is False
    result = comparisons([{**leg, "provider": "ctrip", "status": "ok", "flights": [ctrip]},
                          {**leg, "provider": "fliggy", "status": "ok", "flights": [bare]}])[0]
    assert result["cheapest_reported"]["source"] == "fliggy"
    assert result["cheapest_tax_included"]["source"] == "ctrip"
    assert provider.price_basis("qunar", {"intl": 0})[0] is None
    assert provider.price_basis("fliggy", {"intl": 1})[0] is None
    assert provider.price_basis("fliggy", {"intl": 1, "price_basis": "adult_total"})[0] is True


def test_transfer_boolean_cannot_masquerade_as_direct(offer, leg, request_data):
    flights, _ = provider.normalize_offers([dict(offer, transfer=True)], leg,
        request_data.model_dump(mode="json"), "OBSERVED", "H5")
    assert flights == []


def test_provider_sessions_and_login_windows_are_independent(settings, monkeypatch):
    monkeypatch.setattr(service, "spawn_worker", lambda *a: SimpleNamespace(pid=os.getpid()))
    app = FlightService(settings)
    first = app.start_login("ctrip")
    second = app.start_login("qunar")
    assert first["login_id"] != second["login_id"]
    assert app.start_login("ctrip")["login_id"] == first["login_id"]
    worker._record_session_evidence(app.store, {"provider": "qunar", "scope": "international", "status": "ok"})
    assert app.get_session_status("qunar")["status"] == "verified"
    assert app.get_session_status("ctrip")["status"] == "not_logged_in"
    assert len(app.get_session_status("all")["providers"]) == 6


def test_old_database_migration_preserves_ctrip_login(settings):
    settings.prepare()
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute("CREATE TABLE logins (id TEXT PRIMARY KEY,status TEXT,action TEXT DEFAULT '',created_at TEXT,updated_at TEXT,error TEXT DEFAULT '',worker_pid INTEGER)")
        connection.execute("INSERT INTO logins(id,status,created_at,updated_at) VALUES ('OLD','saved','2026-01-01','2026-01-01')")
    assert Store(settings).login()["id"] == "OLD"
    assert Store(settings).login()["provider"] == "ctrip"


def test_fliggy_h5_retains_real_itinerary_and_tax_basis(day):
    from flight_mcp.vendor.crawlers.fliggy_h5 import parse_listing
    payload = {"data": {"items": [{"itemType": "FLIGHT_DIRECT", "data": [{
        "priceInfo": {"adultTotalPrice": 150000, "adultPrice": 100000, "adultTax": 50000},
        "flightInfos": [{"depTime": day+" 10:00", "arrTime": day+" 12:00", "flightSegments": [{
            "marketingFlightNo": "TEST001", "depCityCode": "SEL", "arrCityCode": "CTU",
            "depAirportCode": "ICN", "arrAirportCode": "TFU", "cabinName": "经济舱"}]}]}]}]}}
    row = parse_listing(payload, "SEL", "CTU", day)[0]
    extra = json.loads(row.extra)
    assert extra["code"] == "TEST001" and extra["arrDate"] == day
    assert extra["price"] == 1500 and extra["price_basis"] == "adult_total"
    assert extra["depAirportCode"] == "ICN" and not extra["transfer"]
