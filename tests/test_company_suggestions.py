from datetime import date, timedelta

import pytest

from bosshunter.company_suggestions import block_companies, company_suggestions
from bosshunter.db import get_db, insert_job_if_new, record_sighting, update_job_status


@pytest.fixture
def db(tmp_path):
    conn = get_db(tmp_path / "jobs.db")
    yield conn
    conn.close()


def _job(db, job_id, company, title="Agent工程师", status="ready"):
    insert_job_if_new(db, {"id": job_id, "title": title, "company": company, "jd": "x" * 200})
    update_job_status(db, job_id, status)


def test_suggestions_explain_each_company_and_skip_blocked(db):
    for i in range(3):
        _job(db, f"m{i}", "多岗公司", title=f"岗位{i}")
    _job(db, "d1", "重复公司")
    _job(db, "d2", "重复公司")
    _job(db, "l1", "长挂公司")
    for offset in range(4):
        record_sighting(db, "l1", date.today() - timedelta(days=offset))
    _job(db, "ok", "正常公司")
    result = {item["company"]: item["reasons"] for item in company_suggestions(db)}
    assert result["多岗公司"] == ["同公司3岗"]
    assert result["重复公司"] == ["重复发布×2"]
    assert result["长挂公司"] == ["连续在招4天"]
    assert "正常公司" not in result
    assert "多岗公司" not in {item["company"] for item in company_suggestions(db, ["多岗"])}


def test_outsourcing_hits_rank_first(db):
    _job(db, "o1", "外包公司")
    db.execute("UPDATE jobs SET outsourcing_level = 'confirmed' WHERE id = 'o1'")
    db.commit()
    for i in range(3):
        _job(db, f"m{i}", "多岗公司", title=f"岗位{i}")
    ranked = company_suggestions(db)
    assert ranked[0]["company"] == "外包公司" and ranked[0]["reasons"] == ["外包(确认)"]


def test_block_companies_updates_config_and_hides_waiting_jobs(db):
    _job(db, "a", "坏公司")
    _job(db, "b", "坏公司", status="sent")
    _job(db, "c", "好公司")
    config = {"profile": {"blocked_companies": ["旧规则"]}}
    assert block_companies(db, config, ["坏公司", "  ", "旧规则公司"]) == ["坏公司"]
    assert config["profile"]["blocked_companies"] == ["旧规则", "坏公司"]
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"a": "skipped", "b": "sent", "c": "ready"}
    assert block_companies(db, config, ["坏公司"]) == []
