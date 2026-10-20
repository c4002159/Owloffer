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
