"""Live check: close one MCP connection, then retrieve the job from another."""
import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from flight_mcp.models import today_china


async def run(args):
    connection = StdioServerParameters(command=sys.executable,
        args=["-m", "flight_mcp", "--data-dir", str(args.data_dir.resolve()), "serve"])
    query = {"routes": [{"origin": "成都天府", "destination": "北京首都", "dates": [args.date]}],
             "direct_only": True, "exclude_stopovers": True,
             "departure_after": "18:00", "departure_before": "23:59", "max_price": 1000, "limit": 3}
    async with Client(connection) as first:
        started = await first.call_tool("search_flights", query)
        assert not started.is_error, started.content
        identifier = started.structured_content["job_id"]
        print("Submitted and disconnecting:", identifier, flush=True)
    async with Client(connection) as second:
        for attempt in range(80):
            response = await second.call_tool("get_search_result", {"job_id": identifier})
            assert not response.is_error, response.content
            result = response.structured_content
            print("After reconnect:", result["status"], flush=True)
            if result["status"] in {"completed", "partial", "failed", "cancelled"}:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
                assert result["status"] == "completed", result
                flights = result["legs"][0]["flights"]
                assert flights, result
                for flight in flights:
                    assert flight["nonstop"] and flight["direct"]
                    assert flight["departure"]["airport_code"] == "TFU"
                    assert flight["arrival"]["airport_code"] == "PEK"
                    departure_clock = flight["departure"]["local_datetime"][11:16]
                    assert "18:00" <= departure_clock <= "23:59"
                    assert flight["price"]["amount"] <= 1000
                print("Verified live filters and reconnect:", len(flights), "flights", flush=True)
                return
            await asyncio.sleep(3)
        raise TimeoutError("Reconnect test exceeded four minutes; inspect the existing job before retrying")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--date", default=str(today_china() + timedelta(days=30)))
    parser.add_argument("--output", type=Path, default=Path("local-results/reconnect-live.json"))
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
