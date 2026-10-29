"""Independent job supervisor and bounded browser workers; no HTTP/UI server."""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .locking import ProfileBusy, ProfileCancelled, profile_lock
from .processes import terminate_process, worker_command
from .settings import Settings
from .store import Store, now
from .channels import NAMES, metadata_key

LOG = logging.getLogger("flight_mcp.worker")
GOOD_LEG_STATUSES = {"ok", "no_matching_flights"}


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def fetch(settings: Settings, job_id: str, index: int) -> None:
    from .provider import fetch_leg
    store = Store(settings)
    job = store.job(job_id)
    leg = job["result"]["legs"][index]
    result_path = settings.work_dir / job_id / f"leg-{index}.json"
    try:
        result = fetch_leg(settings, leg, job["request"])
    except Exception as error:
        LOG.exception("Provider fetch failed")
        missing = "Executable doesn't exist" in str(error)
        result = {**leg, "status": "browser_missing" if missing else "provider_error",
                  "message": "请先运行 flight-mcp setup-browser 安装浏览器" if missing else
                  f"{NAMES[leg.get('provider', 'ctrip')]}采集未成功（{type(error).__name__}），可查看本机任务日志后重试",
                  "flights": [], "observed_at": now()}
    _write_json(result_path, result)


def run_job(settings: Settings, job_id: str) -> None:
    store = Store(settings)
    store.job_pid(job_id, os.getpid())
    job = store.job(job_id)
    result = job["result"]
    folder = settings.work_dir / job_id
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    result["status"] = "running"
    store.save_result(job_id, result)
    try:
        for index, leg in enumerate(result["legs"]):
            if store.job(job_id)["cancel_requested"]:
                break
            leg["status"] = "waiting_for_session"
            store.save_result(job_id, result)
            try:
                with profile_lock(settings.lock_for(leg.get("provider", "ctrip")), settings.lock_timeout,
                                  cancelled=lambda: bool(store.job(job_id)["cancel_requested"])):
                    if store.job(job_id)["cancel_requested"]:
                        break
                    leg["status"] = "running"
                    store.save_result(job_id, result)
                    kwargs = {"start_new_session": True} if os.name != "nt" else {
                        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                    process = subprocess.Popen(worker_command(settings, "fetch", job_id, str(index)),
                        stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr, **kwargs)
                    deadline = time.monotonic() + settings.fetch_timeout
                    reason = None
                    try:
                        while process.poll() is None:
                            if store.job(job_id)["cancel_requested"]:
                                reason = "cancelled"
                                break
                            if time.monotonic() > deadline:
                                reason = "timeout"
                                break
                            time.sleep(0.3)
                    finally:
                        if process.poll() is None:
                            terminate_process(process)
                    path = folder / f"leg-{index}.json"
                    if reason:
                        leg = {**leg, "status": reason, "message": "查询已取消" if reason == "cancelled"
                               else f"平台查询超过 {settings.fetch_timeout} 秒，浏览器进程已回收，可重试",
                               "flights": []}
                    elif process.returncode == 0 and path.exists():
                        leg = json.loads(path.read_text(encoding="utf8"))
                    else:
                        leg = {**leg, "status": "provider_error", "message": "采集进程异常退出，可重试", "flights": []}
            except ProfileBusy as error:
                leg = {**leg, "status": "session_busy", "message": str(error), "flights": []}
            except ProfileCancelled:
                leg = {**leg, "status": "cancelled", "message": "查询已取消", "flights": []}
            result["legs"][index] = leg
            _record_session_evidence(store, leg)
            store.save_result(job_id, result)
        if store.job(job_id)["cancel_requested"]:
            result["status"] = "cancelled"
            for leg in result["legs"]:
                if leg["status"] in {"queued", "waiting_for_session", "running"}:
                    leg.update(status="cancelled", message="查询已取消", flights=[])
        else:
            good = sum(leg["status"] in GOOD_LEG_STATUSES for leg in result["legs"])
            result["status"] = "completed" if good == len(result["legs"]) else "partial" if good else "failed"
        result["finished_at"] = now()
        store.save_result(job_id, result)
    except BaseException:
        LOG.exception("Job supervisor failed")
        result["status"] = "failed"
        result["message"] = "查询任务异常退出，可重新发起查询"
        store.save_result(job_id, result)
        raise
    if job["request"].get("notify", True) and result["status"] != "cancelled":
        try:
            from .bark import notify_job
            notify_job(settings, job_id, automatic=True)
        except Exception:
            LOG.warning("Bark 通知未完成；查价结果已保存，可检查通知配置后手动推送。")


def _record_session_evidence(store: Store, leg: dict) -> None:
    scope = leg.get("scope", "unknown")
    key = metadata_key("session_evidence", leg.get("provider", "ctrip"))
    evidence = store.metadata(key, {})
    if leg.get("status") in {"ok", "no_matching_flights"}:
        evidence[scope] = {"state": "verified", "at": now()}
    elif leg.get("status") == "login_required":
        evidence[scope] = {"state": "login_required", "at": now()}
    elif leg.get("status") == "verification_required":
        evidence[scope] = {"state": "verification_required", "at": now()}
    else:
        return
    store.set_metadata(key, evidence)


class LoginCancelled(Exception):
    pass


def login(settings: Settings, login_id: str) -> None:
    from .provider import crawler_for
    store = Store(settings)
    provider = (store.login(login_id) or {}).get("provider", "ctrip")
    store.update_login(login_id, worker_pid=os.getpid())
    try:
        with profile_lock(settings.lock_for(provider), timeout=0):
            crawler = crawler_for(settings) if provider == "ctrip" else crawler_for(settings, provider)
            crawler.use_mobile = bool(getattr(crawler, "login_mobile", False))
            with crawler.browser(headless=False, window_front=True, save_session="login") as context:
                page = crawler.new_page(context)
                store.update_login(login_id, status="opening")
                page.goto(crawler.login_url(), wait_until="domcontentloaded", timeout=45000)
                store.update_login(login_id, status="active")
                deadline = time.monotonic() + settings.login_timeout
                while True:
                    record = store.login(login_id)
                    if record["action"] == "cancel":
                        raise LoginCancelled()
                    if record["action"] == "complete":
                        store.update_login(login_id, status="saving")
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("登录窗口已超时，请重新打开")
                    if page.is_closed():
                        raise RuntimeError("登录窗口已关闭，请重新打开并通过 finish_login 确认完成")
                    page.wait_for_timeout(400)
            store.set_metadata(metadata_key("session_saved_at", provider), now())
            store.update_login(login_id, status="saved")
    except LoginCancelled:
        store.update_login(login_id, status="cancelled")
    except Exception as error:
        LOG.exception("Login failed")
        message = str(error) if isinstance(error, (ProfileBusy, TimeoutError, RuntimeError)) else "登录窗口未能打开，请确认浏览器已安装且本机桌面可用"
        store.update_login(login_id, status="failed", error=message[:300])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("mode", choices=["run-job", "fetch", "login"])
    parser.add_argument("identifier")
    parser.add_argument("index", nargs="?", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.load(args.data_dir)
    if args.mode == "run-job":
        run_job(settings, args.identifier)
    elif args.mode == "fetch":
        fetch(settings, args.identifier, args.index)
    else:
        login(settings, args.identifier)


if __name__ == "__main__":
    main()
