from __future__ import annotations

import asyncio
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__
from .models import RouteQuery, SearchRequest
from .service import FlightService
from .settings import Settings
from .channels import PROVIDERS, Provider, LoginProvider
from . import bark


def create_server(settings: Settings) -> MCPServer:
    service = FlightService(settings)
    server = MCPServer("flight-agent", version=__version__,
        instructions=("通过携程、去哪儿、飞猪、同程、途牛查询真实单程航班，默认查五个平台，也可指定 providers。将用户日期转换为含年份的 YYYY-MM-DD，"
            "城市名表示同城所有机场，具体机场名或机场码表示严格机场限制。"
            "先调用 search_flights，再用 get_search_result 查询任务。"
            "只将各航线 status=ok 的 flights 作为真实报价，保留币种、税费口径、"
            "当地时区及 observed_at；不同航线独立返回，不能拼成已验证联程。"
            "登录必须由用户在官方窗口自行完成，用户明确表示完成后才能调用 finish_login。"
            "价格保留税前/含税/未知口径，不能把税前价当作含税总价。"
            "不接受密码/验证码，不自动购票。Bark 配置后默认在查价完成时自动推送一次，可用 notify=false 禁用本次通知。"
            "配置工具不发送通知；只有用户请求测试时才调用 test_bark。Bark Key 仅保存在本机，禁止在回复中复述。"))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    query = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

    @server.tool(annotations=query, structured_output=True)
    async def search_flights(
        routes: list[RouteQuery],
        providers: list[Provider] | None = None,
        notify: bool = True,
        direct_only: bool = True,
        exclude_stopovers: bool = True,
        cabin: Literal["any", "economy", "business", "first"] = "economy",
        max_price: float | None = None,
        departure_after: str | None = None,
        departure_before: str | None = None,
        sort_by: Literal["price", "departure", "duration"] = "price",
        limit: int = 10,
    ) -> dict[str, Any]:
        """查询五平台真实航班，支持多条航线与多个日期，立即返回 job_id。

        routes 中每项为 origin、destination、dates（YYYY-MM-DD 数组）。
        如 [{"origin":"首尔","destination":"成都","dates":["2026-11-27"]}]。
        默认经济舱、仅直飞、不经停。价格上限以人民币计；起飞窗口是出发当地 HH:MM，支持跨零点。
        每次最多 16 个航线日期组合，每条结果最多 limit 个航班；不支持往返总价查询。
        providers 默认五个平台，可指定 ctrip/qunar/fliggy/tongcheng/tuniu；各平台独立汇报状态。
        notify 默认 true：配置 Bark 后，全部平台结束时推送一条摘要，部分平台失败也会通知。
        """
        request = SearchRequest(routes=routes, providers=providers if providers is not None else list(PROVIDERS), notify=notify, direct_only=direct_only,
            exclude_stopovers=exclude_stopovers, cabin=cabin, max_price=max_price,
            departure_after=departure_after, departure_before=departure_before,
            sort_by=sort_by, limit=limit)
        return await asyncio.to_thread(service.search_flights, request)

    @server.tool(annotations=read, structured_output=True)
    async def get_search_result(job_id: str) -> dict[str, Any]:
        """读取查询进度和结果。逐航线返回成功/需登录/需验证/无匹配/无数据，保留已成功的部分结果。

        queued/running 表示仍在查询，按 poll_after_seconds 再查；completed/partial/failed/cancelled 为结束。
        报价中的起降时刻为机场当地时间；observed_at 是本次采集时间，旧任务不会冒充新报价。
        """
        return await asyncio.to_thread(service.get_search_result, job_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False), structured_output=True)
    async def cancel_search(job_id: str) -> dict[str, Any]:
        """取消尚未完成的查询，保留已完成航线的结果并回收正在运行的采集进程。"""
        return await asyncio.to_thread(service.cancel_search, job_id)

    @server.tool(annotations=read, structured_output=True)
    async def get_session_status(provider: LoginProvider | Literal["all"] = "ctrip") -> dict[str, Any]:
        """读取指定平台会话与登录窗口状态；provider=all 查看全部，不返回 Cookie、账号或凭据。

        saved_unverified 仅表示本机已保存会话；verified 表示最近实际取得过航班。
        login_required/verification_required 需要用户在官方页面处理；国内、国际验证证据分别列出。
        """
        return await asyncio.to_thread(service.get_session_status, provider)

    @server.tool(annotations=query, structured_output=True)
    async def start_login(provider: LoginProvider = "ctrip") -> dict[str, Any]:
        """在用户本机打开指定平台官方登录窗口；飞猪淘宝 H5 用 fliggy_h5。登录/验证码由用户自行完成；返回 login_id。

        收到用户的完成确认后，调用 finish_login 保存；get_session_status 可查看窗口状态。
        此工具需要本机桌面环境，不会处理密码、短信验证码或自动破解验证。
        """
        return await asyncio.to_thread(service.start_login, provider)

    @server.tool(annotations=query, structured_output=True)
    async def finish_login(login_id: str, cancel: bool = False) -> dict[str, Any]:
        """用户确认在官方窗口完成登录后，保存该平台会话并关闭窗口；cancel=true 为取消。

        只有 status=saved 才表示保存完成，是否可查价仍由下一次真实查询验证。
        """
        return await asyncio.to_thread(service.finish_login, login_id, cancel)

    @server.tool(annotations=read, structured_output=True)
    async def list_providers() -> dict[str, Any]:
        """列出五个平台及各自本机会话状态、登录槽位；飞猪 PC 和淘宝 H5 分开登录。"""
        return await asyncio.to_thread(service.list_providers)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False), structured_output=True)
    async def configure_bark(push_url: str | None = None, device_key: str | None = None,
        server_url: str | None = None, enabled: bool = True, notify_on_completion: bool = True,
        group: str = "机票查价", sound: str | None = None,
        level: Literal["active", "passive", "timeSensitive"] = "active") -> dict[str, Any]:
        """保存 iOS Bark 配置，支持官方或自建服务。本工具不发送推送。

        可传 Bark App 复制的 https://api.day.app/设备Key（不带正文），或 device_key + server_url。
        默认每次查价全部结束后通知一次，notify_on_completion=false 改为仅手动。
        不把设备 Key 或完整推送地址复述给用户；也可让用户通过本机 CLI bark-configure 输入。
        """
        return await asyncio.to_thread(bark.configure, settings, push_url=push_url, device_key=device_key,
            server_url=server_url, enabled=enabled, notify_on_completion=notify_on_completion,
            group=group, sound=sound, level=level)

    @server.tool(annotations=read, structured_output=True)
    async def get_bark_status() -> dict[str, Any]:
        """查看 Bark 是否配置、自动推送开关和通知参数，不返回设备 Key。"""
        return await asyncio.to_thread(bark.status, settings)

    @server.tool(annotations=query, structured_output=True)
    async def test_bark() -> dict[str, Any]:
        """向已配置的 Bark 设备发送一条测试通知，仅在用户明确请求测试推送时调用。"""
        return await asyncio.to_thread(bark.send_test, settings)

    @server.tool(annotations=query, structured_output=True)
    async def push_search_result(job_id: str) -> dict[str, Any]:
        """把已结束的查价任务摘要推送到 Bark；相同 job_id 已发送/发送中/结果未知时不重复发送。"""
        from .service import valid_id
        return await asyncio.to_thread(bark.notify_job, settings, valid_id(job_id))

    return server
