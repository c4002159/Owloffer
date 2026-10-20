"""Review queue: what waits for the user's decision before anything is sent.

Jobs scoring at or above ``automation.review_min_score`` sit in status ``ready``
until the user approves or skips them. Reply drafts the monitor produced wait
in ``reply_pending`` until the user approves the final text, after which the
next monitor cycle sends it (``reply_approved``).
"""

import json
import re
import sqlite3

from bosshunter.db import add_history, update_job_status

COMPANY_REPEAT_MIN = 3
DEFAULT_REVIEW_CAP = 150
DEFAULT_MIN_SCORE = 60
UNCATEGORIZED = "其他"


def automation_settings(config: dict) -> dict:
    raw = config.get("automation") if isinstance(config.get("automation"), dict) else {}
    try:
        cap = max(1, int(raw.get("review_cap", DEFAULT_REVIEW_CAP)))
    except (TypeError, ValueError):
        cap = DEFAULT_REVIEW_CAP
    try:
        min_score = int(raw.get("review_min_score", DEFAULT_MIN_SCORE))
    except (TypeError, ValueError):
        min_score = DEFAULT_MIN_SCORE
    try:
        internship_quota = min(max(int(raw.get("internship_quota", 0)), 0), cap)
    except (TypeError, ValueError):
        internship_quota = 0
    return {
        "auto_send": bool(raw.get("auto_send", False)),
        "review_cap": cap,
        "review_min_score": min_score,
        "internship_quota": internship_quota,
    }


def job_kind(job: dict) -> str:
    """'internship' only with positive evidence (title or JD); everything else counts as full-time."""
    from bosshunter.employment import classify_employment

    return "internship" if classify_employment(job) == "internship" else "fulltime"


def kind_quotas(config: dict) -> dict[str, int]:
    """Review slots per kind: the internship quota, the rest of ``review_cap`` for full-time."""
    settings = automation_settings(config)
    return {
        "internship": settings["internship_quota"],
        "fulltime": settings["review_cap"] - settings["internship_quota"],
    }


def waiting_by_kind(conn: sqlite3.Connection, config: dict) -> dict[str, int]:
    """Jobs waiting for review or sending, split into full-time and internship."""
    settings = automation_settings(config)
    counts = {"fulltime": 0, "internship": 0}
    for job in review_pending(conn, settings["review_min_score"]) + approved_unsent(conn):
        counts[job_kind(job)] += 1
    return counts


def kind_full(conn: sqlite3.Connection, config: dict, job: dict) -> bool:
    """True when this job's kind has no free review slot (without a quota: when the whole cap is full)."""
    if automation_settings(config)["internship_quota"] <= 0:
        return review_cap_reached(conn, config)
    kind = job_kind(job)
    return waiting_by_kind(conn, config)[kind] >= kind_quotas(config)[kind]


def review_pending(conn: sqlite3.Connection, min_score: int) -> list[dict]:
    """Jobs waiting for review: scored high enough, not yet approved or skipped."""
    rows = conn.execute(
        """
        SELECT * FROM jobs
        WHERE status = 'ready' AND deleted_at IS NULL AND score >= ?
        ORDER BY COALESCE(category, ?) , score DESC
        """,
        (min_score, UNCATEGORIZED),
    ).fetchall()
    return [dict(row) for row in rows]


def approved_unsent(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'approved' AND deleted_at IS NULL ORDER BY score DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def review_cap_reached(conn: sqlite3.Connection, config: dict) -> bool:
    """True once reviewable plus approved-but-unsent jobs reach ``automation.review_cap``."""
    settings = automation_settings(config)
    waiting = len(review_pending(conn, settings["review_min_score"])) + len(approved_unsent(conn))
    return waiting >= settings["review_cap"]


def approve_jobs(conn: sqlite3.Connection, job_ids: list[str], note: str = "审查窗口确认") -> int:
    """Mark reviewed jobs approved; only jobs still in ``ready`` are touched."""
    changed = 0
    for job_id in job_ids:
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ? AND deleted_at IS NULL", (job_id,)
        ).fetchone()
        if row and row["status"] == "ready":
            update_job_status(conn, job_id, "approved")
            add_history(conn, job_id, "approved", note)
            changed += 1
    return changed


def revoke_approval(conn: sqlite3.Connection, job_ids: list[str]) -> int:
    """Put approved, unsent jobs back into the review list."""
    changed = 0
    for job_id in job_ids:
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ? AND deleted_at IS NULL", (job_id,)
        ).fetchone()
        if row and row["status"] == "approved":
            update_job_status(conn, job_id, "ready")
            add_history(conn, job_id, "status_changed", "审查窗口撤销通过")
            changed += 1
    return changed


def skip_jobs(conn: sqlite3.Connection, job_ids: list[str]) -> int:
    changed = 0
    for job_id in job_ids:
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ? AND deleted_at IS NULL", (job_id,)
        ).fetchone()
        if row and row["status"] in {"ready", "approved"}:
            update_job_status(conn, job_id, "skipped")
            add_history(conn, job_id, "skipped", "审查窗口跳过")
            changed += 1
    return changed


def direction_for_keyword(keyword: str, plan: dict | None) -> str:
    """Role category for a search keyword, taken from the keyword plan's directions."""
    if not plan or not keyword:
        return UNCATEGORIZED
    needle = str(keyword).strip().lower()
    for direction in plan.get("directions", []):
        if needle in (str(k).strip().lower() for k in direction.get("keywords", [])):
            return direction.get("name") or UNCATEGORIZED
    return UNCATEGORIZED


def assign_categories(conn: sqlite3.Connection, plan: dict | None) -> int:
    """Fill ``category`` for jobs that have none yet, from their source keyword."""
    rows = conn.execute(
        "SELECT id, source_keyword FROM jobs WHERE deleted_at IS NULL AND (category IS NULL OR category = '')"
    ).fetchall()
    updated = 0
    for row in rows:
        category = direction_for_keyword(row["source_keyword"], plan)
        conn.execute("UPDATE jobs SET category = ? WHERE id = ?", (category, row["id"]))
        updated += 1
    conn.commit()
    return updated


def refresh_recruit_scores(conn: sqlite3.Connection, config: dict) -> int:
    """Re-apply the long-listing penalty to jobs still waiting for review.

    A posting that stays listed for days gradually loses recruiting-authenticity
    points, so its blended score can sink below ``review_min_score`` and leave the
    review list. Only ``ready`` jobs with a stored match score are touched.
    """
    from bosshunter.ai.recruit_score import blend_scores, compute_recruit_score, recruit_weight
    from bosshunter.db import sighting_streak

    weight = recruit_weight(config)
    if weight <= 0:
        return 0
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'ready' AND deleted_at IS NULL AND match_score IS NOT NULL"
    ).fetchall()
    changed = 0
    for row in rows:
        job = dict(row)
        recruit, detail = compute_recruit_score(job, sighting_streak(conn, job["id"]))
        score = blend_scores(int(job["match_score"]), recruit, weight)
        if recruit == job.get("recruit_score") and score == job.get("score"):
            continue
        prefix = f"综合{score}=匹配{job['match_score']}×{1 - weight:.0%}+招聘真实度{recruit}×{weight:.0%}（{detail}）；"
        reason = re.sub(r"^综合[^；]*；", "", str(job.get("score_reason") or ""))
        conn.execute(
            "UPDATE jobs SET score = ?, recruit_score = ?, recruit_detail = ?, score_reason = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (score, recruit, detail, prefix + reason, job["id"]),
        )
        changed += 1
    conn.commit()
    return changed


def apply_match_gate(conn: sqlite3.Connection, config: dict) -> int:
    """Filter ``ready`` jobs whose AI match score is below ``scoring.min_match_score``."""
    from bosshunter.ai.recruit_score import min_match_score

    floor = min_match_score(config)
    if floor <= 0:
        return 0
    rows = conn.execute(
        "SELECT id, match_score FROM jobs WHERE status = 'ready' AND deleted_at IS NULL "
        "AND match_score IS NOT NULL AND match_score < ?",
        (floor,),
    ).fetchall()
    for row in rows:
        update_job_status(conn, row["id"], "filtered")
        add_history(conn, row["id"], "filtered", f"匹配分 {row['match_score']} 低于硬门槛 {floor}")
    return len(rows)


def apply_company_dedup(conn: sqlite3.Connection, config: dict | None = None) -> int:
    """Filter ``ready`` jobs whose company was already approved or sent today."""
    from bosshunter.company_dedup import cooldown_days, duplicate_of_approved_today

    days = cooldown_days(config)
    count = 0
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'ready' AND deleted_at IS NULL ORDER BY score DESC"
    ).fetchall()
    for row in rows:
        job = dict(row)
        original = duplicate_of_approved_today(conn, job, days)
        if original:
            update_job_status(conn, job["id"], "filtered")
            add_history(conn, job["id"], "filtered", f"与近期已通过的公司重复：{original}")
            count += 1
    return count


def approve_all(conn: sqlite3.Connection, config: dict) -> tuple[int, int]:
    """Approve every reviewable job, keeping only the best one per company.

    Returns (approved, filtered_as_duplicate). Companies approved within the cooldown window
    count as taken, and variants of one company (华为 / 华为技术) are one company.
    """
    from bosshunter.company_dedup import approved_within, cooldown_days, same_company

    jobs = sorted(
        review_pending(conn, automation_settings(config)["review_min_score"]),
        key=lambda job: job.get("score") or 0,
        reverse=True,
    )
    taken = [item["company"] for item in approved_within(conn, cooldown_days(config))]
    chosen: list[dict] = []
    duplicates: list[dict] = []
    for job in jobs:
        if any(same_company(job["company"], company) for company in taken):
            duplicates.append(job)
        else:
            chosen.append(job)
            taken.append(job["company"])
    approved = approve_jobs(conn, [job["id"] for job in chosen], note="审查窗口：通过全部")
    for job in duplicates:
        update_job_status(conn, job["id"], "filtered")
        add_history(conn, job["id"], "filtered", "通过全部时按公司去重：同公司已有更高分岗位")
    return approved, len(duplicates)


def _norm(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def repeat_flags(conn: sqlite3.Connection, jobs: list[dict]) -> dict[str, list[str]]:
    """Highlight labels per job id: repeated postings, busy companies, long-running listings."""
    from bosshunter.db import sighting_streak

    live = conn.execute(
        "SELECT company, title FROM jobs WHERE deleted_at IS NULL AND status NOT IN ('filtered', 'skipped', 'rejected')"
    ).fetchall()
    by_company: dict[str, int] = {}
    by_company_title: dict[tuple[str, str], int] = {}
    for row in live:
        company, title = _norm(row["company"]), _norm(row["title"])
        by_company[company] = by_company.get(company, 0) + 1
        by_company_title[(company, title)] = by_company_title.get((company, title), 0) + 1

    flags: dict[str, list[str]] = {}
    for job in jobs:
        company, title = _norm(job.get("company")), _norm(job.get("title"))
        labels = []
        if by_company.get(company, 0) >= COMPANY_REPEAT_MIN:
            labels.append(f"同公司{by_company[company]}岗")
        if by_company_title.get((company, title), 0) >= 2:
            labels.append(f"重复发布×{by_company_title[(company, title)]}")
        streak = sighting_streak(conn, job["id"])
        if streak >= 3:
            labels.append(f"连续在招{streak}天")
        if labels:
            flags[str(job["id"])] = labels
    return flags


def dispatch_approved(config: dict, report=None) -> dict:
    """One-click delivery: tailored resume + greeting for every approved job, then throttled sending.

    ``report`` is an optional ``callable(str)`` for progress lines. Sending itself
    uses ``send_greetings`` and therefore keeps the daily limit, random intervals,
    send windows and risk stops from the config.
    """
    from bosshunter.ai.greeter import generate_greetings
    from bosshunter.ai.resume import generate_tailored_resume, get_last_resume_failure_reason
    from bosshunter.db import get_db
    from bosshunter.executor.sender import send_greetings

    say = report or (lambda _msg: None)
    db = get_db()
    try:
        jobs = approved_unsent(db)
    finally:
        db.close()
    ids = [str(job["id"]) for job in jobs]
    result = {"approved": len(ids), "resumes": 0, "resume_failed": 0, "greetings": 0, "sent": 0}
    if not ids:
        say("没有已通过的岗位")
        return result

    for index, job in enumerate(jobs, 1):
        say(f"定制简历 {index}/{len(jobs)}：{job['company']}｜{job['title']}")
        try:
            path = generate_tailored_resume(str(job["id"]), config)
        except Exception as exc:  # one failed resume must not block the rest of the batch
            path = None
            say(f"  简历生成异常：{type(exc).__name__}")
        if path:
            result["resumes"] += 1
        else:
            result["resume_failed"] += 1
            reason = get_last_resume_failure_reason(str(job["id"]))
            if reason:
                say(f"  简历生成失败：{reason}")

    say("生成招呼语…")
    result["greetings"] = generate_greetings(config, job_ids=ids)
    say("开始按限速发送…")
    result["sent"] = send_greetings(config)
    say(f"完成：简历 {result['resumes']}，招呼语 {result['greetings']}，已发送 {result['sent']}")
    return result


# ---- reply drafts -------------------------------------------------------

def _parse_detail(detail: str) -> dict:
    try:
        value = json.loads(detail or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def pending_reply_drafts(conn: sqlite3.Connection) -> list[dict]:
    """Unresolved AI reply drafts that the user has not approved yet."""
    from bosshunter.db import get_unresolved_reply_pending

    drafts = []
    for row in get_unresolved_reply_pending(conn):
        if latest_approved_reply(conn, row["job_id"]):
            continue
        payload = _parse_detail(row["detail"])
        drafts.append({
            "history_id": row["id"],
            "job_id": row["job_id"],
            "company": row["company"],
            "title": row["title"],
            "hr_question": payload.get("hr_question", ""),
            "ai_reply": payload.get("ai_reply", ""),
        })
    return drafts


def approve_reply(conn: sqlite3.Connection, history_id: int, message: str) -> bool:
    """Record the user's final reply text; the monitor sends it on its next cycle."""
    text = str(message or "").strip()
    row = conn.execute(
        "SELECT id, job_id, action, detail FROM history WHERE id = ?", (history_id,)
    ).fetchone()
    if not text or not row or row["action"] != "reply_pending":
        return False
    latest = conn.execute(
        "SELECT MAX(id) AS id FROM history WHERE job_id = ? AND action = 'reply_pending'",
        (row["job_id"],),
    ).fetchone()
    if int(latest["id"] or 0) != int(row["id"]):
        return False
    payload = {
        "schema": "reply_approved.v1",
        "message": text[:1000],
        "pending_history_id": int(row["id"]),
        "pending_detail": row["detail"],
    }
    add_history(conn, row["job_id"], "reply_approved", json.dumps(payload, ensure_ascii=False))
    return True


def latest_approved_reply(conn: sqlite3.Connection, job_id: str) -> dict | None:
    """The newest approved-but-unsent reply for a job, or None."""
    row = conn.execute(
        """
        SELECT a.id, a.detail FROM history a
        WHERE a.job_id = ? AND a.action = 'reply_approved'
          AND NOT EXISTS (
              SELECT 1 FROM history r
              WHERE r.job_id = a.job_id AND r.id > a.id
                AND r.action IN ('replied', 'auto_replied', 'reply_dismissed')
          )
        ORDER BY a.id DESC LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    if not row:
        return None
    payload = _parse_detail(row["detail"])
    message = str(payload.get("message") or "").strip()
    if not message:
        return None
    return {
        "history_id": row["id"],
        "message": message,
        "pending_history_id": payload.get("pending_history_id"),
        "pending_detail": payload.get("pending_detail", ""),
    }
