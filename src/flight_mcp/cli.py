from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from . import __version__
from .settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Flight Agent MCP — 无 UI 的本机五平台查价与 iOS Bark 通知")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", help="本机数据与登录态目录；也可设置 FLIGHT_MCP_DATA_DIR")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="以 MCP stdio 协议运行，无 HTTP 页面")
    commands.add_parser("setup-browser", help="安装 Chromium，首次运行一次即可")
    commands.add_parser("doctor", help="检查本机环境与会话状态")
    commands.add_parser("mcp-config", help="输出可粘贴到 MCP 客户端的 JSON 配置")
    importer = commands.add_parser("import-session", help="从原项目复制现有携程登录态")
    importer.add_argument("source", type=Path)
    importer.add_argument("--provider", default="ctrip", choices=["ctrip", "qunar", "fliggy", "fliggy_h5", "tongcheng", "tuniu"])
    commands.add_parser("bark-status", help="查看 Bark 配置，不输出设备 Key")
    commands.add_parser("bark-test", help="向已配置的 iPhone 发送一条测试通知")
    bark_config = commands.add_parser("bark-configure", help="配置 Bark，默认提示私密输入推送地址")
    bark_config.add_argument("--push-url")
    bark_config.add_argument("--device-key")
    bark_config.add_argument("--server-url")
    bark_config.add_argument("--disable", action="store_true")
    bark_config.add_argument("--manual-only", action="store_true")
    bark_config.add_argument("--group", default="机票查价")
    args = parser.parse_args()
    settings = Settings.load(args.data_dir)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command in (None, "serve"):
        from .server import create_server
        create_server(settings).run(transport="stdio")
    elif args.command == "setup-browser":
        raise SystemExit(subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"]))
    elif args.command == "mcp-config":
        print(json.dumps({"mcpServers": {"flight-agent": {
            "command": sys.executable,
            "args": ["-m", "flight_mcp", "--data-dir", str(settings.data_dir), "serve"]}}},
            ensure_ascii=False, indent=2))
    elif args.command in {"bark-status", "bark-test", "bark-configure"}:
        from . import bark
        if args.command == "bark-status":
            result = bark.status(settings)
        elif args.command == "bark-test":
            result = bark.send_test(settings)
        else:
            push_url = args.push_url
            if not args.disable and not push_url and not args.device_key and not bark.status(settings)["configured"]:
                import getpass
                push_url = getpass.getpass("请粘贴 Bark 推送地址（输入不显示）：")
            result = bark.configure(settings, push_url=push_url, device_key=args.device_key,
                server_url=args.server_url, enabled=not args.disable,
                notify_on_completion=not args.manual_only, group=args.group)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        from .service import FlightService
        service = FlightService(settings)
        if args.command == "import-session":
            result = service.import_session(args.source, args.provider)
        else:
            from playwright.sync_api import sync_playwright
            from .bark import status as bark_status
            with sync_playwright() as playwright:
                installed = Path(playwright.chromium.executable_path).is_file()
            result = {"version": __version__, "python": sys.version.split()[0], "python_executable": sys.executable,
                      "transport": "stdio", "ui": False, "chromium_installed": installed,
                      "data_dir": str(settings.data_dir), "session": service.get_session_status(),
                      "providers": service.list_providers(), "bark": bark_status(settings)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
