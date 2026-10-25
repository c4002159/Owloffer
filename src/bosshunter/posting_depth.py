"""On which page of a keyword's list were the postings that turned out to be good?

The collection reads a limited number of pages per keyword (20 by default). Whether postings that fit the user hide beyond them can
only be answered from data: for every posting seen for the first time this remembers the page it was found on, and ``report`` sets
that against what became of the posting (passed the scoring or not). If the pass rate does not fall off with depth, deeper pages are
worth reading; if it does, they are not.
"""

from __future__ import annotations

import sqlite3

BUCKETS = ((1, 3), (4, 10), (11, 20), (21, 10 ** 6))
PASSED = ("ready", "approved", "sent", "replied", "resume_sent", "needs_resume", "follow_up_sent", "error")


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS posting_pages ("
        "platform TEXT NOT NULL, source_job_id TEXT NOT NULL, keyword TEXT NOT NULL DEFAULT '', page INTEGER NOT NULL, "
        "seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (platform, source_job_id))"
    )


def record(conn: sqlite3.Connection, platform: str, source_job_id: str, keyword: str, page: int | None) -> None:
    """Remember the page of a posting seen for the first time (a later sighting does not change it)."""
    if not source_job_id or not page or str(source_job_id).startswith("card:"):
        return
    _ensure(conn)
    conn.execute(
        "INSERT OR IGNORE INTO posting_pages (platform, source_job_id, keyword, page) VALUES (?, ?, ?, ?)",
        (platform, str(source_job_id), keyword or "", int(page)),
    )
    conn.commit()


def report(conn: sqlite3.Connection, platform: str | None = None, days: int = 14) -> list[dict]:
    """Per platform and page band: how many postings, how many of them passed the scoring (``rate`` = passed / saved)."""
    _ensure(conn)
    marks = ",".join("?" for _ in PASSED)
    rows = conn.execute(
        f"""
        SELECT p.platform AS platform, p.page AS page, j.status AS status
        FROM posting_pages p LEFT JOIN jobs j ON j.source_job_id = p.source_job_id AND j.source_platform = p.platform AND j.deleted_at IS NULL
        WHERE p.seen_at >= datetime('now', ?) {"AND p.platform = ?" if platform else ""}
        """,
        ([f"-{int(days)} day", platform] if platform else [f"-{int(days)} day"]),
    ).fetchall()
    result: dict[tuple[str, int], dict] = {}
    for row in rows:
        band = next(index for index, (low, high) in enumerate(BUCKETS) if low <= row["page"] <= high)
        item = result.setdefault((row["platform"], band), {"seen": 0, "saved": 0, "passed": 0})
        item["seen"] += 1
        if row["status"] is not None:
            item["saved"] += 1
            item["passed"] += row["status"] in PASSED
    output = []
    for (name, band), item in sorted(result.items()):
        low, high = BUCKETS[band]
        output.append({
            "platform": name, "pages": f"{low}-{high}" if high < 10 ** 6 else f"{low}+", **item,
            "rate": round(item["passed"] / item["saved"], 3) if item["saved"] else None,
        })
    return output
