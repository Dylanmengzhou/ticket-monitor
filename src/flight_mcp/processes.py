from __future__ import annotations

import os
import signal
import subprocess
import sys

from .settings import Settings


def worker_command(settings: Settings, mode: str, identifier: str, *extra: str) -> list[str]:
    return [sys.executable, "-m", "flight_mcp.worker", "--data-dir", str(settings.data_dir),
            mode, identifier, *extra]


def spawn_worker(settings: Settings, mode: str, identifier: str) -> subprocess.Popen:
    log_path = settings.log_dir / f"{mode}-{identifier}.log"
    with log_path.open("ab") as log:
        log_path.chmod(0o600)
        kwargs = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return subprocess.Popen(worker_command(settings, mode, identifier),
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            cwd=str(settings.data_dir), **kwargs)


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=3)
