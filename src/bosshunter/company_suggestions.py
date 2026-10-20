"""Companies worth blocking: outsourcing hits plus repeated / long-running postings."""

import sqlite3

from bosshunter.db import sighting_streak
from bosshunter.job_filters import matching_blocked_company

COMPANY_REPEAT_MIN = 3
LIVE_STATUSES_EXCLUDED = ("filtered", "skipped", "rejected")


def _norm(text: object) -> str:
    return "".join(str(text or "").split()).lower()


def company_suggestions(conn: sqlite3.Connection, blocked: list[str] | None = None) -> list[dict]:
    """Companies with reasons to block, strongest first; already-blocked ones are left out.

    Reasons: 外包(确认/疑似) from the outsourcing rules, 同公司N岗, 重复发布×N and
    连续在招N天 (a posting listed for 3+ days in a row).
    """
    blocked = blocked or []
    rows = conn.execute(
        "SELECT id, company, title, outsourcing_level FROM jobs "
        "WHERE deleted_at IS NULL AND status NOT IN (?, ?, ?)",
        LIVE_STATUSES_EXCLUDED,
    ).fetchall()

    companies: dict[str, dict] = {}
    for row in rows:
        name = str(row["company"] or "").strip()
        if not name or matching_blocked_company(name, blocked):
            continue
        entry = companies.setdefault(
            _norm(name),
            {"company": name, "jobs": 0, "titles": {}, "outsourcing": set(), "max_streak": 0},
        )
        entry["jobs"] += 1
        title = _norm(row["title"])
        entry["titles"][title] = entry["titles"].get(title, 0) + 1
        level = row["outsourcing_level"]
        if level in ("confirmed", "suspected"):
            entry["outsourcing"].add(level)
        entry["max_streak"] = max(entry["max_streak"], sighting_streak(conn, row["id"]))

    suggestions = []
    for entry in companies.values():
        reasons: list[str] = []
        weight = 0
        if "confirmed" in entry["outsourcing"]:
            reasons.append("外包(确认)")
            weight += 100
        elif "suspected" in entry["outsourcing"]:
            reasons.append("疑似外包")
            weight += 60
        if entry["jobs"] >= COMPANY_REPEAT_MIN:
            reasons.append(f"同公司{entry['jobs']}岗")
            weight += 10 * entry["jobs"]
        duplicate = max(entry["titles"].values())
        if duplicate >= 2:
            reasons.append(f"重复发布×{duplicate}")
            weight += 15 * duplicate
        if entry["max_streak"] >= 3:
            reasons.append(f"连续在招{entry['max_streak']}天")
            weight += 10 * entry["max_streak"]
        if reasons:
            suggestions.append(
                {"company": entry["company"], "reasons": reasons, "job_count": entry["jobs"], "weight": weight}
            )
    suggestions.sort(key=lambda item: (-item["weight"], item["company"]))
    return suggestions


def block_companies(conn: sqlite3.Connection, config: dict, names: list[str]) -> list[str]:
    """Add names to ``profile.blocked_companies`` (in the given config dict) and hide their open jobs.

    Returns the names newly added. The caller persists the config. Jobs still waiting
    (``ready``/``approved``) become ``skipped`` so they leave the review list; sent or
    replied jobs are untouched.
    """
    from bosshunter.db import add_history, update_job_status

    profile = config.setdefault("profile", {})
    current = [str(item) for item in profile.get("blocked_companies") or []]
    added = []
    for name in names:
        cleaned = str(name or "").strip()
        if cleaned and not matching_blocked_company(cleaned, current + added):
            added.append(cleaned)
    if not added:
        return []
    profile["blocked_companies"] = current + added
    rows = conn.execute(
        "SELECT id, company FROM jobs WHERE deleted_at IS NULL AND status IN ('ready', 'approved', 'pending', 'scored')"
    ).fetchall()
    for row in rows:
        if matching_blocked_company(row["company"], added):
            update_job_status(conn, row["id"], "skipped")
            add_history(conn, row["id"], "blocked_company", f"公司加入屏蔽名单：{row['company']}")
    return added
