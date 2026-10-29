from datetime import timedelta

import pytest
from pydantic import ValidationError

from flight_mcp.locations import expand_routes, resolve_location
from flight_mcp.models import RouteQuery, SearchRequest, today_china


@pytest.mark.parametrize("name,city,airport", [
    ("首尔", "SEL", None), ("SEL", "SEL", None), ("仁川", "SEL", "ICN"),
    ("首尔仁川", "SEL", "ICN"), ("ICN", "SEL", "ICN"),
    ("成都", "CTU", None), ("CTU", "CTU", "CTU"), ("天府", "CTU", "TFU"),
    ("成都天府", "CTU", "TFU"), ("TFU", "CTU", "TFU"),
    ("北京", "BJS", None), ("大兴", "BJS", "PKX"), ("首都", "BJS", "PEK"),
    ("beijing", "BJS", None), ("chengdu", "CTU", None), ("shouer", "SEL", None),
    ("成都天府国际机场", "CTU", "TFU"), ("首尔仁川国际机场", "SEL", "ICN"),
])
def test_city_and_airport_scope(name, city, airport):
    location = resolve_location(name)
    assert location.city_code == city
    assert location.airport_code == airport


def test_unknown_city_is_not_silently_guessed():
    with pytest.raises(ValueError, match="无法识别"):
        resolve_location("不存在的城市")
    with pytest.raises(ValueError, match="未知"):
        resolve_location("QQQ")
    with pytest.raises(ValueError):
        resolve_location("B")
    with pytest.raises(ValueError, match="多个城市"):
        resolve_location("yichun")


def test_dates_are_required_real_future_dates(day):
    with pytest.raises(ValidationError):
        RouteQuery(origin="首尔", destination="成都", dates=["2026-02-30"])
    with pytest.raises(ValidationError):
        RouteQuery(origin="首尔", destination="成都", dates=[today_china() - timedelta(days=1)])
    with pytest.raises(ValidationError):
        RouteQuery(origin="首尔", destination="成都", dates=[])


def test_batch_expansion_deduplicates_without_merging_airport_scopes(day):
    queries = [RouteQuery(origin=o, destination="北京", dates=[day, day])
               for o in ["成都", "成都", "天府", "双流"]]
    legs = expand_routes(queries)
    assert len(legs) == 3
    assert {leg["origin"]["airport_code"] for leg in legs} == {None, "TFU", "CTU"}


def test_reject_roundtrip_and_malformed_filters(day):
    with pytest.raises(ValidationError):
        RouteQuery(origin="成都", destination="北京", dates=[day], return_date=day)
    for options in [{"departure_after": "25:00"}, {"max_price": float("nan")}, {"limit": 0}]:
        with pytest.raises(ValidationError):
            SearchRequest(routes=[RouteQuery(origin="成都", destination="北京", dates=[day])], **options)


def test_reject_intra_city_route(day):
    with pytest.raises(ValueError, match="同一城市"):
        expand_routes([RouteQuery(origin="天府", destination="双流", dates=[day])])
