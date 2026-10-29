"""Private Bark configuration and one delivery per completed search job."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

from .channels import NAMES
from .results import comparisons
from .settings import Settings
from .store import Store, now


def _server(value: str) -> str:
    try:
        parsed = urlsplit(value.strip().rstrip("/"))
        parsed.port
    except (ValueError, AttributeError):
        raise ValueError("Bark 服务地址格式无效。") from None
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Bark 服务地址需为 http/https，不含账号、查询参数或片段。")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _read(settings: Settings) -> dict:
    if settings.bark_path.exists():
        try:
            value = json.loads(settings.bark_path.read_text(encoding="utf8"))
            if not isinstance(value, dict):
                raise ValueError()
        except (ValueError, OSError):
            raise ValueError("Bark 本机配置无法读取，请重新配置。") from None
    else:
        value = {"enabled": True, "notify_on_completion": True, "group": "机票查价", "level": "active"}
    key = os.environ.get("FLIGHT_MCP_BARK_KEY")
    if key:
        value["device_key"] = key
    value["server_url"] = _server(os.environ.get("FLIGHT_MCP_BARK_SERVER") or value.get("server_url", "https://api.day.app"))
    return value


def status(settings: Settings) -> dict:
    try:
        config = _read(settings)
    except ValueError:
        return {"channel": "bark", "configured": False, "enabled": False, "status": "configuration_error",
                "message": "Bark 配置无效，请检查本机配置/环境变量后重新配置。"}
    return {"channel": "bark", "configured": bool(config.get("device_key")),
        "enabled": bool(config.get("enabled")), "notify_on_completion": bool(config.get("notify_on_completion")),
        "server_host": urlsplit(config["server_url"]).hostname, "group": config.get("group"),
        "level": config.get("level", "active"), "sound": config.get("sound"),
        "credentials": "stored_locally" if config.get("device_key") else "missing"}


def configure(settings: Settings, *, push_url: str | None = None, device_key: str | None = None,
              server_url: str | None = None, enabled: bool = True, notify_on_completion: bool = True,
              group: str = "机票查价", sound: str | None = None, level: str = "active") -> dict:
    try:
        config = _read(settings)
    except ValueError:
        config = {"server_url": "https://api.day.app"}
    if push_url:
        parsed = urlsplit(push_url.strip().rstrip("/"))
        path, _, key = parsed.path.rpartition("/")
        if not key or not re.fullmatch(r"[A-Za-z0-9_-]{6,512}", unquote(key)):
            raise ValueError("请复制 Bark 的推送地址（以设备 Key 结尾，不带推送正文）。")
        device_key = unquote(key)
        server_url = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
    if device_key is not None:
        if not device_key.strip() or len(device_key) > 512 or re.search(r"[\s/]", device_key):
            raise ValueError("Bark 设备 Key 格式无效。")
        config["device_key"] = device_key
    if server_url:
        config["server_url"] = _server(server_url)
    if level not in {"active", "passive", "timeSensitive"}:
        raise ValueError("Bark level 支持 active/passive/timeSensitive。")
    if not group.strip() or len(group) > 100 or (sound and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", sound)):
        raise ValueError("Bark 分组或铃声格式无效。")
    config.update(enabled=enabled, notify_on_completion=notify_on_completion, group=group.strip(), sound=sound, level=level)
    if enabled and not config.get("device_key"):
        raise ValueError("请先提供 Bark 推送地址或设备 Key；也可在本机设置 FLIGHT_MCP_BARK_KEY。")
    settings.prepare()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=settings.data_dir, delete=False) as file:
            temporary = Path(file.name)
            temporary.chmod(0o600)
            json.dump(config, file, ensure_ascii=False)
        os.replace(temporary, settings.bark_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {**status(settings), "message": "Bark 配置已保存；配置操作没有发送通知。"}


def _send(config: dict, title: str, body: str, url: str | None = None) -> dict:
    if len(body) > 3200:
        body = body[:3150] + "\n（内容已截断，请让 Agent 读取完整查询结果）"
    payload = {"device_key": config["device_key"], "title": title[:100], "body": body,
               "group": config.get("group", "机票查价"), "level": config.get("level", "active")}
    if config.get("sound"):
        payload["sound"] = config["sound"]
    if url:
        payload["url"] = url
    try:
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            response = client.post(config["server_url"] + "/push", json=payload)
        if response.status_code != 200:
            return {"status": "failed", "message": f"Bark 服务返回 HTTP {response.status_code}，未确认送达。"}
        try:
            code = response.json().get("code")
        except (ValueError, AttributeError):
            code = None
        if code != 200:
            return {"status": "failed" if code is not None else "unknown", "message": "Bark 未返回成功确认。"}
        return {"status": "sent", "message": "Bark 已接受推送，iPhone 是否展示取决于系统通知设置。", "sent_at": now()}
    except httpx.ConnectError:
        return {"status": "failed", "message": "无法连接 Bark 服务，请检查网络和地址。"}
    except httpx.HTTPError:
        return {"status": "unknown", "message": "未能确认 Bark 是否接受推送，未自动重试以避免重复通知。"}


def send_test(settings: Settings) -> dict:
    config = _read(settings)
    if not config.get("device_key") or not config.get("enabled"):
        return {"status": "not_configured", "message": "请先配置并启用 Bark。"}
    return _send(config, "机票查价测试", "Flight Agent MCP 的 iOS Bark 推送测试。")


def notify_job(settings: Settings, job_id: str, *, automatic: bool = False) -> dict:
    store = Store(settings)
    job = store.job(job_id)
    if not job:
        return {"status": "not_found", "job_id": job_id}
    result = job["result"]
    if result["status"] not in {"completed", "partial", "failed"}:
        return {"status": "not_ready", "message": "查价尚未结束，或已取消。"}
    config = _read(settings)
    if not config.get("device_key"):
        return {"status": "not_configured", "message": "缺少 Bark 设备 Key，请先配置。"}
    if not config.get("enabled") or (automatic and not config.get("notify_on_completion")):
        return {"status": "disabled"}
    if not store.claim_notification(job_id):
        return {**store.notification(job_id), "duplicate_prevented": True}
    lines, link = [], None
    for group in comparisons(result["legs"]):
        lines.append(f"{group['date']} {group['origin']}→{group['destination']}")
        for leg in [x for x in result["legs"] if x["date"] == group["date"]
                    and x["origin"]["requested"] == group["origin"] and x["destination"]["requested"] == group["destination"]]:
            source = leg.get("provider", "ctrip")
            flights = leg.get("flights", []) if leg["status"] == "ok" else []
            if flights:
                best = min(flights, key=lambda f: f["price"]["amount"])
                tax = "含税" if best["price"]["tax_included"] is True else "税前" if best["price"]["tax_included"] is False else "税费待确认"
                lines.append(f"{NAMES[source]} ¥{best['price']['amount']:g} {tax} {best['flight_number']} "
                    f"{best['departure']['local_datetime'][11:16]}（出发地当地时间） 采集：{best.get('observed_at', '未知')}")
                link = link or best.get("booking_url")
            else:
                lines.append(f"{NAMES[source]}：{leg.get('message') or leg['status']}")
    lines.append("任务结束：" + str(result.get("finished_at") or result.get("updated_at") or now()))
    lines.append("列表报价未锁价，完整明细请让 Agent 读取查询结果。")
    sent = _send(config, "机票查价完成" if result["status"] != "failed" else "机票查价未取得报价", "\n".join(lines), link)
    store.finish_notification(job_id, sent["status"], sent["message"])
    return {**sent, "job_id": job_id}
