from datetime import datetime, timedelta, timezone

import pytest

from bosshunter import fill as fill_module
from bosshunter import review_queue as rq
from bosshunter.ai.prefilter import quick_score
from bosshunter.company_dedup import approved_within, cooldown_days, duplicate_of_approved_today
from bosshunter.db import (
    get_db, insert_job_if_new, update_job_dual_score, update_job_score, update_job_status,
)

CONFIG = {"automation": {"review_cap": 5, "review_min_score": 60, "internship_quota": 2}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr("bosshunter.db.DB_PATH", path)
    conn = get_db(path)
    yield conn
    conn.close()


def _job(db, job_id, title="Agent工程师", company=None, score=80, status="ready"):
    insert_job_if_new(db, {"id": job_id, "title": title, "company": company or f"公司{job_id}", "jd": "x" * 200})
    update_job_score(db, job_id, score, "ok")
    update_job_dual_score(db, job_id, score, 100, "d")
    update_job_status(db, job_id, status)


def test_quotas_split_the_cap():
    assert rq.kind_quotas(CONFIG) == {"internship": 2, "fulltime": 3}
    assert rq.kind_quotas({}) == {"internship": 0, "fulltime": 150}
    assert rq.automation_settings({"automation": {"review_cap": 10, "internship_quota": 99}})["internship_quota"] == 10


def test_job_kind_needs_positive_internship_evidence():
    assert rq.job_kind({"title": "AI开发实习生", "jd": ""}) == "internship"
    assert rq.job_kind({"title": "产品实习生（可转正）", "jd": ""}) == "internship"
    assert rq.job_kind({"title": "Agent工程师", "jd": "有实习经验优先"}) == "fulltime"
    assert rq.job_kind({"title": "非实习 开发", "jd": ""}) == "fulltime"


def test_kind_full_per_kind(db):
    _job(db, "f1")
    _job(db, "f2")
    _job(db, "f3")
    _job(db, "i1", title="AI实习生")
    fulltime = {"title": "x", "jd": ""}
    intern = {"title": "AI产品实习生", "jd": ""}
    assert rq.waiting_by_kind(db, CONFIG) == {"fulltime": 3, "internship": 1}
    assert rq.kind_full(db, CONFIG, fulltime) is True
    assert rq.kind_full(db, CONFIG, intern) is False
    _job(db, "i2", title="大模型实习生")
    assert rq.kind_full(db, CONFIG, intern) is True


def test_kind_full_without_quota_falls_back_to_total_cap(db):
    config = {"automation": {"review_cap": 2, "review_min_score": 60}}
    _job(db, "a")
    assert rq.kind_full(db, config, {"title": "x", "jd": ""}) is False
    _job(db, "b")
    assert rq.kind_full(db, config, {"title": "x", "jd": ""}) is True


def test_prefilter_accepts_internships_and_skips_monthly_salary_floor():
    config = {
        "automation": {"internship_quota": 30},
        "profile": {"salary_min": 15, "salary_max": 25, "filter_unparsed_salary": True},
    }
    intern = {"title": "AI开发实习生", "company": "星河网络科技有限公司", "salary": "200-300元/天", "jd": ""}
    assert quick_score(intern, config)[0] == 100
    fulltime_low = {"title": "后端工程师", "company": "星河网络科技有限公司", "salary": "5-8K", "jd": ""}
    assert quick_score(fulltime_low, config)[0] == 0
    no_quota = {"profile": {"salary_min": 15}}
    assert quick_score(intern, no_quota)[0] == 0  # without a quota internships stay rejected


def test_cooldown_window_covers_previous_days(db):
    _job(db, "old", company="华为技术有限公司")
    rq.approve_jobs(db, ["old"])
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE history SET created_at = ? WHERE job_id = 'old'", (yesterday,))
    db.commit()
    new = {"id": "n", "company": "上海华为"}
    assert duplicate_of_approved_today(db, new, 1) is None
    assert duplicate_of_approved_today(db, new, 3) == "华为技术有限公司"
    assert [x["id"] for x in approved_within(db, 2)] == ["old"]
    assert cooldown_days({"automation": {"company_cooldown_days": 7}}) == 7
    assert cooldown_days({}) == 1 and cooldown_days({"automation": {"company_cooldown_days": "x"}}) == 1


def test_fill_runs_both_phases_and_uses_internship_filter(db, monkeypatch):
    config = {
        "search": {"keywords": ["AI工程师"], "intern_keywords": ["AI实习生"]},
        "automation": {"review_cap": 3, "review_min_score": 60, "internship_quota": 1},
    }
    seen = []

    def fake_scrape(cfg, keywords, limit=None, **_):
        seen.append((keywords[0], cfg["search"].get("filters"), cfg.get("profile", {}).get("allow_internship")))
        intern = keywords[0] == "AI实习生"
        for i in range(2 if not intern else 1):
            _job(db, f"{keywords[0]}{i}", title="AI开发实习生" if intern else "AI工程师", status="pending")
        cfg["_workbench_collect_report"] = {"stop_reason": "search_exhausted"}
        return 1

    def fake_score(cfg, **_):
        db.execute("UPDATE jobs SET status = 'ready' WHERE status = 'pending'")
        db.commit()

    monkeypatch.setattr("bosshunter.scraper.jobs.scrape_jobs", fake_scrape)
    monkeypatch.setattr("bosshunter.ai.scorer.score_jobs", fake_score)
    summary = fill_module.fill_review_list(config, lambda _m: None)
    assert [s[0] for s in seen] == ["AI工程师", "AI实习生"]
    assert seen[0][1] is None and seen[0][2] is None  # full-time pass keeps the normal filters
    assert seen[1][1] == {"job_type": ["实习"]} and seen[1][2] is True
    assert summary["by_kind"] == {"fulltime": 2, "internship": 1} and summary["stopped"] == ""


def test_fill_skips_a_phase_without_keywords(db, monkeypatch):
    config = {
        "search": {"keywords": ["A"], "intern_keywords": []},
        "automation": {"review_cap": 2, "internship_quota": 1},
    }
    calls = []
    monkeypatch.setattr("bosshunter.scraper.jobs.scrape_jobs", lambda cfg, kw, limit=None, **_: calls.append(kw[0]) or 0)
    monkeypatch.setattr("bosshunter.ai.scorer.score_jobs", lambda cfg, **_: None)
    summary = fill_module.fill_review_list(config, lambda _m: None)
    assert calls == ["A"] and "没有更多新岗位" in summary["stopped"]
