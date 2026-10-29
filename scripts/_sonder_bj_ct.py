"""Beijing -> Chengdu on a given date (default 2026-11-26) via real MCP stdio protocol."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

DATA = Path(__file__).resolve().parents[1] / "data"
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-11-26"
OUT = Path(__file__).resolve().parents[1] / "local-results" / f"sonder_bj_ct_{DATE}.json"


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "flight_mcp", "--data-dir", str(DATA.resolve()), "serve"],
    )
    async with Client(params) as client:
        tools = await client.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools), flush=True)
        query = {
            "routes": [{"origin": "北京", "destination": "成都", "dates": [DATE]}],
            "direct_only": True,
            "exclude_stopovers": True,
            "cabin": "economy",
            "sort_by": "price",
            "limit": 15,
        }
        started = await client.call_tool("search_flights", query)
        if started.is_error:
            print("START_ERROR:", started.content, flush=True)
            return 2
        job_id = started.structured_content["job_id"]
        print("job_id:", job_id, flush=True)
        for _ in range(120):
            await asyncio.sleep(5)
            res = await client.call_tool("get_search_result", {"job_id": job_id})
            if res.is_error:
                print("POLL_ERROR:", res.content, flush=True)
                return 3
            data = res.structured_content
            legs = [(f'{l["origin"]["city_name"]}→{l["destination"]["city_name"]}', l["status"])
                    for l in data.get("legs", [])]
            print("progress:", data.get("status"), legs, flush=True)
            if data.get("status") in {"completed", "partial", "failed", "cancelled"}:
                OUT.parent.mkdir(parents=True, exist_ok=True)
                OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
                print("SAVED:", OUT, flush=True)
                return 0
        print("TIMEOUT waiting for job", flush=True)
        return 4


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
