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


def pending_words(ordered: list[str], done: set[str]) -> list[str]:
    """The keywords still to search today, in their normal order (which is also where the last run ended)."""
    return [word for word in ordered if word not in done]


def spot_check_words(ordered: list[str], rng: random.Random | None = None) -> list[str]:
    """A few of the earlier keywords to look at again once all of them are done."""
    rng = rng or random.Random()
    return rng.sample(ordered, min(SPOT_CHECK_SIZE, len(ordered)))
