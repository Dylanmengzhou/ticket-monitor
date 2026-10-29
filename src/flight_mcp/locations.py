from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .vendor.core.citydata import CITY, CITY_NPY, CODE_NAME
from .vendor.core.flightnorm import AIRPORT_NAME_CN
from .vendor.core.intl import INTL_AIRPORT_CITY, INTL_CITY_NAME, INTL_CITY_NPY

AIRPORT_CITY = {**INTL_AIRPORT_CITY, "CTU": "CTU", "TFU": "CTU", "SHA": "SHA",
                "PVG": "SHA", "PEK": "BJS", "PKX": "BJS", "ICN": "SEL", "GMP": "SEL"}
AIRPORT_NAMES = {**AIRPORT_NAME_CN, "ICN": "仁川", "GMP": "金浦",
                 "NRT": "成田", "HND": "羽田", "KIX": "关西", "ITM": "伊丹"}
CITY_NAMES = {**INTL_CITY_NAME, **CODE_NAME}
CITY_CODES = set(CITY.values()) | set(INTL_CITY_NAME) | set(AIRPORT_CITY.values())
ALIASES = {name: code for name, code in CITY.items()}
ALIASES.update({name: code for code, name in INTL_CITY_NAME.items() if code not in AIRPORT_CITY})
ALIASES.update({"东京": "TYO", "大阪": "OSA", "伦敦": "LON", "纽约": "NYC",
                "首尔": "SEL", "北京": "BJS", "成都": "CTU", "上海": "SHA"})
PINYIN: dict[str, set[str]] = {}
for _name, _pinyin in CITY_NPY.items():
    PINYIN.setdefault(str(_pinyin).lower(), set()).add(_name)
for _name, _pinyin in INTL_CITY_NPY.items():
    PINYIN.setdefault(str(_pinyin).lower(), set()).add(_name)


@dataclass(frozen=True)
class Location:
    requested: str
    city_code: str
    city_name: str
    airport_code: str | None = None
    airport_name: str | None = None

    def public(self) -> dict:
        return asdict(self)


def resolve_location(value: str) -> Location:
    requested = value.strip()
    key = re.sub(r"\s+", "", requested).replace("国际机场", "机场")
    upper = key.upper()
    if re.fullmatch(r"[A-Z]{3}", upper):
        if upper in AIRPORT_CITY or upper in AIRPORT_NAMES:
            city = AIRPORT_CITY.get(upper, upper)
            return Location(requested, city, CITY_NAMES.get(city, city), upper,
                            AIRPORT_NAMES.get(upper, INTL_CITY_NAME.get(upper, upper)))
        if upper in CITY_CODES:
            return Location(requested, upper, CITY_NAMES.get(upper, upper))
        raise ValueError(f"未知城市/机场代码 {upper}，请改用中文城市名或有效 IATA 三字码")
    if key.lower() in PINYIN:
        names = PINYIN[key.lower()]
        if len(names) != 1:
            raise ValueError("拼音对应多个城市，请使用中文名或三字码：" + "、".join(sorted(names)))
        key = next(iter(names))
    if key in ALIASES:
        code = ALIASES[key]
        return Location(requested, code, CITY_NAMES.get(code, key))
    matches = []
    for airport, name in AIRPORT_NAMES.items():
        city = AIRPORT_CITY.get(airport, airport)
        city_name = CITY_NAMES.get(city, "")
        candidates = {name, name + "机场", city_name + name, city_name + name + "机场",
                      INTL_CITY_NAME.get(airport, "")}
        if key in candidates:
            matches.append(Location(requested, city, city_name or city, airport, name))
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError("机场名称不唯一，请指定三字码：" + "、".join(m.airport_code for m in matches))
    suggestions = [name for name in ALIASES if key in name or name in key][:6]
    suffix = "，可能是：" + "、".join(suggestions) if suggestions else ""
    raise ValueError(f"无法识别城市/机场「{requested}」，请用明确中文名或 IATA 三字码{suffix}")


def expand_routes(routes) -> list[dict]:
    legs, seen = [], set()
    for route in routes:
        origin, destination = resolve_location(route.origin), resolve_location(route.destination)
        if origin.city_code == destination.city_code:
            raise ValueError("出发地与目的地属于同一城市，请确认航线")
        for day in route.dates:
            key = (origin.city_code, origin.airport_code, destination.city_code,
                   destination.airport_code, str(day))
            if key not in seen:
                seen.add(key)
                legs.append({"origin": origin.public(), "destination": destination.public(),
                             "date": str(day)})
    return legs
