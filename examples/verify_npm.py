"""Verify a locally installed npm package through the real MCP stdio protocol."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def verify(cli: Path, check_browser: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="flight-npm-中文 空格-") as temporary:
        params = StdioServerParameters(command=shutil.which("node"),
            args=[str(cli.resolve()), "--data-dir", temporary, "serve"], cwd=temporary,
            env=dict(os.environ))
        async with Client(params) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert tools == {"search_flights", "get_search_result", "cancel_search",
                             "get_session_status", "start_login", "finish_login", "list_providers",
                             "configure_bark", "get_bark_status", "test_bark", "push_search_result"}, tools
            result = await client.call_tool("get_session_status")
            assert not result.is_error, result
            assert result.structured_content["status"] == "not_logged_in", result
            assert "cookies" not in json.dumps(result.structured_content).lower()
        if check_browser:
            # Browser launch uses the npm-managed Python, not the maintainer's environment.
            doctor = await asyncio.create_subprocess_exec(shutil.which("node"), str(cli.resolve()),
                "--data-dir", temporary, "doctor", stdout=asyncio.subprocess.PIPE)
            stdout, _ = await doctor.communicate()
            assert doctor.returncode == 0
            info = json.loads(stdout)
            executable = Path(info["python_executable"])
            check = await asyncio.create_subprocess_exec(str(executable), "-c",
                "from playwright.sync_api import sync_playwright; "
                "p=sync_playwright().start(); b=p.chromium.launch(headless=True); "
                "page=b.new_page(); page.goto('data:text/html,<title>flight-npm-ok</title>'); "
                "assert page.title()=='flight-npm-ok'; b.close(); p.stop()")
            assert await check.wait() == 0
        print(json.dumps({"npm_mcp": "ok", "tools": sorted(tools), "browser_launch": check_browser}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cli", type=Path)
    parser.add_argument("--check-browser", action="store_true")
    args = parser.parse_args()
    asyncio.run(verify(args.cli, args.check_browser))
