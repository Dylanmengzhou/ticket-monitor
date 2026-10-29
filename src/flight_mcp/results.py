from __future__ import annotations

from collections import defaultdict


def comparisons(legs: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for leg in legs:
        key = (leg["origin"]["requested"], leg["destination"]["requested"], leg["date"])
        groups[key].append(leg)
    result = []
    for (origin, destination, day), items in groups.items():
        offers = [f for leg in items if leg["status"] == "ok" for f in leg.get("flights", [])
                  if isinstance(f.get("price", {}).get("amount"), (float, int))]
        eligible = [f for f in offers if f["price"]["tax_included"] is True]
        pick = lambda rows: min(rows, key=lambda f: f["price"]["amount"], default=None)
        result.append({"origin": origin, "destination": destination, "date": day,
            "providers": [{"provider": leg.get("provider", "ctrip"), "status": leg["status"],
                           "offers_returned": len(leg.get("flights", []))} for leg in items],
            "cheapest_reported": pick(offers), "cheapest_tax_included": pick(eligible),
            "price_note": "各平台标价保留原税费口径；税前价或税费未知不能当作含税总价比较。"})
    return result
