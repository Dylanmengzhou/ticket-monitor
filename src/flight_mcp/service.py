from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .locations import expand_routes
from .locking import profile_lock
from .models import SearchRequest
from .processes import spawn_worker
from .settings import Settings
from .channels import LOGIN_URLS, NAMES, PROVIDERS, metadata_key, validate_provider
from .results import comparisons
from .store import Store, now, pid_alive

TERMINAL = {"completed", "partial", "failed", "cancelled"}
LOGIN_ACTIVE = {"opening", "active", "saving"}


def valid_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("任务编号无效，请使用工具返回的 job_id 或 login_id") from None


class FlightService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.processes = {}

    def search_flights(self, request: SearchRequest) -> dict:
        legs = [{**leg, "provider": provider} for leg in expand_routes(request.routes) for provider in request.providers]
        identifier = str(uuid.uuid4())
        self.store.create_job(identifier, request.model_dump(mode="json"), legs)
        try:
            process = spawn_worker(self.settings, "run-job", identifier)
            self.processes[identifier] = process
            self.store.job_pid(identifier, process.pid)
        except OSError:
            result = self.store.job(identifier)["result"]
            result.update(status="failed", message="查询进程未能启动，请检查运行环境")
            self.store.save_result(identifier, result)
            return result
        return {"job_id": identifier, "status": "queued", "providers": request.providers,
                "legs": legs, "poll_after_seconds": 3,
                "next_step": "使用 get_search_result 查询进度；仅将 status=ok 的 flights 当作本次航班报价"}

    def get_search_result(self, job_id: str) -> dict:
        job_id = valid_id(job_id)
        job = self.store.job(job_id)
        if job is None:
            return {"job_id": job_id, "status": "not_found", "message": "未找到该查询任务"}
        process = self.processes.get(job_id)
        if process is not None and process.poll() is not None:
            self.processes.pop(job_id, None)
        result = job["result"]
        if result["status"] not in TERMINAL:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(job["created_at"])).total_seconds()
            if age > 5 and not pid_alive(job["worker_pid"]):
                result.update(status="failed", message="采集任务进程已停止，请重新发起查询")
                for leg in result["legs"]:
                    if leg["status"] in {"queued", "running", "waiting_for_session"}:
                        leg.update(status="worker_stopped", message="采集任务进程已停止", flights=[])
                self.store.save_result(job_id, result)
            else:
                result["poll_after_seconds"] = 3
        result["progress"] = {"finished": sum(l["status"] not in {"queued", "running", "waiting_for_session"}
                                                for l in result["legs"]), "total": len(result["legs"])}
        result["retrieved_at"] = now()
        result["comparisons"] = comparisons(result["legs"])
        result["notification"] = self.store.notification(job_id) or {"status": "not_sent"}
        return result

    def cancel_search(self, job_id: str) -> dict:
        job_id = valid_id(job_id)
        job = self.store.job(job_id)
        if job is None:
            return {"job_id": job_id, "status": "not_found"}
        if job["status"] in TERMINAL:
            return {"job_id": job_id, "status": job["status"], "message": "任务已结束"}
        self.store.cancel(job_id)
        return {"job_id": job_id, "status": "cancelling", "message": "已请求取消，正在回收采集进程；用 get_search_result 确认结束"}

    def list_providers(self) -> dict:
        return {"providers": [{"id": p, "name": NAMES[p], "session": self.get_session_status(p),
            "login_slots": [p, "fliggy_h5"] if p == "fliggy" else [p]} for p in PROVIDERS],
            "note": "各平台可能要求登录或验证，国际渠道可能仅返回日历价；只有 status=ok 的 flights 才是具体航班。"}

    def get_session_status(self, provider: str = "ctrip") -> dict:
        if provider == "all":
            return {"providers": [self.get_session_status(p) for p in LOGIN_URLS]}
        provider = validate_provider(provider, login=True)
        state_path = self.settings.profile_for(provider) / "session-state.json"
        key = lambda name: metadata_key(name, provider)
        saved_at = self.store.metadata(key("session_saved_at"))
        saved = state_path.is_file()
        if saved and not saved_at:
            try:
                stamp = json.loads(state_path.read_text(encoding="utf8")).get("saved_at")
                if stamp:
                    saved_at = datetime.fromtimestamp(float(stamp), timezone.utc).isoformat(timespec="seconds")
            except (OSError, ValueError, TypeError):
                pass
        evidence = self.store.metadata(key("session_evidence"), {})
        current = {scope: item for scope, item in evidence.items()
                   if not saved_at or item.get("at", "") >= saved_at}
        latest = max(current.values(), key=lambda item: item.get("at", ""), default={})
        status = latest.get("state") or ("saved_unverified" if saved else "not_logged_in")
        login = self.store.login(provider=provider)
        if login and login["status"] in LOGIN_ACTIVE and login.get("worker_pid") and not pid_alive(login["worker_pid"]):
            self.store.update_login(login["id"], status="failed", error="登录进程已停止，请重新打开")
            login = self.store.login(login["id"])
        login_view = ({"login_id": login["id"], "status": login["status"], "error": login["error"] or None}
                      if login else None)
        return {"provider": provider, "status": status, "session_saved": saved,
                "saved_at": saved_at, "verified_scopes": current, "login": login_view,
                "message": {"verified": "该平台最近已返回真实航班；各航线是否可查仍以本次采集为准",
                            "saved_unverified": "会话已保存在本机，尚需一次真实查询验证",
                            "not_logged_in": f"尚未保存会话，可用 start_login(provider='{provider}') 打开官方登录页",
                            "login_required": "该平台拒绝当前会话，请重新登录",
                            "verification_required": "该平台要求人工验证，请打开官方登录页面完成验证"}.get(status, status)}

    def start_login(self, provider: str = "ctrip") -> dict:
        provider = validate_provider(provider, login=True)
        existing = self.store.login(provider=provider)
        if existing and existing["status"] in LOGIN_ACTIVE and pid_alive(existing.get("worker_pid")):
            return {"login_id": existing["id"], "status": existing["status"],
                    "message": "登录窗口已在打开，请在官方页面完成登录后调用 finish_login"}
        identifier = str(uuid.uuid4())
        self.store.create_login(identifier, provider)
        try:
            process = spawn_worker(self.settings, "login", identifier)
            self.processes[identifier] = process
            self.store.update_login(identifier, worker_pid=process.pid)
        except OSError:
            self.store.update_login(identifier, status="failed", error="登录进程未能启动")
            return {"login_id": identifier, "status": "failed", "message": "登录进程未能启动"}
        return {"login_id": identifier, "provider": provider, "status": "opening", "url": LOGIN_URLS[provider],
                "message": "正在本机打开平台官方登录窗口，请用户自行完成登录或验证；收到用户完成确认后再调用 finish_login。不要在 MCP 参数中传密码或验证码。",
                "timeout_seconds": self.settings.login_timeout}

    def finish_login(self, login_id: str, cancel: bool = False) -> dict:
        login_id = valid_id(login_id)
        record = self.store.login(login_id)
        if not record:
            return {"login_id": login_id, "status": "not_found"}
        if record["status"] in LOGIN_ACTIVE:
            self.store.update_login(login_id, action="cancel" if cancel else "complete")
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                record = self.store.login(login_id)
                if record["status"] not in LOGIN_ACTIVE:
                    break
                time.sleep(0.2)
        return {"login_id": login_id, "provider": record.get("provider", "ctrip"), "status": record["status"], "error": record["error"] or None,
                "message": "已保存会话，下一次查询会验证平台是否接受登录态" if record["status"] == "saved"
                else "登录操作尚未完成，请通过 get_session_status 查看状态" if record["status"] in LOGIN_ACTIVE
                else record["error"] or record["status"]}

    def import_session(self, source: Path, provider: str = "ctrip") -> dict:
        provider = validate_provider(provider, login=True)
        target = self.settings.profile_for(provider)
        source = source.expanduser().resolve()
        if not source.is_dir() or not (source / "session-state.json").is_file():
            raise ValueError("来源需为包含 session-state.json 的平台资料目录")
        if (source / "SingletonLock").is_symlink() or (source / "SingletonLock").exists():
            raise ValueError("原携程浏览器仍在运行，请先关闭原登录/采集窗口后再迁移")
        with profile_lock(self.settings.lock_for(provider)):
            if target.exists():
                raise ValueError("本应用已有该平台资料，未覆盖；需要重登时请调用 start_login")
            shutil.copytree(source, target,
                ignore=shutil.ignore_patterns("Singleton*", "Cache", "Code Cache", "GPUCache",
                                             "DawnCache", "ShaderCache", "Crashpad", "*.lock"))
            for folder, directories, files in os.walk(target):
                Path(folder).chmod(0o700)
                for name in files:
                    Path(folder, name).chmod(0o600)
            self.store.set_metadata(metadata_key("session_saved_at", provider), now())
            self.store.set_metadata(metadata_key("session_evidence", provider), {})
            self.store.set_metadata(metadata_key("session_imported_at", provider), now())
        return {"provider": provider, "status": "saved_unverified", "message": "已复制现有会话到本应用的独立目录，原资料保持原样；需要真实查询验证"}
