from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .settings import Settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, settings: Settings):
        settings.prepare()
        self.path = settings.db_path
        with self.connection() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, status TEXT NOT NULL, request TEXT NOT NULL,
              result TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              worker_pid INTEGER, cancel_requested INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS logins (
              id TEXT PRIMARY KEY, status TEXT NOT NULL, action TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
              worker_pid INTEGER
            );
            CREATE TABLE IF NOT EXISTS notifications (
              job_id TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL,
              detail TEXT NOT NULL DEFAULT ''
            );
            """)
            columns = {r[1] for r in c.execute("PRAGMA table_info(logins)")}
            if "provider" not in columns:
                c.execute("ALTER TABLE logins ADD COLUMN provider TEXT NOT NULL DEFAULT 'ctrip'")
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def create_job(self, job_id: str, request: dict, legs: list[dict]) -> None:
        result = {"job_id": job_id, "status": "queued", "created_at": now(),
                  "provider": request.get("providers", ["ctrip"])[0] if len(request.get("providers", ["ctrip"])) == 1 else "multiple",
                  "providers": request.get("providers", ["ctrip"]), "legs": [{**leg, "status": "queued", "flights": []}
                                                for leg in legs]}
        with self.connection() as c:
            c.execute("INSERT INTO jobs(id,status,request,result,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                      (job_id, "queued", json.dumps(request), json.dumps(result), now(), now()))

    def job(self, job_id: str) -> dict | None:
        with self.connection() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "request": json.loads(row["request"]), "result": json.loads(row["result"])}

    def save_result(self, job_id: str, result: dict) -> None:
        result["updated_at"] = now()
        with self.connection() as c:
            c.execute("UPDATE jobs SET status=?,result=?,updated_at=? WHERE id=?",
                      (result["status"], json.dumps(result), now(), job_id))

    def job_pid(self, job_id: str, pid: int) -> None:
        with self.connection() as c:
            c.execute("UPDATE jobs SET worker_pid=? WHERE id=?", (pid, job_id))

    def cancel(self, job_id: str) -> None:
        with self.connection() as c:
            c.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))

    def metadata(self, key: str, default=None):
        with self.connection() as c:
            row = c.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_metadata(self, key: str, value) -> None:
        with self.connection() as c:
            c.execute("INSERT INTO metadata(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))

    def create_login(self, login_id: str, provider: str = "ctrip") -> None:
        with self.connection() as c:
            c.execute("INSERT INTO logins(id,status,created_at,updated_at,provider) VALUES (?,?,?,?,?)",
                      (login_id, "opening", now(), now(), provider))

    def login(self, login_id: str | None = None, provider: str = "ctrip") -> dict | None:
        with self.connection() as c:
            row = (c.execute("SELECT * FROM logins WHERE id=?", (login_id,)).fetchone()
                   if login_id else c.execute("SELECT * FROM logins WHERE provider=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (provider,)).fetchone())
        return dict(row) if row else None

    def notification(self, job_id: str) -> dict | None:
        with self.connection() as c:
            row = c.execute("SELECT * FROM notifications WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def claim_notification(self, job_id: str) -> bool:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT status FROM notifications WHERE job_id=?", (job_id,)).fetchone()
            if row and row["status"] in {"sending", "sent", "unknown"}:
                return False
            c.execute("INSERT INTO notifications(job_id,status,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,detail=''",
                (job_id, "sending", now()))
        return True

    def finish_notification(self, job_id: str, status: str, detail: str = "") -> dict:
        with self.connection() as c:
            c.execute("UPDATE notifications SET status=?,updated_at=?,detail=? WHERE job_id=?",
                      (status, now(), detail, job_id))
        return self.notification(job_id)

    def update_login(self, login_id: str, **fields) -> None:
        allowed = {"status", "action", "error", "worker_pid"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("Invalid login update")
        fields["updated_at"] = now()
        columns = ",".join(k + "=?" for k in fields)
        with self.connection() as c:
            c.execute(f"UPDATE logins SET {columns} WHERE id=?", (*fields.values(), login_id))


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError):
        return False


def _windows_pid_alive(pid: int) -> bool:
    # os.kill(pid, 0) calls TerminateProcess on Windows: never use it to probe!
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return ctypes.get_last_error() == 5  # Access denied still means it exists.
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
    finally:
        kernel.CloseHandle(handle)
