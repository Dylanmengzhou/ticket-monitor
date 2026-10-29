from datetime import date, timedelta

from flight_mcp.provider import normalize_offers

OBSERVED = "2026-10-28T08:00:00+00:00"


def process(rows, leg, request_data):
    return normalize_offers(rows, leg, request_data.model_dump(mode="json"), OBSERVED, "PC")


def test_direct_nonstop_is_a_hard_constraint(offer, leg, request_data):
    rows = [offer, dict(offer, code="CA100/MU200", transCity="上海", price=400),
            dict(offer, code="CA300", stopover=True, price=500),
            dict(offer, code="CA400/MU500", price=300)]
    flights, counts = process(rows, leg, request_data)
    assert [f["flight_number"] for f in flights] == ["OZ323"]
    assert flights[0]["nonstop"] is True
    assert counts["filtered_rows"] == 3


def test_calendar_wrong_date_and_nonfinite_prices_never_become_flights(offer, leg, request_data):
    tomorrow = str(date.fromisoformat(leg["date"]) + timedelta(days=1))
    rows = [{"price": 1}, dict(offer, depDate=tomorrow), dict(offer, price=float("nan")),
            dict(offer, price=float("inf")), dict(offer, price=-1), dict(offer, depDate="")]
    flights, counts = process(rows, leg, request_data)
    assert flights == []
    assert counts["calendar_rows"] == 1
    assert counts["wrong_date_rows"] == 2
    assert counts["invalid_rows"] == 3


def test_timezone_price_and_provenance_survive(offer, leg, request_data):
    flights, _ = process([offer], leg, request_data)
    flight = flights[0]
    assert flight["departure"]["local_datetime"].endswith("20:15:00+09:00")
    assert flight["arrival"]["local_datetime"].endswith("23:20:00+08:00")
    assert flight["duration_minutes"] == 245
    assert flight["price"] == {"amount": 1561.0, "currency": "CNY", "tax_included": True,
                               "tax_amount": 641.0, "basis": "ctrip_list_total", "is_booking_quote": False}
    assert flight["observed_at"] == OBSERVED
    assert "oneway-sel-ctu" in flight["booking_url"]


def test_explicit_airport_drops_other_and_unknown_airports(offer, leg, request_data):
    leg = {**leg, "destination": {**leg["destination"], "airport_code": "TFU"}}
    flights, counts = process([offer, dict(offer, arrAirportCode="CTU", price=300),
                              dict(offer, arrAirportCode=None, price=200)], leg, request_data)
    assert len(flights) == 1
    assert flights[0]["arrival"]["airport_code"] == "TFU"
    assert counts["filtered_rows"] == 2


def test_deduplicate_then_sort_and_limit(offer, leg, request_data):
    rows = [dict(offer, price=2000), offer, dict(offer, code="CA402", price=1000)]
    request_data.limit = 1
    flights, counts = process(rows, leg, request_data)
    assert counts["matching_flights"] == 2
    assert flights[0]["flight_number"] == "CA402"


def test_wrapping_midnight_departure_window(offer, leg, request_data):
    request_data.departure_after = "22:00"
    request_data.departure_before = "06:00"
    rows = [offer, dict(offer, code="CA402", depTime="23:00"),
            dict(offer, code="CA403", depTime="04:00")]
    flights, _ = process(rows, leg, request_data)
    assert {f["flight_number"] for f in flights} == {"CA402", "CA403"}
