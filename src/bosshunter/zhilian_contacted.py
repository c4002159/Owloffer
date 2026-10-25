"""Zhilian postings that the account already applied to or talked about.

The detail page shows it: the apply button reads "继续沟通" instead of "立即投递". Such a posting used to be saved like any other,
scored, given a greeting and queued for delivery, and the delivery then found the button and gave up ("already_contacted"): a lost
sending slot of 60-180 seconds each (about 4 in 10 delivery attempts on Zhilian one afternoon). Now the collection notices it on the
page it has opened anyway and does not save the posting. The number is kept here so that the posting is not opened again in the next
run just to find out the same thing.
"""

from __future__ import annotations

import sqlite3

CONTACTED_LABEL = "继续沟通"


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS zhilian_contacted ("
        "source_job_id TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )


def is_contacted_label(label: object) -> bool:
    return CONTACTED_LABEL in str(label or "")


def remember(conn: sqlite3.Connection, source_job_id: str) -> None:
    _ensure(conn)
    conn.execute("INSERT OR IGNORE INTO zhilian_contacted (source_job_id) VALUES (?)", (str(source_job_id),))
    conn.commit()


def known(conn: sqlite3.Connection, source_job_id: str) -> bool:
    _ensure(conn)
    return conn.execute("SELECT 1 FROM zhilian_contacted WHERE source_job_id = ?", (str(source_job_id),)).fetchone() is not None
