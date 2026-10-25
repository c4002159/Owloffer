"""Can Zhilian's list tell that a posting is too old, before its detail page is opened?

The age limit (``update_age_reason``) used to be checked only on the detail page, so most detail pages that were opened (about
a third of all postings seen in a run, and the biggest share of the time) were opened just to find out that the posting was old.
The list shows a publish date too, but it is the date the posting was first published, and the detail page shows the date it was
last updated: a posting that was refreshed is old in the list and new on its page. Rejecting on the list date alone would drop
those.

So the program checks it itself instead of assuming: every detail page that is opened records whether the list date would have
rejected the posting and whether the page did. The list date is used to reject early only while, among the postings it would have
rejected, almost none were fine according to their page.
"""

from __future__ import annotations

import sqlite3

MIN_REJECTED_SAMPLES = 20           # postings the list date would have rejected, before the list date is trusted at all
MAX_WRONG_SHARE = 0.03              # of those, at most this share may have been fine on their page
WINDOW = 300                        # the latest comparisons that count


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS zhilian_age_pairs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, list_reject INTEGER NOT NULL, detail_reject INTEGER NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )


def record(conn: sqlite3.Connection, list_reject: bool, detail_reject: bool) -> None:
    _ensure(conn)
    conn.execute("INSERT INTO zhilian_age_pairs (list_reject, detail_reject) VALUES (?, ?)", (int(list_reject), int(detail_reject)))
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict:
    """``{"rejected": n, "wrong": k, "share": k / n, "trusted": bool}`` over the latest comparisons."""
    _ensure(conn)
    rows = conn.execute(
        "SELECT list_reject, detail_reject FROM zhilian_age_pairs ORDER BY id DESC LIMIT ?", (WINDOW,)
    ).fetchall()
    rejected = [row for row in rows if row[0]]
    wrong = sum(1 for row in rejected if not row[1])
    share = wrong / len(rejected) if rejected else 1.0
    return {"rejected": len(rejected), "wrong": wrong, "share": round(share, 3),
            "trusted": len(rejected) >= MIN_REJECTED_SAMPLES and share <= MAX_WRONG_SHARE}
