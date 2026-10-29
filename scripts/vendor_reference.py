"""Import five flight providers and their dependency tree; no reference web app."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "ticket-monitoring"
DEST = ROOT / "src/flight_mcp/vendor"
FILES = [
    "crawlers/base.py", "crawlers/challenge.py", "crawlers/ctrip.py",
    "core/models.py", "core/citydata.py", "core/flightnorm.py", "core/intl.py",
    "core/ctrip_dict_gen.py", "core/qunar_dict_gen.py",
    "crawlers/qunar.py", "crawlers/fliggy.py", "crawlers/fliggy_h5.py",
    "crawlers/tongcheng.py", "crawlers/tuniu.py",
    "core/channel_links.py", "core/login_slots.py",
]


def main() -> None:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REFERENCE, text=True).strip()
    manifest = {"repository": "https://github.com/dengmeiluan/ticket-monitoring",
                "revision": revision, "license": "Apache-2.0", "files": {}}
    for relative in FILES:
        source = (REFERENCE / relative).read_bytes()
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source.decode("utf8").replace(
            "from core.", "from flight_mcp.vendor.core.").replace(
            "from crawlers", "from flight_mcp.vendor.crawlers")
        changes = ["Added provenance header; namespaced absolute core imports."]
        if relative == "crawlers/fliggy.py":
            content = content.replace("import json\n", "import json\nimport os\n", 1)
            changes.append("Import os for the H5 session fallback.")
        if relative == "crawlers/fliggy_h5.py":
            content = content.replace(
                '                    "src": "fliggy_h5", "price_basis": "adult_total",',
                '                    "src": "fliggy_h5", "price_basis": "adult_total" if total or after else "base_fare",\n'
                '                    "code": fno, "price": price, "name": airline,\n'
                '                    "depDate": dep[:10], "arrDate": arr[:10],\n'
                '                    "depTime": dep[11:16], "arrTime": arr[11:16],\n'
                '                    "depAirportCode": seg.get("depAirportCode"),\n'
                '                    "arrAirportCode": seg.get("arrAirportCode"),\n'
                '                    "cabin": seg.get("cabinName") or fi.get("cabinName"),\n'
                '                    "transfer": item.get("itemType") == "FLIGHT_TRANSFER" or len(infos) > 1 or len(segs) > 1,\n'
                '                    "intl": int(is_intl_route(from_city, to_city)),')
            changes.append("Retain H5 itinerary fields and the actual adult-total/base-fare price basis for MCP normalization.")
        if relative == "crawlers/ctrip.py":
            content = content.replace(
                '                from .qunar import QunarCrawler as _Q\n'
                '                trans = _Q.CITY_NAME.get(tc, tc) or "中转"',
                '                # Flight MCP: use this provider\'s complete city dictionary;\n'
                '                # parsing a transfer row must not import another crawler.\n'
                '                trans = self.CITY_NAME.get(tc, tc) or "中转"')
            changes.append("Resolve transfer city names with Ctrip's own city table instead of importing QunarCrawler.")
        target.write_text(
            "# Vendored from ticket-monitoring; see ../NOTICE and source.json.\n"
            + content, encoding="utf8")
        manifest["files"][relative] = {
            "original_sha256": hashlib.sha256(source).hexdigest(),
            "changes": changes,
        }
    for package in (DEST, DEST / "core", DEST / "crawlers"):
        (package / "__init__.py").write_text(
            '"""Selected Apache-2.0 reference components; no UI or scheduler."""\n',
            encoding="utf8")
    (DEST / "LICENSE").write_bytes((REFERENCE / "LICENSE").read_bytes())
    (ROOT / "LICENSE").write_bytes((REFERENCE / "LICENSE").read_bytes())
    (DEST / "source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    (DEST / "NOTICE").write_text(
        "Selected code from dengmeiluan/ticket-monitoring, licensed under Apache-2.0.\n"
        "Source: https://github.com/dengmeiluan/ticket-monitoring\n"
        f"Revision: {revision}\n"
        "Modifications: provenance headers, package-qualified imports, local Ctrip city lookup, Fliggy os import and retained H5 itinerary/price-basis fields.\n"
        "Original copyright and attribution notices are retained in the sources.\n"
        "Included: Ctrip, Qunar, Fliggy (PC/H5), Tongcheng, Tuniu and their data/login/link dependencies.\n",
        encoding="utf8")
    print(f"Imported {len(FILES)} reference files at {revision[:12]}")


if __name__ == "__main__":
    main()
