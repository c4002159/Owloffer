import tempfile
from pathlib import Path

import pytest

from bosshunter import rejected_companies as rc
from bosshunter.db import get_db, insert_job_if_new, update_job_dual_score, update_job_score, update_job_status


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr("bosshunter.db.DB_PATH", path)
    conn = get_db(path)
    yield conn
    conn.close()


def _job(db, job_id, company, status="ready", score=80):
    insert_job_if_new(db, {"id": job_id, "title": f"岗位{job_id}", "company": company, "jd": "x" * 200})
    update_job_score(db, job_id, score, "ok")
    update_job_dual_score(db, job_id, score, 100, "d")
    update_job_status(db, job_id, status)


def test_add_list_remove_and_variants_count_as_one_company(db):
    assert rc.add_rejected(db, ["华为技术有限公司", "  ", "上海华为", "美团"]) == ["华为技术有限公司", "美团"]
    names = rc.rejected_names(db)
    assert rc.is_rejected_company("华为技术", names) == "华为技术有限公司"
    assert rc.is_rejected_company("上海华为技术有限公司", names) == "华为技术有限公司"
    assert rc.is_rejected_company("字节跳动", names) is None
    # The short brand name is already covered by the longer remembered name, so it is not stored twice.
    assert rc.add_rejected(db, ["华为"]) == []

    assert rc.remove_rejected(db, "美团") is True and rc.remove_rejected(db, "美团") is False
    assert [item["company"] for item in rc.list_rejected(db)] == ["华为技术有限公司"]


def test_marking_jobs_rejects_them_and_hides_other_waiting_jobs_of_the_company(db):
    _job(db, "sent1", "华为技术有限公司", status="sent")
    _job(db, "wait1", "上海华为", status="ready")
    _job(db, "appr1", "华为", status="approved")
    _job(db, "other", "美团", status="ready")
    result = rc.mark_jobs_company_rejected(db, ["sent1"])
    assert result["companies"] == ["华为技术有限公司"] and result["marked_jobs"] == 1
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"sent1": "rejected", "wait1": "filtered", "appr1": "filtered", "other": "ready"}
    assert rc.mark_jobs_company_rejected(db, []) == {"companies": [], "marked_jobs": 0, "hidden_jobs": 0}


def test_apply_rejected_companies_filters_new_jobs_without_touching_sent_ones(db):
    rc.add_rejected(db, ["美团"])
    _job(db, "new", "美团科技", status="pending")
    _job(db, "done", "美团", status="sent")
    assert rc.apply_rejected_companies(db) == 1
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"new": "filtered", "done": "sent"}
    assert rc.apply_rejected_companies(db) == 0


def test_collection_skips_rejected_companies():
    from bosshunter.collection.base import CollectorHooks
    from bosshunter.collection.models import JobCandidate, PlatformCollectionResult
    from bosshunter.collection.orchestrator import CollectionOrchestrator
    from bosshunter.collection.registry import CollectorRegistry

    def candidate(source_id, company):
        return JobCandidate(
            platform="boss", source_job_id=source_id, title="Agent工程师", company=company,
            city="上海", city_code="101020100", jd="负责岗位相关工作", url=f"/job_detail/{source_id}.html",
        )

    class Collector:
        def collect(self, _request, hooks: CollectorHooks):
            for item in (candidate("a", "华为技术有限公司"), candidate("b", "新公司")):
                if hooks.on_list_candidate(item):
                    hooks.on_candidate(item)
            return PlatformCollectionResult("boss", "completed", "search_exhausted", "ok")

    options = {
        "platform_order": ["boss"],
        "auto_score": False,
        "platforms": {"boss": {
            "keywords": ["AI"], "cities": ["上海"], "city_codes": {"上海": "101020100"},
            "max_pages": 1, "sort": "default",
        }},
    }
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "jobs.db"
        seed = get_db(db_path)
        rc.add_rejected(seed, ["华为"])
        seed.close()
        result = CollectionOrchestrator({}, db_path=db_path, registry=CollectorRegistry({"boss": Collector})).run(options)
        conn = get_db(db_path)
        companies = [row["company"] for row in conn.execute("SELECT company FROM jobs")]
        conn.close()
    assert companies == ["新公司"] and result["platforms"]["boss"]["filtered"] == 1
