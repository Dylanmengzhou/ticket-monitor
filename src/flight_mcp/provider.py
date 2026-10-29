from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .settings import Settings
from .channels import NAMES, validate_provider
from .store import now
from .vendor.core.flightnorm import AIRLINE_CN, normalize
from .vendor.core.intl import INTL_TZ, is_intl_route
from .vendor.crawlers.ctrip import CtripCrawler

LOG = logging.getLogger("flight_mcp.providers")


def crawler_for(settings: Settings, provider: str = "ctrip"):
    validate_provider(provider, login=True)
    from .vendor.crawlers.qunar import QunarCrawler
    from .vendor.crawlers.fliggy import FliggyCrawler, FliggyH5Login
    from .vendor.crawlers.tongcheng import TongchengCrawler
    from .vendor.crawlers.tuniu import TuniuCrawler
    cls = {"ctrip": CtripCrawler, "qunar": QunarCrawler, "fliggy": FliggyCrawler,
           "fliggy_h5": FliggyH5Login, "tongcheng": TongchengCrawler, "tuniu": TuniuCrawler}[provider]
    return cls({"user_data_dir": str(settings.profile_root), "headless": True,
                         "timeout_seconds": 40, "delay_min": 1, "delay_max": 2,
                         "debug": False}, LOG)


def route_link(leg: dict) -> str:
    source = leg.get("provider", "ctrip")
    origin, destination = leg["origin"], leg["destination"]
    if source != "ctrip":
        from .vendor.core.channel_links import channel_links
        links = channel_links(origin["city_code"], destination["city_code"], leg["date"])
        cells = links["cells"]
        scope = "intl" if is_intl_route(origin["city_code"], destination["city_code"]) else "domestic"
        surface = "h5" if source in {"qunar", "tongcheng"} else "pc"
        return next(c["url"] for c in cells if c["plat"] == source and c["surface"] == surface and c["scope"] == scope)
    fc = origin.get("airport_code") or origin["city_code"]
    tc = destination.get("airport_code") or destination["city_code"]
    return "https://flights.ctrip.com/online/list/oneway-" + fc.lower() + "-" + tc.lower() + "?" + urlencode({"depdate": leg["date"]})


def _clock_in_window(value: str, lower: str | None, upper: str | None) -> bool:
    if lower and upper and lower > upper:
        return value >= lower or value <= upper
    return (not lower or value >= lower) and (not upper or value <= upper)


def _cabin_kind(value: str) -> str:
    if "头等" in value:
        return "first"
    if "公务" in value or "商务" in value:
        return "business"
    return "economy" if "经济" in value else "unknown"


def price_basis(source: str, row: dict) -> tuple[bool | None, str]:
    if source == "ctrip":
        return True, "ctrip_list_total"
    if row.get("price_basis") == "adult_total":
        return True, f"{source}_adult_total"
    if row.get("price_basis") == "base_fare":
        return False, f"{source}_base_fare"
    if source == "fliggy" and not row.get("intl"):
        return False, "fliggy_base_fare"
    if source == "tongcheng" or (source in {"qunar", "tuniu"} and row.get("intl")):
        return True, f"{source}_list_total"
    return None, f"{source}_reported_price"


def _local_time(day: str, clock: str, city: str, airport: str | None) -> dict:
    zone = INTL_TZ.get(airport or "") or INTL_TZ.get(city)
    if zone is None:
        from .vendor.core.intl import MAINLAND_CODES
        zone = "Asia/Shanghai" if city in MAINLAND_CODES else None
    text = day + "T" + clock + ":00"
    if zone:
        text = datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(zone)).isoformat()
    return {"local_datetime": text, "timezone": zone}


def normalize_offers(raw: list[dict], leg: dict, request: dict,
                     observed_at: str, source_path: str) -> tuple[list[dict], dict]:
    stats = {"raw_offers": len(raw), "calendar_rows": 0, "invalid_rows": 0,
             "wrong_date_rows": 0, "filtered_rows": 0}
    origin, destination = leg["origin"], leg["destination"]
    source = leg.get("provider", "ctrip")
    best: dict[tuple, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            stats["invalid_rows"] += 1
            continue
        f = dict(item)
        if not f.get("code") or not f.get("depTime") or not f.get("arrTime"):
            stats["calendar_rows"] += 1
            continue
        if f.get("depDate") != leg["date"]:
            stats["wrong_date_rows"] += 1
            continue
        try:
            normalize(f, leg["date"])
            price = float(f.get("price"))
            if not math.isfinite(price) or price <= 0 or f.get("_dep_mismatch"):
                raise ValueError("invalid price/date")
            depart = datetime.fromisoformat(f["depDate"] + "T" + f["depTime"])
            arrive = datetime.fromisoformat(f["arrDate"] + "T" + f["arrTime"])
        except (TypeError, ValueError, KeyError):
            stats["invalid_rows"] += 1
            continue
        transfer = bool(f.get("transfer")) or bool(f.get("transCity")) or "/" in str(f["code"])
        stopover = bool(f.get("stopover")) or bool(f.get("stopCity"))
        cabin = _cabin_kind(str(f.get("cabin") or ""))
        requested_airports = ((origin.get("airport_code"), f.get("depAirportCode")),
                              (destination.get("airport_code"), f.get("arrAirportCode")))
        if ((request["direct_only"] and transfer)
                or (request["exclude_stopovers"] and stopover)
                or (request["cabin"] != "any" and cabin != request["cabin"])
                or (request.get("max_price") is not None and price > request["max_price"])
                or not _clock_in_window(f["depTime"], request.get("departure_after"), request.get("departure_before"))
                or any(wanted and wanted != got for wanted, got in requested_airports)):
            stats["filtered_rows"] += 1
            continue
        dep = _local_time(depart.date().isoformat(), f["depTime"], origin["city_code"], f.get("depAirportCode"))
        arr = _local_time(arrive.date().isoformat(), f["arrTime"], destination["city_code"], f.get("arrAirportCode"))
        duration = f.get("durM")
        if dep["timezone"] and arr["timezone"]:
            delta = datetime.fromisoformat(arr["local_datetime"]) - datetime.fromisoformat(dep["local_datetime"])
            duration = int(delta.total_seconds() / 60)
        if duration is not None and duration <= 0:
            stats["invalid_rows"] += 1
            continue
        airline = str(f.get("airlineCode") or str(f["code"])[:2])
        tax = f.get("tax")
        try:
            tax = float(tax) if tax is not None else None
            if tax is not None and (not math.isfinite(tax) or tax < 0):
                tax = None
        except (TypeError, ValueError):
            tax = None
        offer = {
            "flight_number": f["code"], "airline_code": airline,
            "airline_name": AIRLINE_CN.get(airline), "cabin": f.get("cabin"),
            "departure": {"city": origin["city_name"], "airport_code": f.get("depAirportCode"),
                          "airport_name": f.get("depAirport"), **dep},
            "arrival": {"city": destination["city_name"], "airport_code": f.get("arrAirportCode"),
                        "airport_name": f.get("arrAirport"), **arr},
            "duration_minutes": duration, "direct": not transfer, "nonstop": not transfer and not stopover,
            "transfer_city": f.get("transCity") or None,
            "price": {"amount": price, "currency": "CNY", "tax_included": price_basis(source, f)[0],
                      "tax_amount": tax, "basis": price_basis(source, f)[1], "is_booking_quote": False},
            "baggage": f.get("baggage") or None, "seats_left": f.get("leftTickets"),
            "marketing_or_operating": {"operating_flight": f.get("shareCarrier") or None},
            "restrictions": {k: f[k] for k in ("agePolicy", "riskPolicy", "ticketRisk", "lurePrice") if f.get(k)},
            "source": source, "source_name": NAMES[source], "source_path": source_path, "observed_at": observed_at,
            "booking_url": route_link(leg),
        }
        key = (f["code"], f.get("depAirportCode"), f.get("arrAirportCode"),
               dep["local_datetime"], arr["local_datetime"], cabin, transfer, stopover)
        if key not in best or price < best[key]["price"]["amount"]:
            best[key] = offer
    offers = list(best.values())
    sort = request["sort_by"]
    offers.sort(key=lambda o: (
        o["departure"]["local_datetime"] if sort == "departure" else
        (o["duration_minutes"] if o["duration_minutes"] is not None else math.inf) if sort == "duration" else o["price"]["amount"],
        o["price"]["amount"], o["departure"]["local_datetime"], o["flight_number"]))
    stats["matching_flights"] = len(offers)
    return offers[:request["limit"]], stats


def fetch_leg(settings: Settings, leg: dict, request: dict) -> dict:
    source = leg.get("provider", "ctrip")
    crawler = crawler_for(settings) if source == "ctrip" else crawler_for(settings, source)
    # Query a city-wide pool, then enforce explicit airport constraints ourselves.
    rows = crawler.fetch(leg["origin"]["city_code"], leg["destination"]["city_code"], [leg["date"]])
    paths = [{k: p.get(k) for k in ("path", "status", "rows", "date", "detail")}
             for p in getattr(crawler, "paths", [])]
    raw = []
    for row in rows:
        try:
            items = json.loads(row.extra or "[]")
            if isinstance(items, list):
                raw.extend(items)
            elif isinstance(items, dict):
                raw.append(items)
        except (TypeError, ValueError):
            continue
    observed = now()
    path = next((p["path"] for p in reversed(paths) if p.get("status") == "ok"), "unknown")
    offers, stats = normalize_offers(raw, leg, request, observed, path)
    statuses = {p.get("status") for p in paths}
    if offers:
        status, message = "ok", "查询完成"
    elif "blocked" in statuses:
        status, message = "verification_required", f"{NAMES[source]}要求人工验证；请通过 start_login(provider='{source}') 打开官方页面完成验证后重试"
    elif "login" in statuses:
        status, message = "login_required", f"{NAMES[source]}未接受当前会话；请重新登录后重试"
    elif stats["calendar_rows"] and stats["calendar_rows"] == len(raw):
        status, message = "calendar_only", "本次仅取得日历低价，没有可核实的具体航班；未将其当作航班报价"
    elif stats["filtered_rows"]:
        status, message = "no_matching_flights", "本次有航班数据，但没有航班满足指定条件"
    elif "error" in statuses or "timeout" in statuses or "parse_error" in statuses:
        status, message = "provider_error", f"{NAMES[source]}采集失败；可查看本机任务日志后重试"
    elif "unsupported" in statuses:
        status, message = "unsupported", "该平台暂不支持这类航线查询"
    else:
        status, message = "no_data", "本次未获得可核实的航班报价，不代表该航线没有航班"
    return {**leg, "status": status, "message": message, "observed_at": observed,
            "source": source, "provider": source, "source_name": NAMES[source], "query_url": route_link(leg), "flights": offers,
            "counts": stats, "paths": paths,
            "scope": "international" if is_intl_route(leg["origin"]["city_code"], leg["destination"]["city_code"]) else "domestic"}
