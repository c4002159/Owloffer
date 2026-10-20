"""Zombie postings: listed for days on end while the HR is neither online nor recently active.

A posting that stays open for ``automation.zombie_days`` days in a row (default 7) and whose
HR was last seen inactive is probably not hiring. Such jobs leave the review list and their
company is recommended for the blacklist (the user still confirms the block).
"""

import json
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from bosshunter.ai.prefilter import hr_active_level
from bosshunter.db import add_history, sighting_streak, update_job_status

DEFAULT_ZOMBIE_DAYS = 7
INACTIVE_MAX_LEVEL = 1  # hr_active_level: 0 = long inactive, 1 = within a month; 2-3 = recent / online
REFRESH_MAX_AGE_HOURS = 20


def zombie_days(config: dict | None) -> int:
    raw = ((config or {}).get("automation") or {}).get("zombie_days", DEFAULT_ZOMBIE_DAYS)
    try:
        return min(max(int(raw), 2), 60)
    except (TypeError, ValueError):
        return DEFAULT_ZOMBIE_DAYS


def is_zombie(job: dict, streak: int, days: int = DEFAULT_ZOMBIE_DAYS) -> bool:
    """Long-listed and known-inactive HR. An unknown activity label never makes a zombie."""
    level = hr_active_level(str(job.get("hr_active") or ""))
    return streak >= days and level is not None and level <= INACTIVE_MAX_LEVEL


def zombie_note(job: dict, streak: int) -> str:
    return f"僵尸岗位：连续在招 {streak} 天，HR 活跃度「{job.get('hr_active') or '未知'}」"


def apply_zombie_filter(conn: sqlite3.Connection, config: dict) -> int:
    """Filter ``ready`` zombie jobs and stamp every zombie (any status) so its company can be recommended."""
    days = zombie_days(config)
    rows = conn.execute(
        "SELECT * FROM jobs WHERE deleted_at IS NULL AND status IN ('ready', 'approved', 'pending', 'scored', 'filtered')"
    ).fetchall()
    filtered = 0
    for row in rows:
        job = dict(row)
        streak = sighting_streak(conn, job["id"])
        if not is_zombie(job, streak, days):
            continue
        if not job.get("zombie_at"):
            conn.execute("UPDATE jobs SET zombie_at = CURRENT_TIMESTAMP WHERE id = ?", (job["id"],))
        if job["status"] == "ready":
            update_job_status(conn, job["id"], "filtered")
            add_history(conn, job["id"], "filtered", zombie_note(job, streak))
            filtered += 1
    conn.commit()
    return filtered


def _jobs_to_refresh(conn: sqlite3.Connection, days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=REFRESH_MAX_AGE_HOURS)
    rows = conn.execute(
        "SELECT * FROM jobs WHERE deleted_at IS NULL AND status IN ('ready', 'approved') "
        "AND COALESCE(url, '') != '' AND zombie_at IS NULL"
    ).fetchall()
    due = []
    for row in rows:
        job = dict(row)
        checked = job.get("hr_checked_at")
        if checked:
            try:
                if datetime.fromisoformat(str(checked)).replace(tzinfo=timezone.utc) > cutoff:
                    continue
            except ValueError:
                pass
        # Only jobs close to the zombie threshold are worth another page view.
        if sighting_streak(conn, job["id"]) >= days - 1:
            due.append(job)
    return due


def refresh_long_listed(config: dict, report=print, *, max_pages: int = 30, stop_event=None) -> dict:
    """Re-read the HR activity of long-listed jobs (one detail page each, slowly) before judging them.

    The HR label saved at first collection can be a week old, so it cannot prove a job is a zombie.
    Stops at a risk signal, a safety limit or ``max_pages``. Returns {"checked", "updated", "stopped"}.
    """
    from bosshunter.browser import close_tab, evaluate, new_tab, wait_for_load
    from bosshunter.collection.platforms.boss import JS_DETECT_COLLECTION_RISK, JS_EXTRACT_DETAIL
    from bosshunter.db import get_db
    from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop

    result = {"checked": 0, "updated": 0, "stopped": ""}
    db = get_db()
    try:
        due = _jobs_to_refresh(db, zombie_days(config))[:max_pages]
        guard = PlatformAccessGuard(db, config, "collection", "boss")
        for job in due:
            if stop_event is not None and stop_event.is_set():
                result["stopped"] = "已手动停止"
                break
            try:
                guard.reserve("detail_page")
            except PlatformSafetyStop as exc:
                result["stopped"] = f"安全上限：{exc.reason}"
                break
            target = new_tab(job["url"] if str(job["url"]).startswith("http") else f"https://www.zhipin.com{job['url']}", True)
            if not target:
                result["stopped"] = "无法打开页面（请检查 Chrome 连接）"
                break
            try:
                wait_for_load(target)
                time.sleep(random.uniform(2.0, 4.0))
                risk = evaluate(target, JS_DETECT_COLLECTION_RISK)
                risk_data = json.loads(risk) if isinstance(risk, str) else (risk or {})
                if isinstance(risk_data, dict) and risk_data.get("risk"):
                    guard.lock(str(risk_data["risk"]))
                    result["stopped"] = f"检测到风险信号：{risk_data['risk']}"
                    break
                raw = evaluate(target, JS_EXTRACT_DETAIL)
                info = json.loads(raw) if isinstance(raw, str) else (raw or {})
                result["checked"] += 1
                latest = str(info.get("hr_active") or "").strip() if isinstance(info, dict) else ""
                if latest:
                    db.execute(
                        "UPDATE jobs SET hr_active = ?, hr_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (latest, job["id"]),
                    )
                    result["updated"] += 1
                else:
                    db.execute("UPDATE jobs SET hr_checked_at = CURRENT_TIMESTAMP WHERE id = ?", (job["id"],))
                db.commit()
            finally:
                close_tab(target)
            time.sleep(random.uniform(3.0, 6.0))
        if result["checked"]:
            report(f"已复查 {result['checked']} 个长期在招岗位的 HR 活跃度，更新 {result['updated']} 个")
        return result
    finally:
        db.close()
