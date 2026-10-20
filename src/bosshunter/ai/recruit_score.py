"""Activity score (0-100): how likely a posting is a live, reachable opening.

Computed locally (no API call) from signals BossHunter already stores: how recently the HR was
active, how fresh the posting is, whether the company looks like an outsourcer, and how complete
the JD is. It is a separate indicator from the AI match score, which only measures fit with the
user's experience; the two are never blended.
"""

from bosshunter.ai.prefilter import hr_active_level, job_age_days

# Weights add up to 100: HR activity 50, freshness 30, outsourcing 10, JD completeness 10.
HR_MAX, FRESHNESS_MAX, COMPANY_MAX, JD_MAX = 50, 30, 10, 10
_HR_POINTS = {3: 50, 2: 35, 1: 15, 0: 0}
_HR_UNKNOWN_POINTS = 25
_OUTSOURCING_POINTS = {"clean": 10, "suspected": 4, "confirmed": 0}


def _freshness_points(job: dict) -> tuple[int, str]:
    age = job_age_days(job)
    if age is None:
        return 15, "首次采集时间未知"
    if age <= 1:
        return 30, "1天内首次出现"
    if age <= 3:
        return 22, "3天内首次出现"
    if age <= 7:
        return 12, "7天内首次出现"
    return 0, "首次出现超过7天"


def _jd_points(job: dict) -> tuple[int, str]:
    length = len(str(job.get("jd") or "").strip())
    if length >= 150:
        return 10, "JD完整"
    if length >= 60:
        return 6, "JD较简略"
    return 2, "JD过短"


def streak_factor(streak: int) -> float:
    """HR-activity multiplier for a posting that has stayed listed for ``streak`` days in a row.

    Up to 2 days: unchanged. From day 3 it drops 20% per extra day, floored at 40%,
    because a posting that stays open for days suggests low hiring urgency.
    """
    if streak < 3:
        return 1.0
    return max(0.4, 1.0 - 0.2 * (streak - 2))


def compute_recruit_score(job: dict, streak: int = 0) -> tuple[int, str]:
    """Return (0-100 score, human-readable breakdown)."""
    level = hr_active_level(str(job.get("hr_active") or ""))
    hr_points = _HR_UNKNOWN_POINTS if level is None else _HR_POINTS[level]
    hr_label = str(job.get("hr_active") or "").strip() or "HR活跃度未知"
    factor = streak_factor(streak)
    if factor < 1.0:
        hr_points = int(round(hr_points * factor))
        hr_label += f"，连续在招{streak}天×{factor:.1f}"
    fresh_points, fresh_label = _freshness_points(job)
    company_points = _OUTSOURCING_POINTS.get(str(job.get("outsourcing_level") or "clean"), COMPANY_MAX)
    jd_points, jd_label = _jd_points(job)
    total = hr_points + fresh_points + company_points + jd_points
    detail = (
        f"HR活跃{hr_points}/{HR_MAX}({hr_label}) 新鲜度{fresh_points}/{FRESHNESS_MAX}({fresh_label}) "
        f"外包{company_points}/{COMPANY_MAX} {jd_label}{jd_points}/{JD_MAX}"
    )
    return total, detail


def min_match_score(config: dict) -> int:
    """Hard floor for the AI match score (0 = off); weaker matches never reach the review list."""
    raw = (config.get("scoring") or {}).get("min_match_score", 0)
    try:
        return min(max(int(raw), 0), 100)
    except (TypeError, ValueError):
        return 0


def passes_match_gate(match_score: int, config: dict) -> bool:
    return int(match_score) >= min_match_score(config)


def min_activity_score(config: dict) -> int:
    """Floor for the activity score (0 = off); postings that look dead never reach the review list."""
    raw = (config.get("scoring") or {}).get("min_activity_score", 0)
    try:
        return min(max(int(raw), 0), 100)
    except (TypeError, ValueError):
        return 0


def passes_activity_gate(activity_score: int, config: dict) -> bool:
    return int(activity_score) >= min_activity_score(config)
