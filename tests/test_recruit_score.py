import pytest

from bosshunter.ai.recruit_score import (
    compute_recruit_score,
    min_activity_score,
    min_match_score,
    passes_activity_gate,
    passes_match_gate,
)


def test_blending_is_gone():
    import bosshunter.ai.recruit_score as module

    assert not hasattr(module, "blend_scores") and not hasattr(module, "recruit_weight")


def test_active_online_hr_with_full_jd_scores_high():
    job = {"hr_active": "在线", "jd": "x" * 200, "outsourcing_level": "clean"}
    score, detail = compute_recruit_score(job)
    assert score >= 80
    assert "在线" in detail


def test_inactive_outsourcing_short_jd_scores_low():
    job = {"hr_active": "半年前活跃", "jd": "招人", "outsourcing_level": "confirmed"}
    assert compute_recruit_score(job)[0] <= 30


def test_unknown_hr_is_neutral_not_penalised_to_zero():
    score, _ = compute_recruit_score({"jd": "x" * 200})
    assert 0 < score < 100


def test_activity_and_match_floors_are_independent_settings():
    config = {"scoring": {"min_match_score": 50, "min_activity_score": 70}}
    assert min_match_score(config) == 50 and min_activity_score(config) == 70
    assert passes_match_gate(50, config) and not passes_match_gate(49, config)
    assert passes_activity_gate(70, config) and not passes_activity_gate(69, config)
    assert min_activity_score({}) == 0 and passes_activity_gate(0, {})
    assert min_activity_score({"scoring": {"min_activity_score": "bad"}}) == 0
    assert min_activity_score({"scoring": {"min_activity_score": 500}}) == 100


def test_weights_are_hr_50_freshness_30_outsourcing_10_jd_10():
    from bosshunter.ai.recruit_score import COMPANY_MAX, FRESHNESS_MAX, HR_MAX, JD_MAX

    assert (HR_MAX, FRESHNESS_MAX, COMPANY_MAX, JD_MAX) == (50, 30, 10, 10) and HR_MAX + FRESHNESS_MAX + COMPANY_MAX + JD_MAX == 100
    best = {"hr_active": "在线", "jd": "x" * 200, "outsourcing_level": "clean",
            "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}
    score, detail = compute_recruit_score(best)
    assert score == 100
    assert "HR活跃50/50" in detail and "新鲜度30/30" in detail and "外包10/10" in detail and "JD完整10/10" in detail


def test_each_component_scales_inside_its_own_maximum():
    base = {"hr_active": "在线", "jd": "x" * 200, "outsourcing_level": "clean"}
    online, _ = compute_recruit_score(base)
    stale_hr, _ = compute_recruit_score({**base, "hr_active": "半年前活跃"})
    assert online - stale_hr == 50                       # HR activity is worth 50 points
    confirmed, _ = compute_recruit_score({**base, "outsourcing_level": "confirmed"})
    assert online - confirmed == 10                      # outsourcing is worth 10
    short_jd, _ = compute_recruit_score({**base, "jd": "招人"})
    assert online - short_jd == 8                        # JD completeness: 10 -> 2
    long_listed, detail = compute_recruit_score(base, streak=5)
    assert online - long_listed == 30 and "连续在招5天" in detail   # 50 * 0.4 left = 20
