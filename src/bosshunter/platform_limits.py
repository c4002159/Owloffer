"""Platforms that told us they will take no more contacts today (BOSS: "您已达到沟通上限").

The mark lasts until the end of the local day. Collection asks for confirmation before it runs on a marked platform, and
unattended runs skip it.
"""

import sqlite3
from datetime import date

PLATFORM_NAMES = {"boss": "BOSS 直聘", "51job": "前程无忧", "liepin": "猎聘", "zhilian": "智联招聘"}


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS platform_daily_limits ("
        "platform TEXT NOT NULL, day TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', "
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (platform, day))"
    )


def mark_limit_reached(conn: sqlite3.Connection, platform: str, reason: str = "") -> None:
    _ensure(conn)
    conn.execute(
        "INSERT OR REPLACE INTO platform_daily_limits (platform, day, reason) VALUES (?, ?, ?)",
        (platform, date.today().isoformat(), reason[:300]),
    )
    conn.commit()


def limits_today(conn: sqlite3.Connection) -> dict[str, str]:
    """``{platform: reason}`` for the platforms that reached their limit today."""
    _ensure(conn)
    rows = conn.execute(
        "SELECT platform, reason FROM platform_daily_limits WHERE day = ?", (date.today().isoformat(),)
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def clear_limit(conn: sqlite3.Connection, platform: str) -> None:
    _ensure(conn)
    conn.execute("DELETE FROM platform_daily_limits WHERE platform = ? AND day = ?", (platform, date.today().isoformat()))
    conn.commit()
