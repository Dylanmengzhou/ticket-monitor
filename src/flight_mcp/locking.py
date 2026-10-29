from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable


class ProfileBusy(RuntimeError):
    pass


class ProfileCancelled(RuntimeError):
    pass


@contextmanager
def profile_lock(path: Path, timeout: float = 0, cancelled: Callable[[], bool] | None = None):
    file = path.open("a+b")
    file.write(b"0")
    file.flush()
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while True:
            if cancelled and cancelled():
                raise ProfileCancelled("查询已取消")
            try:
                file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise ProfileBusy("携程会话正在被查询或登录使用，请完成当前操作后重试")
                time.sleep(0.2)
        yield
    finally:
        if acquired:
            file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        file.close()
