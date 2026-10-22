"""Where in each keyword's result list the earlier days stopped, so that a day does not read the same pages again.

A list has two parts worth reading: the head (what was posted or refreshed since the last look) and the deep pages nobody has read
yet (small companies that do not pay for a place at the top). Per (platform, keyword, cities) this remembers

* ``frontier``: the last deep page read; the next day reads on from there, and wraps around at the end of the list;
* ``head_pages``: how many pages at the top are worth reading: it grows while most of the head is new and shrinks when
  most of it is already in the database.

The share of a page's postings that are not yet in the database ("refresh rate") decides: a remembered page that is mostly new
means the list has moved, so reading restarts at that very page; a page that is mostly known is skipped and reading starts
after it.
"""

from __future__ import annotations

import sqlite3
from datetime import date

HEAD_DEFAULT = 3
HEAD_MIN = 1
HEAD_MAX = 10
FRESH_HIGH = 0.30                      # at least this share new: the page (or head) is worth reading
FRESH_LOW = 0.15                       # below this share new: the head is mostly known, read less of it next time


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS scan_frontier ("
        "platform TEXT NOT NULL, keyword TEXT NOT NULL, cities TEXT NOT NULL, "
        "frontier INTEGER NOT NULL DEFAULT 0, head_pages INTEGER NOT NULL DEFAULT 3, "
        "head_rate REAL, deep_rate REAL, updated_day TEXT, PRIMARY KEY (platform, keyword, cities))"
    )


def get(conn: sqlite3.Connection, platform: str, keyword: str, cities: str) -> dict:
    _ensure(conn)
    row = conn.execute(
        "SELECT frontier, head_pages, head_rate, deep_rate, updated_day FROM scan_frontier WHERE platform = ? AND keyword = ? AND cities = ?",
        (platform, keyword, cities),
    ).fetchone()
    if row is None:
        return {"frontier": 0, "head_pages": HEAD_DEFAULT, "head_rate": None, "deep_rate": None, "updated_day": ""}
    return {"frontier": int(row[0]), "head_pages": int(row[1]), "head_rate": row[2], "deep_rate": row[3], "updated_day": row[4] or ""}


def save(conn: sqlite3.Connection, platform: str, keyword: str, cities: str, **values) -> None:
    current = get(conn, platform, keyword, cities)
    current.update(values)
    conn.execute(
        "INSERT OR REPLACE INTO scan_frontier (platform, keyword, cities, frontier, head_pages, head_rate, deep_rate, updated_day) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (platform, keyword, cities, int(current["frontier"]), int(current["head_pages"]), current["head_rate"], current["deep_rate"],
         date.today().isoformat()),
    )
    conn.commit()


def refresh_rate(seen: int, duplicate: int) -> float | None:
    """Share of the postings read that were not in the database yet; ``None`` when nothing was read."""
    if seen <= 0:
        return None
    return max(0.0, min(1.0, (seen - duplicate) / seen))


def next_head_pages(current: int, rate: float | None) -> int:
    if rate is None:
        return current
    if rate >= FRESH_HIGH:
        return min(current + 2, HEAD_MAX)
    if rate < FRESH_LOW:
        return max(current - 1, HEAD_MIN)
    return current


def deep_start(frontier: int, head_pages: int, probe_rate: float | None) -> int:
    """First page of the deep window. ``frontier`` is the last deep page read before (0 = none yet)."""
    if frontier <= head_pages:
        return head_pages + 1
    if probe_rate is not None and probe_rate >= FRESH_HIGH:
        return frontier                  # the remembered page is mostly new: the list moved, start at that very page
    return frontier + 1
