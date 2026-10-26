import random
from datetime import date, datetime, timedelta

import pytest

from bosshunter import keyword_trials as kt
from bosshunter import term_stats as ts
from bosshunter.db import get_db, insert_job, update_job_status


@pytest.fixture
def db(tmp_path):
    conn = get_db(tmp_path / "t.db")
    yield conn
    conn.close()


def _config(*words, slots=3, deal_breakers=()):
    return {"search": {"keywords": list(words)}, "profile": {"deal_breakers": list(deal_breakers)}, "automation": {"keyword_trial_slots": slots}}


def _seen(db, title, times, day=None):
    for _ in range(times):
        ts.note_seen(db, title, day or date.today())


def _scored(db, title, passed, failed, start=0):
    for number in range(passed + failed):
        ts.note_outcome(db, f"{title}-{start + number}", title, "", number < passed, date.today())


def _jobs(db, keyword, total, passed, prefix="j"):
    for index in range(total):
        job_id = f"{prefix}-{keyword}-{index}"
        insert_job(db, {"id": job_id, "title": f"岗位{job_id}", "company": f"公司{job_id}", "salary": "", "city": "上海", "experience": "",
                        "jd": "x" * 100, "hr_name": "", "hr_title": "", "url": "", "hr_active": "", "source_keyword": keyword})
        update_job_status(db, job_id, "ready" if index < passed else "filtered")
    db.commit()


def test_role_names_drop_levels_brackets_pay_and_everything_after_a_separator():
    assert ts.role_name("高级Java开发工程师（上海）") == "java开发工程师"
    assert ts.role_name("AI研发工程师-大模型方向 20-30K") == "ai研发工程师"
    assert ts.role_name("算法实习生") == "算法"[:0] or ts.role_name("算法实习生") == ""      # too short once the level word is gone
    assert ts.role_name("上海") == ""
    assert ts.role_name("") == ""


def test_postings_are_counted_by_role_name_when_listed_and_when_scored(db):
    _seen(db, "AI研发工程师（上海）", 5)
    _scored(db, "AI研发工程师", 3, 1)
    row = db.execute("SELECT SUM(seen) AS seen, SUM(scored) AS scored, SUM(passed) AS passed FROM term_daily WHERE term = 'role:ai研发工程师'").fetchone()
    assert (row["seen"], row["scored"], row["passed"]) == (5, 4, 3)


def test_roles_without_a_keyword_that_are_common_enough_are_candidates(db):
    _seen(db, "AI研发工程师", 6)
    _seen(db, "Agent开发工程师", 9)                      # covered by a keyword
    _seen(db, "数字后端工程师", 8)                        # a deal-breaker
    _seen(db, "应用工程师", 8)                           # too vague to search
    _seen(db, "稀有岗位工程师", 2)                        # too rare
    found = ts.mine_roles(db, ["Agent开发工程师"], exclude=["数字后端"])
    assert [item["role"] for item in found] == ["ai研发工程师"]
    assert found[0]["seen"] == 6


def test_the_draw_prefers_names_that_passed_but_still_gives_unknown_ones_a_chance():
    candidates = [{"role": "好岗位", "seen": 20, "scored": 20, "passed": 18}, {"role": "差岗位", "seen": 20, "scored": 20, "passed": 1},
                  {"role": "没评过", "seen": 20, "scored": 0, "passed": 0}]
    first = {"好岗位": 0, "差岗位": 0, "没评过": 0}
    for seed in range(200):
        first[kt.pick(candidates, 0.3, 1, random.Random(seed), set())[0]["role"]] += 1
    assert first["好岗位"] > first["没评过"] > first["差岗位"]
    assert [i["role"] for i in kt.pick(candidates, 0.3, 3, random.Random(1), {"好岗位"})] and all(i["role"] != "好岗位" for i in kt.pick(candidates, 0.3, 3, random.Random(1), {"好岗位"}))
    assert kt.pick([{"role": "x岗位", "seen": 9, "scored": 8, "passed": 0}], 0.3, 1, random.Random(1), set()) == []    # scored often, never passed


def test_free_slots_are_filled_with_new_keywords_in_the_explore_tier(db, tmp_path):
    _seen(db, "AI研发工程师", 8)
    _seen(db, "大模型测试工程师", 7)
    result = kt.run(db, _config("Agent开发工程师", slots=2), tmp_path, rng=random.Random(3))
    assert sorted(result["started"]) == ["AI研发工程师", "大模型测试工程师"]
    assert "AI研发工程师" in result["config"]["search"]["keywords"] and "Agent开发工程师" in result["config"]["search"]["keywords"]
    assert db.execute("SELECT tier FROM keyword_tiers WHERE keyword = 'AI研发工程师'").fetchone()["tier"] == "explore"
    assert len(kt.state(tmp_path)["active"]) == 2
    again = kt.run(db, result["config"], tmp_path, rng=random.Random(3))
    assert again["started"] == [] and again["config"] is None                       # the slots are taken


def test_zero_slots_means_no_trials(db, tmp_path):
    _seen(db, "AI研发工程师", 8)
    assert kt.run(db, _config("Agent开发工程师", slots=0), tmp_path)["started"] == []


def _start_one(db, tmp_path, config=None):
    _seen(db, "AI研发工程师", 8)
    config = config or _config("Agent开发工程师", slots=1)
    return kt.run(db, config, tmp_path, rng=random.Random(1), today=date.today() - timedelta(days=2))["config"]


def test_a_trial_that_brings_good_jobs_is_kept(db, tmp_path):
    config = _start_one(db, tmp_path)
    _scored(db, "别的岗位", 6, 14)                                     # the average pass rate is 30 %
    _jobs(db, "AI研发工程师", 16, 8)
    result = kt.run(db, config, tmp_path, rng=random.Random(1))
    assert result["kept"] == ["AI研发工程师"] and result["config"] is None
    assert kt.state(tmp_path)["active"] == [] and kt.state(tmp_path)["history"][-1]["result"] == "kept"


def test_a_trial_that_brings_poor_jobs_is_removed_again_and_not_tried_for_a_month(db, tmp_path):
    config = _start_one(db, tmp_path)
    _scored(db, "别的岗位", 6, 14)
    _jobs(db, "AI研发工程师", 16, 1)
    result = kt.run(db, config, tmp_path, rng=random.Random(1))
    assert result["dropped"] == ["AI研发工程师"]
    assert "AI研发工程师" not in result["config"]["search"]["keywords"]
    assert db.execute("SELECT 1 FROM keyword_tiers WHERE keyword = 'AI研发工程师'").fetchone() is None
    later = kt.run(db, result["config"], tmp_path, rng=random.Random(1))
    assert later["started"] == []                                              # in its cooldown


def test_a_trial_that_brings_nothing_in_ten_days_ends(db, tmp_path):
    _seen(db, "AI研发工程师", 8)
    config = kt.run(db, _config("Agent开发工程师", slots=1), tmp_path, rng=random.Random(1), today=date.today() - timedelta(days=11))["config"]
    result = kt.run(db, config, tmp_path, rng=random.Random(1))
    assert result["dropped"] == ["AI研发工程师"] and "几乎没有产出" in kt.state(tmp_path)["history"][-1]["reason"]


def test_a_keyword_the_user_removed_is_forgotten_not_judged(db, tmp_path):
    _start_one(db, tmp_path)
    result = kt.run(db, _config("Agent开发工程师", slots=1), tmp_path, rng=random.Random(1))       # the config no longer has the trial word
    assert result["dropped"] == [] and result["kept"] == []
    assert kt.state(tmp_path)["history"][0]["result"] == "removed"


def test_the_keywords_of_the_user_are_never_removed_by_a_trial(db, tmp_path):
    config = _start_one(db, tmp_path)
    _scored(db, "别的岗位", 6, 14)
    _jobs(db, "Agent开发工程师", 30, 0)                                         # bad numbers for the user's own word
    _jobs(db, "AI研发工程师", 16, 1)
    result = kt.run(db, config, tmp_path, rng=random.Random(1))
    assert "Agent开发工程师" in result["config"]["search"]["keywords"]
