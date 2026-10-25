"""Postings that were read and turned away, so that they are not read as "new" again and again.

The freshness of a keyword's list (how many pages are worth reading) used to be judged by the share of postings that are not in the
database yet. A posting that the rules turned away (a company that was applied to recently, a banned company, a word in the title, a
city) is never saved, so it looked new on every visit: the list always seemed fresh, the head stayed at its maximum number of pages and
the deep window kept reading on. On BOSS that was about 515 pages a day for 249 new postings.

This remembers the turned-away postings for a day. Meeting one again counts as a posting that is already known, which is what it is.
A day, not for ever: the rules change (a cooldown ends, a setting is edited) and then the posting deserves another look.
"""

from __future__ import annotations

import sqlite3

REMEMBER_HOURS = 24
PRUNE_AFTER_DAYS = 3


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rejected_postings ("
        "platform TEXT NOT NULL, source_job_id TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', "
        "until TEXT NOT NULL, times INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (platform, source_job_id))"
    )


def remember(conn: sqlite3.Connection, platform: str, source_job_id: str, reason: str = "", hours: float = REMEMBER_HOURS) -> None:
    if not source_job_id or str(source_job_id).startswith("card:"):          # list-card stubs have no identity of their own
        return
    _ensure(conn)
    conn.execute(
        "INSERT INTO rejected_postings (platform, source_job_id, reason, until) VALUES (?, ?, ?, datetime('now', ?)) "
        "ON CONFLICT(platform, source_job_id) DO UPDATE SET reason = excluded.reason, until = excluded.until, times = times + 1",
        (platform, str(source_job_id), reason[:200], f"{int(hours * 60):+d} minutes"),
    )
    conn.commit()


def is_remembered(conn: sqlite3.Connection, platform: str, source_job_id: str) -> bool:
    _ensure(conn)
    return conn.execute(
        "SELECT 1 FROM rejected_postings WHERE platform = ? AND source_job_id = ? AND until > datetime('now')",
        (platform, str(source_job_id)),
    ).fetchone() is not None


def prune(conn: sqlite3.Connection) -> int:
    _ensure(conn)
    cursor = conn.execute("DELETE FROM rejected_postings WHERE until < datetime('now', ?)", (f"-{PRUNE_AFTER_DAYS} day",))
    conn.commit()
    return cursor.rowcount
