"""Opt-in live search: disconnect MCP, receive one Bark POST on a local test server.

This checks the delivery plumbing, not APNs/iPhone delivery. Never contacts a real Bark device.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from flight_mcp.models import today_china


async def verify(cli: Path, source: Path | None) -> dict:
    event, received = threading.Event(), []
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"code":200,"message":"success"}')
            event.set()
        def log_message(self, *args):
            pass
    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="flight-bark-e2e-") as folder:
            if source:
                target = Path(folder, "profiles", "ctrip")
                target.mkdir(parents=True, mode=0o700)
                shutil.copy2(source / "session-state.json", target / "session-state.json")
                (target / "session-state.json").chmod(0o600)
            params = StdioServerParameters(command=shutil.which("node"),
                args=[str(cli.resolve()), "--data-dir", folder, "serve"], env=dict(os.environ))
            async with Client(params) as client:
                config = await client.call_tool("configure_bark", {"device_key": "LOCAL_TEST_ONLY",
                    "server_url": f"http://127.0.0.1:{receiver.server_port}"})
                assert not config.is_error and config.structured_content["notify_on_completion"] is True
                response = await client.call_tool("search_flights", {"providers": ["ctrip"], "cabin": "any",
                    "routes": [{"origin": "成都", "destination": "北京",
                                "dates": [(today_china()+timedelta(days=30)).isoformat()]}], "limit": 1})
                assert not response.is_error, response
                job_id = response.structured_content["job_id"]
                print(json.dumps({"job_id": job_id, "next": "disconnecting MCP; waiting for worker push"}), flush=True)
            assert await asyncio.to_thread(event.wait, 180), "Background worker did not POST a notification"
            async with Client(params) as client:
                for _ in range(10):
                    response = await client.call_tool("get_search_result", {"job_id": job_id})
                    result = response.structured_content
                    if result["notification"]["status"] == "sent":
                        break
                    await asyncio.sleep(0.2)
                assert result["notification"]["status"] == "sent", result
                repeated = await client.call_tool("push_search_result", {"job_id": job_id})
                assert repeated.structured_content["duplicate_prevented"] is True
            assert len(received) == 1
            assert "成都→北京" in received[0]["body"]
            assert received[0]["device_key"] == "LOCAL_TEST_ONLY"
            return {"job_id": job_id, "npm_mcp": "passed", "disconnect_then_auto_notify": "passed",
                "duplicate_prevention": "passed", "bark_posts": len(received), "target": "local_test_receiver",
                "iphone_delivery": "not_tested", "query_status": result["status"],
                "provider_status": result["legs"][0]["status"],
                "message_preview": received[0]["body"]}
    finally:
        receiver.shutdown()
        receiver.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cli", type=Path)
    parser.add_argument("--session-source", type=Path, help="Optionally copy only this user's saved Ctrip session-state.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(verify(args.cli, args.session_source))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(result, ensure_ascii=False))
