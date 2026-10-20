from datetime import date, timedelta

import pytest

from bosshunter import keyword_strategy as ks
from bosshunter.db import get_db, insert_job, update_job_status


@pytest.fixture
def db(tmp_path):
    conn = get_db(tmp_path / "jobs.db")
    yield conn
    conn.close()


def _jobs(db, keyword, total, passed, hr="3小时前在线", prefix=None):
    prefix = prefix or keyword
    for index in range(total):
        job_id = f"{prefix}-{index}"
        insert_job(db, {"id": job_id, "title": f"岗位{job_id}", "company": f"公司{job_id}", "salary": "", "city": "上海",
                        "experience": "", "jd": "x" * 100, "hr_name": "", "hr_title": "", "url": "", "hr_active": hr,
                        "source_keyword": keyword})
        update_job_status(db, job_id, "ready" if index < passed else "filtered")


def test_budgets_cover_every_keyword_and_shrink_by_tier():
    tiers = {"core": ["a", "b"], "expand": ["c", "d", "e"], "explore": ["f"]}
    caps = ks.first_pass_caps(tiers, raw_target=240)
    assert set(caps) == {"a", "b", "c", "d", "e", "f"}
    assert caps["a"] == 60 and caps["c"] == 24 and caps["f"] == 24
    assert caps["a"] > caps["c"]
    assert min(ks.first_pass_caps(tiers, raw_target=2).values()) == ks.MIN_SLICE      # tiny gaps still touch every keyword


def test_platform_share_is_everything_for_one_platform_and_even_for_several():
    assert ks.platform_share(240, 1) == 240 and ks.platform_share(240, 3) == 80


def test_scores_need_enough_samples_and_missing_signals_are_neutral(db):
    _jobs(db, "好词", 12, 6)
    _jobs(db, "少样本", 3, 3)
    scores = ks.keyword_scores(db)
    assert scores["少样本"]["score"] is None
    assert scores["好词"]["pass_rate"] == 0.5 and scores["好词"]["reply_rate"] == 0.5 and scores["好词"]["score"] > 0.4


def test_explore_word_with_enough_samples_and_no_passes_is_paused_then_comes_back(db):
    _jobs(db, "死词", ks.PAUSE_SAMPLES, 0)
    words = ["死词"]
    plan = {"explore": ["死词"]}
    moves = ks.adjust_tiers(db, words, plan)
    assert moves and moves[0]["to"] == "paused"
    assert ks.current_tiers(db, words, plan)["paused"] == ["死词"]
    later = date.today() + timedelta(days=ks.PAUSE_DAYS + 1)
    assert ks.current_tiers(db, words, plan, today=later)["explore"] == ["死词"]


def test_explore_word_is_promoted_after_two_good_days_and_pins_are_respected(db):
    for keyword, passed in (("强", 10), ("中", 5), ("弱", 1), ("最弱", 0)):
        _jobs(db, keyword, 12, passed)
    words, plan = ["强", "中", "弱", "最弱"], {"core": ["中", "弱", "最弱"], "explore": ["强"]}
    day1 = date.today()
    assert ks.adjust_tiers(db, words, plan, today=day1) == []                         # first good day: streak only
    assert ks.adjust_tiers(db, words, plan, today=day1) == []                         # same day never counts twice
    moves = ks.adjust_tiers(db, words, plan, today=day1 + timedelta(days=1))
    assert [(m["keyword"], m["from"], m["to"]) for m in moves] == [("强", "explore", "expand")]
    ks.set_pinned(db, "强", True, tier="expand")
    assert ks.adjust_tiers(db, words, plan, today=day1 + timedelta(days=5)) == []


def test_core_keeps_at_least_four_words_when_demoting(db):
    for index, passed in enumerate((10, 9, 8, 7, 0)):
        _jobs(db, f"词{index}", 12, passed)
    words = [f"词{i}" for i in range(5)]
    plan = {"core": words}
    start = date.today()
    for offset in range(5):
        ks.adjust_tiers(db, words, plan, today=start + timedelta(days=offset))
    assert len(ks.current_tiers(db, words, plan)["core"]) >= ks.MIN_CORE


def test_first_pass_schedule_covers_every_keyword_and_later_passes_favour_scored_ones(db, monkeypatch):
    from bosshunter import fill

    from pathlib import Path
    path = Path(db.execute("PRAGMA database_list").fetchone()["file"])
    monkeypatch.setattr("bosshunter.fill.get_db", lambda: get_db(path))
    words = ["a", "b", "c", "d"]
    plan = {"core": ["a", "b"], "expand": ["c"], "explore": ["d"]}
    first = fill.keyword_schedule("fulltime", words, 1, 60, plan, per_keyword=20)
    assert [word for word, _ in first] == ["a", "b", "c", "d"]
    assert dict(first)["a"] >= dict(first)["c"] >= dict(first)["d"]
    _jobs(db, "d", 12, 12)
    _jobs(db, "a", 12, 0)
    later = fill.keyword_schedule("fulltime", words, 2, 60, plan, per_keyword=20)
    assert later[0] == ("d", 20) and later[-1][0] == "a"
    intern = fill.keyword_schedule("internship", ["x", "y"], 1, 15, plan, per_keyword=20)
    assert len(intern) == 2 and all(cap >= ks.MIN_SLICE for _, cap in intern)


def test_greeting_prompt_has_a_platform_specific_scene():
    from bosshunter.ai.greeter import GREETING_PROMPT, _platform_style

    assert "{platform_style}" in GREETING_PROMPT
    assert "微信" in _platform_style({"source_platform": "51job"})
    assert "聊天窗口" in _platform_style({"source_platform": "liepin"}) and "BOSS" in _platform_style({})
