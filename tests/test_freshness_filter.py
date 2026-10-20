from datetime import datetime, timedelta, timezone

import pytest

from bosshunter.ai.prefilter import (
    freshness_filter_result,
    hr_active_level,
    job_age_days,
    quick_score,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("在线", 3),
        ("刚刚活跃", 3),
        ("今日活跃", 3),
        ("3日内活跃", 2),
        ("2天内活跃", 2),
        ("本周活跃", 1),
        ("本月活跃", 1),
        ("半年前活跃", 0),
        ("", None),
        ("未知文案", None),
    ],
)
def test_hr_active_level(label, expected):
    assert hr_active_level(label) == expected


def test_job_age_days_uses_first_seen_timestamp():
    created = (NOW - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    assert job_age_days({"created_at": created}, NOW) == pytest.approx(5.0)
    assert job_age_days({}, NOW) is None
    assert job_age_days({"created_at": "garbage"}, NOW) is None


def test_filters_are_off_by_default():
    job = {"created_at": "2020-01-01 00:00:00", "hr_active": "半年前活跃"}
    assert freshness_filter_result(job, {}, NOW)[0] == 100


def test_age_filter_blocks_old_jobs_only():
    profile = {"max_job_age_days": 3}
    old = {"created_at": (NOW - timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S")}
    new = {"created_at": (NOW - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")}
    assert freshness_filter_result(old, profile, NOW)[0] == 0
    assert freshness_filter_result(new, profile, NOW)[0] == 100
    assert freshness_filter_result({}, profile, NOW)[0] == 100


def test_hr_activity_filter_and_unknown_handling():
    profile = {"hr_active_min_level": 2}
    assert freshness_filter_result({"hr_active": "在线"}, profile, NOW)[0] == 100
    assert freshness_filter_result({"hr_active": "3日内活跃"}, profile, NOW)[0] == 100
    assert freshness_filter_result({"hr_active": "本月活跃"}, profile, NOW)[0] == 0
    assert freshness_filter_result({"hr_active": ""}, profile, NOW)[0] == 100
    strict = {**profile, "hr_active_unknown_pass": False}
    assert freshness_filter_result({"hr_active": ""}, strict, NOW)[0] == 0


def test_quick_score_applies_freshness_filter():
    config = {"profile": {"hr_active_min_level": 3, "filter_unparsed_salary": False}}
    stale = {"title": "Agent 工程师", "company": "某科技有限公司X", "salary": "20-30K", "hr_active": "本月活跃"}
    fresh = {**stale, "hr_active": "在线"}
    assert quick_score(stale, config)[0] == 0
    assert quick_score(fresh, config)[0] == 100
