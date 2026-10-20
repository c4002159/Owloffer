"""Recruiting-authenticity score: how likely is this posting a live, reachable opening.

Computed locally (no API call) from signals BossHunter already stores, on a 0-100
scale. The scorer blends it with the AI match score using ``scoring.recruit_weight``.
"""

from bosshunter.ai.prefilter import hr_active_level, job_age_days

_HR_POINTS = {3: 40, 2: 28, 1: 12, 0: 0}
_HR_UNKNOWN_POINTS = 20
_OUTSOURCING_POINTS = {"clean": 20, "suspected": 8, "confirmed": 0}


def recruit_weight(config: dict) -> float:
    """Share (0-1) of the final score given to recruiting authenticity; 0 disables blending."""
    raw = (config.get("scoring") or {}).get("recruit_weight", 0)
    try:
        return min(max(float(raw), 0.0), 0.9)
    except (TypeError, ValueError):
        return 0.0


def _freshness_points(job: dict) -> tuple[int, str]:
    age = job_age_days(job)
    if age is None:
        return 10, "首次采集时间未知"
    if age <= 1:
        return 20, "1天内首次出现"
    if age <= 3:
        return 15, "3天内首次出现"
    if age <= 7:
        return 8, "7天内首次出现"
    return 0, "首次出现超过7天"


def _jd_points(job: dict) -> tuple[int, str]:
    length = len(str(job.get("jd") or "").strip())
    if length >= 150:
        return 20, "JD完整"
    if length >= 60:
        return 12, "JD较简略"
    return 4, "JD过短"


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
    company_points = _OUTSOURCING_POINTS.get(str(job.get("outsourcing_level") or "clean"), 20)
    jd_points, jd_label = _jd_points(job)
    total = hr_points + fresh_points + company_points + jd_points
    detail = (
        f"HR活跃{hr_points}/40({hr_label}) 新鲜度{fresh_points}/20({fresh_label}) "
        f"公司{company_points}/20 {jd_label}{jd_points}/20"
    )
    return total, detail


def blend_scores(match_score: int, recruit_score: int, weight: float) -> int:
    """Blend the AI match score and the recruiting score into one 0-100 number."""
    return int(round(match_score * (1 - weight) + recruit_score * weight))


def min_match_score(config: dict) -> int:
    """Hard floor for the AI match score (0 = off); weaker matches never reach the review list."""
    raw = (config.get("scoring") or {}).get("min_match_score", 0)
    try:
        return min(max(int(raw), 0), 100)
    except (TypeError, ValueError):
        return 0


def passes_match_gate(match_score: int, config: dict) -> bool:
    return int(match_score) >= min_match_score(config)
