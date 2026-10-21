"""Platforms that are not collected from or delivered to for a while.

Two kinds of mark, both keyed by platform and local day:
* the platform told us it will take no more contacts today (BOSS: "您已达到沟通上限"): lasts until the end of the day;
* a cooldown (``minutes``): the platform stopped answering or asked for verification, so other platforms go first and
  this one is tried again later.

Collection asks for confirmation before it runs on a marked platform, and unattended runs skip it.
"""

import sqlite3
from datetime import date, datetime, timedelta

PLATFORM_NAMES = {"boss": "BOSS 直聘", "51job": "前程无忧", "liepin": "猎聘", "zhilian": "智联招聘", "zhilian_campus": "智联校招"}
DEFAULT_COOLDOWN_MINUTES = 30


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS platform_daily_limits ("
        "platform TEXT NOT NULL, day TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', "
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP, until TEXT, marked_at TEXT, PRIMARY KEY (platform, day))"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(platform_daily_limits)").fetchall()}
    if "until" not in columns:                       # a table made before cooldowns existed
        conn.execute("ALTER TABLE platform_daily_limits ADD COLUMN until TEXT")
    if "marked_at" not in columns:                   # local time of the mark, for the notice
        conn.execute("ALTER TABLE platform_daily_limits ADD COLUMN marked_at TEXT")


def cooldown_minutes(config: dict | None) -> int:
    raw = ((config or {}).get("automation") or {}).get("platform_cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)
    try:
        return min(max(int(raw), 1), 720)
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_MINUTES


def mark_limit_reached(conn: sqlite3.Connection, platform: str, reason: str = "", minutes: int | None = None) -> None:
    """Mark the platform for the rest of today, or for ``minutes`` only."""
    _ensure(conn)
    now = datetime.now()
    until = (now + timedelta(minutes=minutes)).isoformat(timespec="seconds") if minutes else None
    conn.execute(
        "INSERT OR REPLACE INTO platform_daily_limits (platform, day, reason, until, marked_at) VALUES (?, ?, ?, ?, ?)",
        (platform, date.today().isoformat(), reason[:300], until, now.isoformat(timespec="seconds")),
    )
    conn.commit()


def limits_today(conn: sqlite3.Connection) -> dict[str, str]:
    """``{platform: reason}`` for the platforms that are marked right now (cooldowns that ran out are left out)."""
    _ensure(conn)
    rows = conn.execute(
        "SELECT platform, reason, until FROM platform_daily_limits WHERE day = ?", (date.today().isoformat(),)
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    return {str(row[0]): str(row[1]) for row in rows if not row[2] or str(row[2]) > now}


def limit_details(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """``{platform: {reason, since, until}}`` for the platforms marked right now; ``since`` is when it happened (local
    time) and ``until`` when a cooldown ends ('' = the mark lasts until the end of the day)."""
    _ensure(conn)
    rows = conn.execute(
        "SELECT platform, reason, until, marked_at FROM platform_daily_limits WHERE day = ?", (date.today().isoformat(),)
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    return {
        str(row[0]): {"reason": str(row[1]), "until": str(row[2] or ""), "since": str(row[3] or "")}
        for row in rows if not row[2] or str(row[2]) > now
    }


def cooldown_remaining_seconds(conn: sqlite3.Connection, platform: str) -> float:
    """Seconds left of a platform's cooldown (0 when there is none, it ran out, or the mark lasts all day)."""
    _ensure(conn)
    row = conn.execute(
        "SELECT until FROM platform_daily_limits WHERE platform = ? AND day = ?", (platform, date.today().isoformat())
    ).fetchone()
    if not row or not row[0]:
        return 0.0
    return max((datetime.fromisoformat(str(row[0])) - datetime.now()).total_seconds(), 0.0)


def clear_limit(conn: sqlite3.Connection, platform: str) -> None:
    _ensure(conn)
    conn.execute("DELETE FROM platform_daily_limits WHERE platform = ? AND day = ?", (platform, date.today().isoformat()))
    conn.commit()
