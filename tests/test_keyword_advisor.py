from datetime import date, datetime, timedelta

import pytest

from bosshunter import keyword_advisor as adv
from bosshunter import keyword_strategy as ks
from bosshunter.db import get_db, insert_job, update_job_status


@pytest.fixture
def db(tmp_path):
    conn = get_db(tmp_path / "t.db")
    ks.ensure_tables(conn)
    yield conn
    conn.close()


def _jobs(db, keyword, total, passed, *, days_ago=0, title=None, prefix=None):
    prefix = prefix or f"{keyword}-{days_ago}"
    for index in range(total):
        job_id = f"{prefix}-{index}"
        insert_job(db, {"id": job_id, "title": title or f"岗位{job_id}", "company": f"公司{job_id}", "salary": "", "city": "上海", "experience": "",
                        "jd": "x" * 100, "hr_name": "", "hr_title": "", "url": "", "hr_active": "", "source_keyword": keyword})
        update_job_status(db, job_id, "ready" if index < passed else "filtered")
        db.execute("UPDATE jobs SET created_at = datetime('now', ?) WHERE id = ?", (f"-{days_ago} day", job_id))
    db.commit()


def _config(*words):
    return {"search": {"keywords": list(words)}}


def _plan(*core):
    return {"core": list(core), "expand": [], "explore": []}


def test_nothing_to_say_when_the_keywords_do_well(db):
    _jobs(db, "AI", 20, 8, days_ago=0)
    _jobs(db, "AI", 20, 8, days_ago=1)
    result = adv.advise(db, _config("AI"), _plan("AI"))
    assert result["level3"] is False and result["suggestions"] == []


def test_core_keywords_that_pass_under_ten_percent_two_days_running_raise_the_alarm(db):
    _jobs(db, "AI", 20, 1, days_ago=0)
    _jobs(db, "AI", 20, 0, days_ago=1)
    result = adv.advise(db, _config("AI"), _plan("AI"))
    assert result["level3"] and result["reasons"][0]["code"] == "core_low_pass"
    assert {"kind": "pause", "keyword": "AI"} == {k: result["suggestions"][0][k] for k in ("kind", "keyword")}


def test_one_bad_day_alone_is_not_enough(db):
    _jobs(db, "AI", 40, 0, days_ago=0)
    _jobs(db, "AI", 20, 10, days_ago=1)
    assert adv.advise(db, _config("AI"), _plan("AI"))["level3"] is False


def test_too_few_jobs_never_trigger_it(db):
    _jobs(db, "AI", 8, 0, days_ago=0)
    _jobs(db, "AI", 8, 0, days_ago=1)                     # 16 jobs in all: below the 30 needed
    assert adv.advise(db, _config("AI"), _plan("AI"))["level3"] is False


def test_half_of_the_keywords_dead_raises_the_alarm_and_suggests_pausing_them(db):
    words = ["a", "b", "c", "d"]
    for word in ("a", "b"):
        _jobs(db, word, 16, 0)
    for word in ("c", "d"):
        _jobs(db, word, 16, 6)
    result = adv.advise(db, _config(*words), {"core": ["c", "d"], "expand": ["a", "b"], "explore": []})
    assert any(reason["code"] == "mostly_dead" for reason in result["reasons"])
    assert {s["keyword"] for s in result["suggestions"] if s["kind"] == "pause"} == {"a", "b"}


def test_two_full_days_with_few_new_jobs_is_reported(db):
    ks.record_run(db, "boss", "AI", "fulltime", seen=200, new=12, filtered=100)
    ks.record_run(db, "boss", "AI", "fulltime", seen=200, new=9, filtered=100)
    db.execute("UPDATE keyword_runs SET run_at = datetime('now', '-1 day') WHERE id = 1")
    db.execute("UPDATE keyword_runs SET run_at = datetime('now', '-2 day') WHERE id = 2")
    db.commit()
    result = adv.advise(db, _config("AI"), _plan("AI"))
    assert any(reason["code"] == "few_new" for reason in result["reasons"])


def test_a_title_that_passed_often_and_no_keyword_covers_is_suggested_as_new_keyword(db):
    _jobs(db, "AI", 6, 6, title="Agent开发工程师（上海）", prefix="agent")
    result = adv.advise(db, _config("大模型"), _plan("大模型"))
    assert result["level3"]
    assert {"kind": "add", "keyword": "Agent开发工程师"}.items() <= result["suggestions"][0].items()


def test_a_title_a_keyword_already_covers_is_not_suggested(db):
    _jobs(db, "AI", 6, 6, title="AI应用开发工程师", prefix="cover")
    result = adv.advise(db, _config("AI应用开发"), _plan("AI应用开发"))
    assert [s for s in result["suggestions"] if s["kind"] == "add"] == []


def test_apply_pauses_and_adds_then_undo_restores_both(db, tmp_path):
    state = tmp_path / "advice.json"
    config = _config("AI", "旧词")
    done = adv.apply(db, config, state, [{"kind": "pause", "keyword": "旧词"}, {"kind": "add", "keyword": "Agent开发工程师"}])
    assert [c["kind"] for c in done["changes"]] == ["pause", "add"]
    assert done["config"]["search"]["keywords"] == ["AI", "旧词", "Agent开发工程师"]
    tiers = ks.current_tiers(db, ["AI", "旧词"], None)
    assert tiers["paused"] == ["旧词"]
    assert adv.last_applied(state)["changes"]

    back = adv.undo(db, done["config"], state)
    assert back["config"]["search"]["keywords"] == ["AI", "旧词"]
    assert ks.current_tiers(db, ["AI", "旧词"], None)["paused"] == []
    assert adv.last_applied(state) is None


def test_applying_the_same_keyword_twice_does_not_duplicate_it(db, tmp_path):
    state = tmp_path / "advice.json"
    first = adv.apply(db, _config("AI"), state, [{"kind": "add", "keyword": "Agent开发工程师"}])
    second = adv.apply(db, first["config"], state, [{"kind": "add", "keyword": "agent开发工程师"}])
    assert second["changes"] == [] and second["config"]["search"]["keywords"] == ["AI", "Agent开发工程师"]


def test_the_popup_respects_the_setting_and_a_snooze(tmp_path):
    state = tmp_path / "advice.json"
    advice = {"level3": True, "suggestions": [{"id": "pause:a", "kind": "pause", "keyword": "a", "reason": ""}]}
    now = datetime(2026, 10, 23, 12, 0, 0)
    assert adv.popup_wanted(state, {}, advice, now) is True
    assert adv.popup_wanted(state, {"automation": {"keyword_advice_popup": False}}, advice, now) is False
    assert adv.popup_wanted(state, {}, {"level3": True, "suggestions": []}, now) is False
    adv.snooze(state, 24, now)
    assert adv.popup_wanted(state, {}, advice, now + timedelta(hours=23)) is False
    assert adv.popup_wanted(state, {}, advice, now + timedelta(hours=25)) is True


def test_a_new_keyword_goes_into_every_platform_list_and_undo_removes_it_from_all(db, tmp_path):
    state = tmp_path / "advice.json"
    config = {"search": {"keywords": ["AI"]}, "platforms": {"boss": {"search": {"keywords": ["AI"]}}, "liepin": {"search": {"keywords": ["AI"]}}, "zhilian": {"search": {}}}}
    done = adv.apply(db, config, state, [{"kind": "add", "keyword": "Agent开发工程师"}])
    new = done["config"]
    assert new["search"]["keywords"] == ["AI", "Agent开发工程师"]
    assert new["platforms"]["boss"]["search"]["keywords"] == ["AI", "Agent开发工程师"] == new["platforms"]["liepin"]["search"]["keywords"]
    assert "keywords" not in new["platforms"]["zhilian"]["search"]              # a platform without its own list keeps using the shared one
    back = adv.undo(db, new, state)["config"]
    assert back["search"]["keywords"] == back["platforms"]["boss"]["search"]["keywords"] == ["AI"]
