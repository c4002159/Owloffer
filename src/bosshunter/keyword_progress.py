"""Which keywords were searched to the end today, per platform.

A second collection on the same day carries on with the keywords that are not done yet (so it starts where the last one
ended); once every keyword is done it only spot-checks a few of the earlier ones. The marks are per local day, so the next
day searches all keywords again from the beginning.
"""

import random
import sqlite3
from datetime import date

SPOT_CHECK_SIZE = 3
SPOT_CHECK_PAGES = 2


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS keyword_progress ("
        "platform TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL, keyword TEXT NOT NULL, "
        "finished_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (platform, kind, day, keyword))"
    )


def done_today(conn: sqlite3.Connection, platform: str, kind: str) -> set[str]:
    _ensure(conn)
    rows = conn.execute(
        "SELECT keyword FROM keyword_progress WHERE platform = ? AND kind = ? AND day = ?",
        (platform, kind, date.today().isoformat()),
    ).fetchall()
    return {str(row[0]) for row in rows}


def mark_done(conn: sqlite3.Connection, platform: str, kind: str, keyword: str) -> None:
    _ensure(conn)
    conn.execute(
        "INSERT OR REPLACE INTO keyword_progress (platform, kind, day, keyword) VALUES (?, ?, ?, ?)",
        (platform, kind, date.today().isoformat(), keyword),
    )
    conn.commit()


def _ensure_cursor(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS keyword_cursor ("
        "platform TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL, keyword TEXT NOT NULL, "
        "PRIMARY KEY (platform, kind, day))"
    )


def get_cursor(conn: sqlite3.Connection, platform: str, kind: str) -> str | None:
    """The keyword the last collection of today ended on (done, capped or cut off by a time limit)."""
    _ensure_cursor(conn)
    row = conn.execute(
        "SELECT keyword FROM keyword_cursor WHERE platform = ? AND kind = ? AND day = ?",
        (platform, kind, date.today().isoformat()),
    ).fetchone()
    return str(row[0]) if row else None


def set_cursor(conn: sqlite3.Connection, platform: str, kind: str, keyword: str | None) -> None:
    """Remember where the collection ended; None forgets it (the next one starts at the top of the list)."""
    _ensure_cursor(conn)
    if keyword is None:
        conn.execute("DELETE FROM keyword_cursor WHERE platform = ? AND kind = ? AND day = ?", (platform, kind, date.today().isoformat()))
        conn.commit()
        return
    conn.execute(
        "INSERT OR REPLACE INTO keyword_cursor (platform, kind, day, keyword) VALUES (?, ?, ?, ?)",
        (platform, kind, date.today().isoformat(), keyword),
    )
    conn.commit()


def rotate_after(pending: list[str], cursor: str | None) -> list[str]:
    """Start with the keyword after the one the last collection ended on, so a keyword that was capped (or a run that was cut
    short) does not make every following collection start at the top of the list again. Wraps round at the end."""
    if not cursor or cursor not in pending:
        return pending
    index = pending.index(cursor) + 1
    return pending[index:] + pending[:index]


def pending_words(ordered: list[str], done: set[str]) -> list[str]:
    """The keywords still to search today, in their normal order (which is also where the last run ended)."""
    return [word for word in ordered if word not in done]


def spot_check_words(ordered: list[str], rng: random.Random | None = None) -> list[str]:
    """A few of the earlier keywords to look at again once all of them are done."""
    rng = rng or random.Random()
    return rng.sample(ordered, min(SPOT_CHECK_SIZE, len(ordered)))
