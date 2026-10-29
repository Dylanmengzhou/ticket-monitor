"""Exercise the real MCP stdio protocol; default route pair comes from this chat."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from flight_mcp.models import today_china


async def run(args) -> int:
    parameters = StdioServerParameters(command=sys.executable,
        args=["-m", "flight_mcp", "--data-dir", str(args.data_dir.resolve()), "serve"])
    async with Client(parameters) as client:
        tools = await client.list_tools()
        print("MCP tools:", ", ".join(tool.name for tool in tools.tools), flush=True)
        session = await client.call_tool("get_session_status")
        print("Session:", json.dumps(session.structured_content, ensure_ascii=False), flush=True)
        if args.status_only:
            return 0
        query = {"routes": [
            {"origin": "首尔", "destination": "成都", "dates": [args.date]},
            {"origin": "成都", "destination": "北京", "dates": [args.date]}],
            "direct_only": True, "exclude_stopovers": True, "limit": 5}
        started = await client.call_tool("search_flights", query)
        if started.is_error:
            raise RuntimeError(str(started.content))
        ticket = started.structured_content
        print("Search:", json.dumps(ticket, ensure_ascii=False), flush=True)
        identifier = ticket["job_id"]
        if args.detach:
            print("Detached job_id:", identifier, flush=True)
            return 0
        while True:
            await asyncio.sleep(3)
            response = await client.call_tool("get_search_result", {"job_id": identifier})
            if response.is_error:
                raise RuntimeError(str(response.content))
            result = response.structured_content
            print("Progress:", result["status"],
                  [(leg["origin"]["city_name"] + "→" + leg["destination"]["city_name"], leg["status"])
                   for leg in result["legs"]], flush=True)
            if result["status"] in {"completed", "partial", "failed", "cancelled"}:
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                return 0 if result["status"] == "completed" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="通过真实 MCP 协议试查首尔→成都、成都→北京")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--date", default=str(today_china() + timedelta(days=30)))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--detach", action="store_true", help="创建查询后关闭 MCP 客户端，用于验证后台任务持续执行")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
