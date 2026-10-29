from datetime import timedelta

import pytest

from flight_mcp.locations import expand_routes
from flight_mcp.models import RouteQuery, SearchRequest, today_china
from flight_mcp.settings import Settings


@pytest.fixture(autouse=True)
def isolate_notification_credentials(monkeypatch):
    monkeypatch.delenv("FLIGHT_MCP_BARK_KEY", raising=False)
    monkeypatch.delenv("FLIGHT_MCP_BARK_SERVER", raising=False)


@pytest.fixture
def day():
    return (today_china() + timedelta(days=30)).isoformat()


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "state")


@pytest.fixture
def request_data(day):
    return SearchRequest(routes=[RouteQuery(origin="首尔", destination="成都", dates=[day])], providers=["ctrip"])


@pytest.fixture
def leg(request_data):
    return expand_routes(request_data.routes)[0]


@pytest.fixture
def offer(day):
    return {"code": "OZ323", "name": "TEST ONLY", "price": 1561, "cabin": "经济舱",
            "depTime": "20:15", "arrTime": "23:20", "depDate": day, "arrDate": day,
            "depAirportCode": "ICN", "arrAirportCode": "TFU", "depAirport": "仁川", "arrAirport": "天府",
            "intl": 1, "tax": 641, "tz_dep": 540, "tz_arr": 480}
