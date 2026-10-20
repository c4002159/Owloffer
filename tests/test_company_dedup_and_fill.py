import pytest

from bosshunter import fill as fill_module
from bosshunter import review_queue as rq
from bosshunter.company_dedup import (
    approved_today, company_key, duplicate_of_approved_today, same_company,
)
from bosshunter.db import (
    add_history, get_db, insert_job_if_new, update_job_dual_score, update_job_score, update_job_status,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr("bosshunter.db.DB_PATH", path)
    conn = get_db(path)
    yield conn
    conn.close()


def _job(db, job_id, company, score=80, status="ready"):
    insert_job_if_new(db, {"id": job_id, "title": f"岗位{job_id}", "company": company, "jd": "x" * 200})
    update_job_score(db, job_id, score, "ok")
    update_job_dual_score(db, job_id, score, 100, "d")
    update_job_status(db, job_id, status)


@pytest.mark.parametrize("a,b", [
    ("华为", "华为技术有限公司"),
    ("上海华为技术有限公司", "华为"),
    ("华为云计算", "华为"),
    ("万宝盛华", "上海万宝盛华信息科技"),
    ("上海万宝盛华", "万宝盛华信息科技有限公司"),
])
def test_company_variants_are_the_same(a, b):
    assert same_company(a, b)


@pytest.mark.parametrize("a,b", [
    ("华为", "美团"),
    ("字节跳动", "跳动网络"),
    ("", "华为"),
    ("某", "某某科技有限公司"),
    ("中国银行", "招商银行"),
    ("上海银行", "招商银行"),
    ("中国平安", "中国银行"),
])
def test_different_companies_are_not_merged(a, b):
    assert not same_company(a, b)


def test_company_key_strips_region_and_legal_suffix():
    assert company_key("上海华为技术有限公司") == "华为技术"
    assert company_key("中国") == "中国"  # never reduce a name to nothing
    assert company_key("中国银行") == "中国银行"  # 中国 is part of the name, not a region


def test_approved_today_and_duplicate_detection(db):
    _job(db, "a", "华为技术有限公司")
    _job(db, "b", "上海华为")
    _job(db, "c", "美团")
    rq.approve_jobs(db, ["a"])
    assert [x["company"] for x in approved_today(db)] == ["华为技术有限公司"]
    assert duplicate_of_approved_today(db, {"id": "b", "company": "上海华为"}) == "华为技术有限公司"
    assert duplicate_of_approved_today(db, {"id": "c", "company": "美团"}) is None
    assert duplicate_of_approved_today(db, {"id": "a", "company": "华为技术有限公司"}) is None


def test_old_approvals_do_not_count_as_today(db):
    _job(db, "a", "华为技术有限公司")
    rq.approve_jobs(db, ["a"])
    db.execute("UPDATE history SET created_at = datetime('now', '-5 day') WHERE job_id = 'a'")
    db.commit()
    assert approved_today(db) == []


def test_apply_company_dedup_filters_second_job_of_same_company(db):
    _job(db, "a", "华为技术有限公司", 90)
    _job(db, "b", "华为", 85)
    _job(db, "c", "美团", 80)
    rq.approve_jobs(db, ["a"])
    assert rq.apply_company_dedup(db) == 1
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"a": "approved", "b": "filtered", "c": "ready"}
    assert rq.apply_company_dedup(db) == 0


def test_approve_all_keeps_best_job_per_company(db):
    config = {"automation": {"review_min_score": 60}}
    _job(db, "h1", "华为", 70)
    _job(db, "h2", "华为技术有限公司", 90)
    _job(db, "h3", "上海华为", 80)
    _job(db, "m1", "美团", 75)
    _job(db, "low", "某低分公司", 50)
    approved, duplicates = rq.approve_all(db, config)
    assert (approved, duplicates) == (2, 2)
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"h1": "filtered", "h2": "approved", "h3": "filtered", "m1": "approved", "low": "ready"}


def test_approve_all_skips_companies_already_approved_today(db):
    config = {"automation": {"review_min_score": 60}}
    _job(db, "old", "美团", 90, status="ready")
    rq.approve_jobs(db, ["old"])
    _job(db, "new", "美团科技", 95)
    assert rq.approve_all(db, config) == (0, 1)


def test_fill_collects_until_cap_and_reports(db, monkeypatch):
    config = {"search": {"keywords": ["A", "B", "C"]}, "automation": {"review_cap": 4, "review_min_score": 60}}
    calls = []

    def fake_scrape(cfg, keywords, limit=None, **_):
        calls.append(keywords[0])
        for i in range(2):
            _job(db, f"{keywords[0]}{i}", f"公司{keywords[0]}{i}", 80, status="pending")
        cfg["_workbench_collect_report"] = {"stop_reason": "callback_stopped"}
        return 2

    def fake_score(cfg, **_):
        db.execute("UPDATE jobs SET status = 'ready' WHERE status = 'pending'")
        db.commit()

    monkeypatch.setattr("bosshunter.scraper.jobs.scrape_jobs", fake_scrape)
    monkeypatch.setattr("bosshunter.ai.scorer.score_jobs", fake_score)
    lines = []
    summary = fill_module.fill_review_list(config, lines.append)
    assert calls == ["A", "B"]  # stops as soon as the cap is reached
    assert summary["waiting"] == 4 and summary["collected"] == 4 and summary["stopped"] == ""


def test_fill_stops_on_safety_stop(db, monkeypatch):
    config = {"search": {"keywords": ["A", "B"]}, "automation": {"review_cap": 50}}

    def fake_scrape(cfg, keywords, limit=None, **_):
        cfg["_workbench_collect_report"] = {"stop_reason": "captcha"}
        return 0

    monkeypatch.setattr("bosshunter.scraper.jobs.scrape_jobs", fake_scrape)
    monkeypatch.setattr("bosshunter.ai.scorer.score_jobs", lambda cfg, **_: None)
    summary = fill_module.fill_review_list(config, lambda _m: None)
    assert "captcha" in summary["stopped"]


def test_fill_without_keywords_or_when_full(db):
    assert "关键词" in fill_module.fill_review_list({"search": {"keywords": []}}, lambda _m: None)["stopped"]
    _job(db, "a", "公司A", 80)
    full = fill_module.fill_review_list(
        {"search": {"keywords": ["A"]}, "automation": {"review_cap": 1}}, lambda _m: None
    )
    assert full["stopped"] == "待审查岗位已达上限"
