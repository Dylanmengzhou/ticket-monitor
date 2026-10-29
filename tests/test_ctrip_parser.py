import json
import logging
import sys

from flight_mcp.vendor.crawlers.ctrip import CtripCrawler


def test_domestic_mixed_pool_does_not_require_other_provider(day):
    def segment(code, departure, arrival, origin="CTU", destination="SIA"):
        return {"basinfo": {"flgno": code, "aircode": code[:2]},
                "dateinfo": {"ddate": day + " " + departure + ":00", "adate": day + " " + arrival + ":00"},
                "dportinfo": {"city": origin}, "aportinfo": {"city": destination}}
    policy = [{"quantity": 5, "tprice": 1000, "classinfor": [{"cgrd": 0}]}]
    sample = {"fltitem": [
        {"mutilstn": [segment("CA1000", "08:00", "10:30", destination="BJS")], "policyinfo": policy},
        {"mutilstn": [segment("CA2000", "09:00", "10:30"),
                      segment("CA3000", "12:00", "14:00", "SIA", "BJS")], "policyinfo": policy},
    ]}
    crawler = CtripCrawler({}, logging.getLogger("parser_test"))
    flights = crawler._extract_ctrip_flights(json.dumps(sample))
    assert len(flights) == 2
    assert not flights[0].get("transCity")
    assert flights[1]["transCity"] == "西安"
    assert not any(name.endswith(".crawlers.qunar") for name in sys.modules)
