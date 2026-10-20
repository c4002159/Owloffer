import json

import pytest

from bosshunter import review_queue as rq
from bosshunter.db import (
    add_history, get_db, insert_job_if_new, update_job_dual_score, update_job_score, update_job_status,
)


@pytest.fixture
def db(tmp_path):
    conn = get_db(tmp_path / "jobs.db")
    yield conn
    conn.close()


def _job(db, job_id, score, *, status="ready", keyword="Agent工程师"):
    insert_job_if_new(db, {
        "id": job_id, "title": f"岗位{job_id}", "company": f"公司{job_id}", "salary": "20-30K",
        "jd": "x" * 200, "source_keyword": keyword,
    })
    update_job_score(db, job_id, score, "ok")
    update_job_status(db, job_id, status)


def test_review_pending_filters_by_score_and_status(db):
    _job(db, "a", 80)
    _job(db, "b", 59)
    _job(db, "c", 90, status="approved")
    assert [j["id"] for j in rq.review_pending(db, 60)] == ["a"]
    assert [j["id"] for j in rq.approved_unsent(db)] == ["c"]


def test_cap_counts_pending_and_approved(db):
    config = {"automation": {"review_cap": 3, "review_min_score": 60}}
    _job(db, "a", 80)
    _job(db, "b", 70, status="approved")
    assert not rq.review_cap_reached(db, config)
    _job(db, "c", 65)
    assert rq.review_cap_reached(db, config)


def test_automation_defaults_keep_auto_send_off():
    settings = rq.automation_settings({})
    assert settings == {"auto_send": False, "review_cap": 150, "review_min_score": 60, "internship_quota": 0}
    assert rq.automation_settings({"automation": {"review_cap": "bad"}})["review_cap"] == 150


def test_approve_and_skip_only_touch_reviewable_jobs(db):
    _job(db, "a", 80)
    _job(db, "b", 80)
    _job(db, "sent", 80, status="sent")
    assert rq.approve_jobs(db, ["a", "sent", "missing"]) == 1
    assert rq.skip_jobs(db, ["b", "sent"]) == 1
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"a": "approved", "b": "skipped", "sent": "sent"}


def test_categories_come_from_keyword_plan_directions(db):
    plan = {"directions": [{"name": "Agent/AI应用", "keywords": ["Agent工程师", "RAG工程师"]}]}
    _job(db, "a", 80, keyword="agent工程师")
    _job(db, "b", 80, keyword="完全不相关")
    assert rq.assign_categories(db, plan) == 2
    cats = {r["id"]: r["category"] for r in db.execute("SELECT id, category FROM jobs")}
    assert cats == {"a": "Agent/AI应用", "b": rq.UNCATEGORIZED}


def _pending_reply(db, job_id, question="何时方便面试？", reply="明天下午都可以"):
    detail = json.dumps({"schema": "reply_pending.v1", "hr_question": question, "ai_reply": reply}, ensure_ascii=False)
    add_history(db, job_id, "reply_pending", detail)


def test_reply_draft_approval_flow(db):
    _job(db, "a", 80, status="sent")
    _pending_reply(db, "a")
    drafts = rq.pending_reply_drafts(db)
    assert len(drafts) == 1 and drafts[0]["ai_reply"] == "明天下午都可以"

    assert not rq.approve_reply(db, drafts[0]["history_id"], "  ")
    assert rq.approve_reply(db, drafts[0]["history_id"], "明天下午三点可以，谢谢")
    assert rq.pending_reply_drafts(db) == []

    approved = rq.latest_approved_reply(db, "a")
    assert approved["message"] == "明天下午三点可以，谢谢"
    assert approved["pending_history_id"] == drafts[0]["history_id"]

    add_history(db, "a", "replied", "{}")
    assert rq.latest_approved_reply(db, "a") is None


def test_stale_draft_cannot_be_approved(db):
    _job(db, "a", 80, status="sent")
    _pending_reply(db, "a", reply="旧")
    old_id = rq.pending_reply_drafts(db)[0]["history_id"]
    _pending_reply(db, "a", reply="新")
    assert not rq.approve_reply(db, old_id, "旧内容")


def test_group_by_category_orders_groups_by_best_score():
    from bosshunter.ui.review_window import group_by_category

    jobs = [
        {"id": "1", "category": "IT管理", "score": 70},
        {"id": "2", "category": "Agent", "score": 90},
        {"id": "3", "category": "Agent", "score": 95},
        {"id": "4", "category": None, "score": 60},
    ]
    groups = group_by_category(jobs)
    assert list(groups) == ["Agent", "IT管理", rq.UNCATEGORIZED]
    assert [j["id"] for j in groups["Agent"]] == ["3", "2"]


def test_match_gate_filters_weak_matches_even_with_high_blended_score(db):
    config = {"scoring": {"min_match_score": 50}}
    _job(db, "weak", 62)
    _job(db, "strong", 62)
    update_job_dual_score(db, "weak", 35, 88, "d")
    update_job_dual_score(db, "strong", 72, 40, "d")
    assert rq.apply_match_gate(db, config) == 1
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"weak": "filtered", "strong": "ready"}
    assert rq.apply_match_gate(db, config) == 0
    assert rq.apply_match_gate(db, {}) == 0


def test_passes_match_gate_boundaries():
    from bosshunter.ai.recruit_score import min_match_score, passes_match_gate

    config = {"scoring": {"min_match_score": 50}}
    assert passes_match_gate(50, config) and not passes_match_gate(49, config)
    assert passes_match_gate(0, {})
    assert min_match_score({"scoring": {"min_match_score": "bad"}}) == 0
    assert min_match_score({"scoring": {"min_match_score": 500}}) == 100
