"""Only one server per data folder, and only one on the port.

Two servers on the same data have separate task lists in memory, separate daily loops and separate schedulers, but share the
database, the browser and the platform accounts: one shows "no task" while the other collects, both may start a loop, and a
request goes to either of them. On Windows a listening socket with ``SO_REUSEADDR`` can even be bound twice, so the second server
does not fail to start, it just joins the first. This module refuses the second one before it does anything.

Two checks, because each catches what the other can miss:
* the port: something already answers on it (a server started by anybody, from any folder);
* a lock on ``data/server.lock`` held for the life of the process (two starts at the very same moment, when nothing listens yet).
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

LOCK_OFFSET = 1_000_000
_held = []                                   # the lock file stays open (and locked) as long as the process lives


class AlreadyRunning(RuntimeError):
    """Another server is already running; ``detail`` says which."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


def _lock(handle) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(LOCK_OFFSET)                       # far behind the pid text, which others must still be able to read
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire(lock_path: Path, host: str, port: int) -> None:
    """Refuse to go on when another server runs. Call once, before anything is started."""
    if port_in_use(host, port):
        raise AlreadyRunning(f"端口 {port} 上已经有一个程序在运行（多半是已经启动的 OwlOffer / BossHunter 服务器）")
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    if not _lock(handle):
        try:
            handle.seek(0)
            owner = handle.read().strip()
        except OSError:
            owner = ""
        handle.close()
        raise AlreadyRunning("已经有另一个服务器在使用这份数据" + (f"（进程 {owner}）" if owner else ""))
    handle.seek(0)
    handle.truncate(0)
    handle.write(str(os.getpid()))
    handle.flush()
    _held.append(handle)


def release() -> None:
    """Give the lock back (tests; a normal exit lets the system do it)."""
    while _held:
        handle = _held.pop()
        try:
            handle.close()
        except OSError:
            pass
