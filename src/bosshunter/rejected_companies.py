"""Companies that already turned the user down: remembered in the database, never collected again.

Different from the config blocklist (a preference): this is a per-company outcome marked by the
user, kept with a note and a timestamp. Name variants count as one company (华为 / 华为技术).
"""

import sqlite3

from bosshunter.company_dedup import company_key, same_company
from bosshunter.db import add_history, update_job_status

# Jobs in these states already went out; marking the company never rewrites their history.
_FINAL_STATUSES = ("sent", "replied", "resume_sent", "needs_resume", "follow_up_sent", "rejected")


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rejected_companies ("
        "key TEXT PRIMARY KEY, company TEXT NOT NULL, note TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )


def list_rejected(conn: sqlite3.Connection) -> list[dict]:
    ensure_table(conn)
    rows = conn.execute("SELECT company, note, created_at FROM rejected_companies ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def rejected_names(conn: sqlite3.Connection) -> list[str]:
    return [item["company"] for item in list_rejected(conn)]


def is_rejected_company(company: object, names: list[str]) -> str | None:
    """The remembered company this name matches (same company, name variants included), else None."""
    for name in names:
        if same_company(company, name):
            return name
    return None


def add_rejected(conn: sqlite3.Connection, companies: list[str], note: str = "用户标记：已拒绝") -> list[str]:
    """Remember companies; returns the ones newly added."""
    ensure_table(conn)
    existing = rejected_names(conn)
    added: list[str] = []
    for company in companies:
        name = str(company or "").strip()
        key = company_key(name)
        if not name or not key or is_rejected_company(name, existing + added):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO rejected_companies (key, company, note) VALUES (?, ?, ?)", (key, name, note)
        )
        added.append(name)
    conn.commit()
    return added


def remove_rejected(conn: sqlite3.Connection, company: str) -> bool:
    ensure_table(conn)
    cursor = conn.execute("DELETE FROM rejected_companies WHERE key = ? OR company = ?", (company_key(company), company))
    conn.commit()
    return cursor.rowcount > 0


def mark_jobs_company_rejected(conn: sqlite3.Connection, job_ids: list[str]) -> dict:
    """The companies of the given jobs turned the user down.

    Remembers the companies, marks the given jobs ``rejected`` and hides every other waiting job of
    those companies (``filtered``). Jobs already sent or replied keep their own history.
    """
    if not job_ids:
        return {"companies": [], "marked_jobs": 0, "hidden_jobs": 0}
    placeholders = ",".join("?" for _ in job_ids)
    rows = conn.execute(
        f"SELECT id, company, status FROM jobs WHERE deleted_at IS NULL AND id IN ({placeholders})", job_ids
    ).fetchall()
    companies = sorted({str(row["company"]).strip() for row in rows if str(row["company"]).strip()})
    added = add_rejected(conn, companies)
    marked = 0
    for row in rows:
        if row["status"] != "rejected":
            update_job_status(conn, row["id"], "rejected")
            add_history(conn, row["id"], "rejected", "用户手动标记：该公司已拒绝")
            marked += 1
    hidden = apply_rejected_companies(conn)
    return {"companies": companies, "added": added, "marked_jobs": marked, "hidden_jobs": hidden}


def apply_rejected_companies(conn: sqlite3.Connection) -> int:
    """Filter every not-yet-sent job whose company is remembered as having rejected the user."""
    names = rejected_names(conn)
    if not names:
        return 0
    rows = conn.execute(
        "SELECT id, company FROM jobs WHERE deleted_at IS NULL "
        "AND status IN ('pending', 'scored', 'ready', 'approved', 'filtered')"
    ).fetchall()
    hidden = 0
    for row in rows:
        match = is_rejected_company(row["company"], names)
        if match and conn.execute("SELECT status FROM jobs WHERE id = ?", (row["id"],)).fetchone()["status"] != "filtered":
            update_job_status(conn, row["id"], "filtered")
            add_history(conn, row["id"], "filtered", f"公司已拒绝过：{match}")
            hidden += 1
    return hidden
