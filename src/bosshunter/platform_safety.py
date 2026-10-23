"""Shared page-access budgets and persistent safety locks for platform workflows."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable

from bosshunter.db import (
    add_platform_access,
    count_platform_access_today,
    get_active_platform_safety_lock,
    set_platform_safety_lock,
)

class PlatformSafetyStop(RuntimeError):
    """Raised before another platform page is opened when a safety guard stops work."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class PlatformAccessGuard:
    conn: sqlite3.Connection
    config: dict
    stage: str
    platform: str = "boss"

    def ensure_unlocked(self) -> None:
        lock = get_active_platform_safety_lock(self.conn)
        if lock:
            raise PlatformSafetyStop("persistent_risk_lock")

    def reserve(self, action: str, *, daily_limit: int | None = None) -> None:
        """Reserve one page-open attempt before navigation."""
        self.ensure_unlocked()
        safety_cfg = self.config.get("safety", {})
        global_limit = _positive_int(safety_cfg.get("daily_platform_page_limit", 1100), 1100)
        if count_platform_access_today(self.conn, platform=self.platform) >= global_limit:
            raise PlatformSafetyStop("daily_platform_page_limit")
        if daily_limit is not None:
            stage_count = count_platform_access_today(
                self.conn,
                platform=self.platform,
                stage=self.stage,
                action=action,
            )
            if stage_count >= max(int(daily_limit), 1):
                raise PlatformSafetyStop(f"daily_{action}_limit")
        add_platform_access(self.conn, self.stage, action, platform=self.platform)

    def lock(self, reason: str, *, minutes: int | None = None) -> None:
        safety_cfg = self.config.get("safety", {})
        lock_minutes = (
            _positive_int(minutes, 1)
            if minutes is not None
            else _positive_int(safety_cfg.get("risk_lock_minutes", 10), 10)
        )
        set_platform_safety_lock(self.conn, reason, minutes=lock_minutes)


@dataclass
class TransientPlatformAccessGuard:
    """Open a short-lived DB connection for workflows without a cycle-long connection."""

    config: dict
    stage: str
    db_factory: Callable[[], sqlite3.Connection]
    platform: str = "boss"

    def ensure_unlocked(self) -> None:
        conn = self.db_factory()
        try:
            PlatformAccessGuard(conn, self.config, self.stage, self.platform).ensure_unlocked()
        finally:
            conn.close()

    def reserve(self, action: str, *, daily_limit: int | None = None) -> None:
        conn = self.db_factory()
        try:
            PlatformAccessGuard(conn, self.config, self.stage, self.platform).reserve(
                action,
                daily_limit=daily_limit,
            )
        finally:
            conn.close()


def _positive_int(value: object, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


# Daily search-page budget for the platforms that have no guard of their own (BOSS keeps its own settings). A page is a list
# page opened for one (city, keyword, page); the numbers leave room for a full daily loop and only stop a runaway scan.
DEFAULT_SEARCH_PAGE_LIMITS = {"51job": 300, "liepin": 300, "zhilian": 300, "zhilian_campus": 200}


def platform_search_page_limit(config: dict, platform: str) -> int | None:
    """Pages one platform may open per day for collection; None = no budget (BOSS, unknown platforms, or 0 in the settings)."""
    if platform not in DEFAULT_SEARCH_PAGE_LIMITS:
        return None
    collection = config.get("collection") if isinstance(config.get("collection"), dict) else {}
    limits = collection.get("platform_limits") if isinstance(collection.get("platform_limits"), dict) else {}
    own = limits.get(platform) if isinstance(limits.get(platform), dict) else {}
    value = own.get("daily_search_page_limit", DEFAULT_SEARCH_PAGE_LIMITS[platform])
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = DEFAULT_SEARCH_PAGE_LIMITS[platform]
    return value if value > 0 else None


class PlatformStop:
    """A stop signal for one platform: set when the platform's own budget is used up or when the whole task is stopped."""

    def __init__(self, parent=None) -> None:
        self.parent = parent
        self._local = threading.Event()

    def set(self) -> None:
        self._local.set()

    def is_set(self) -> bool:
        return self._local.is_set() or bool(self.parent is not None and self.parent.is_set())

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            step = 0.2 if deadline is None else min(0.2, deadline - time.monotonic())
            if step <= 0:
                return False
            self._local.wait(step)
        return True


class PlatformPageBudget:
    """Counts the list pages one platform opens today and says when the daily budget is used up."""

    def __init__(self, conn: sqlite3.Connection, platform: str, limit: int | None, stage: str = "collection") -> None:
        self.conn = conn
        self.platform = platform
        self.limit = limit
        self.stage = stage
        self.tripped = False
        self._seen: set[tuple] = set()

    def used(self) -> int:
        return count_platform_access_today(self.conn, platform=self.platform, stage=self.stage, action="search_page")

    def exhausted(self) -> bool:
        return self.limit is not None and self.used() >= self.limit

    def note_page(self, key: tuple) -> bool:
        """Record a page the first time it is seen. False when the budget is used up (the page is not recorded)."""
        if key in self._seen:
            return not self.tripped
        if self.limit is not None and self.used() >= self.limit:
            self.tripped = True
            return False
        add_platform_access(self.conn, self.stage, "search_page", platform=self.platform)
        self._seen.add(key)
        return True
