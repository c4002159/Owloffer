from bosshunter.ai.recruit_score import blend_scores, compute_recruit_score, recruit_weight


def test_recruit_weight_is_clamped_and_defaults_off():
    assert recruit_weight({}) == 0
    assert recruit_weight({"scoring": {"recruit_weight": 0.5}}) == 0.5
    assert recruit_weight({"scoring": {"recruit_weight": 5}}) == 0.9
    assert recruit_weight({"scoring": {"recruit_weight": "x"}}) == 0


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


def test_blend_matches_half_half():
    assert blend_scores(80, 60, 0.5) == 70
    assert blend_scores(80, 60, 0) == 80
