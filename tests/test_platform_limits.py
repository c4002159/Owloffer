import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from bosshunter.db import add_history, get_db, insert_job_if_new, update_job_status
from bosshunter.executor import sender
from bosshunter.platform_limits import clear_limit, limits_today, mark_limit_reached
from bosshunter.web import server
from tests.test_external_platforms import _request


def test_limit_mark_lasts_for_today_and_can_be_cleared(tmp_path):
    conn = get_db(tmp_path / "t.db")
    assert limits_today(conn) == {}
    mark_limit_reached(conn, "boss", "已达沟通上限")
    assert limits_today(conn) == {"boss": "已达沟通上限"}
    clear_limit(conn, "boss")
    assert limits_today(conn) == {}
    conn.close()


def test_chat_limit_dialog_is_reported_instead_of_a_generic_failure():
    with patch.object(sender, "_detect_chat_limit", return_value="您已达到沟通上限 您今天已与150位BOSS沟通"):
        result = sender._handle_greet_popup("target", "您好")
    assert result["error"] == "chat_limit_reached" and result["skip_backoff"] is True
    assert "150位BOSS" in result["history_detail"]


def test_no_limit_dialog_means_empty_text():
    with patch.object(sender, "evaluate", return_value=json.dumps({"success": True, "text": ""})):
        assert sender._detect_chat_limit("target") == ""
    with patch.object(sender, "evaluate", return_value=json.dumps({"success": True, "text": "您已达到沟通上限"})):
        assert "沟通上限" in sender._detect_chat_limit("target")


@pytest.fixture
def pool(tmp_path):
    conn = get_db(tmp_path / "data" / "bosshunter.db")

    def add(job_id, company, platform, status, score=80):
        insert_job_if_new(conn, {"id": job_id, "title": f"岗位{job_id}", "company": company, "jd": "x" * 100,
                                 "source_platform": platform})
        conn.execute("UPDATE jobs SET status = ?, score = ? WHERE id = ?", (status, score, job_id))
        conn.commit()

    add("z1", "华为技术有限公司", "zhilian", "ready", 90)
    add("z2", "上海华为", "zhilian", "ready", 70)             # same company as z1: only the better one is listed
    add("z3", "美团", "zhilian", "scored")                     # the company was already applied to on BOSS
    add("z4", "小米科技", "zhilian", "approved")
    add("z5", "字节跳动", "zhilian", "sent")                   # already handled: history, not manual list
    add("b1", "美团科技有限公司", "boss", "sent")
    add_history(conn, "b1", "sent")
    add("b2", "腾讯", "boss", "ready")
    conn.close()
    server.set_base_dir(tmp_path)
    return tmp_path


def test_manual_view_lists_filtered_zhilian_jobs_deduplicated_against_the_database(pool):
    status, payload = _request("/api/jobs/search?limit=50&queue_view=manual")
    assert status.startswith("200"), payload
    assert {item["id"] for item in payload["items"]} == {"z1", "z4"}
    status, payload = _request("/api/jobs/search?limit=50&queue_view=confirm")
    assert {item["id"] for item in payload["items"]} == {"b2"}                  # zhilian jobs live in the manual view


def test_collecting_a_platform_that_hit_its_limit_needs_confirmation(pool, monkeypatch):
    conn = get_db(pool / "data" / "bosshunter.db")
    mark_limit_reached(conn, "boss", "今天已与150位BOSS沟通")
    conn.close()
    started = []
    monkeypatch.setattr(server.task_runner, "start", lambda mode, config, **kwargs: started.append(mode) or {"id": "t", "mode": mode, "status": "running", "logs": []})
    monkeypatch.setattr(server, "_preflight_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(server, "normalize_collection_options", lambda config, options: {
        "platform_order": ["boss"], "auto_score": False, "platforms": {"boss": {}}, "quotas": {"fulltime": 1, "internship": 0}})
    monkeypatch.setattr(server.task_runner, "status", lambda: {"active": None, "last_task": None, "tasks": []})
    body = {"mode": "collect", "options": {"platform_order": ["boss"], "auto_score": False}}
    status, payload = _request("/api/workbench/task", "POST", body)
    assert status.startswith("409") and payload["code"] == "platform_limit_today" and "boss" in payload["platforms"]
    assert "确定仍要采集" in payload["error"] and not started
    body["options"]["confirm_platform_limit"] = True
    status, payload = _request("/api/workbench/task", "POST", body)
    assert status.startswith("200") and started == ["collect"]
    status, payload = _request("/api/workbench")
    assert payload["platform_limits_today"] == {"boss": "今天已与150位BOSS沟通"}


def test_a_platform_that_stops_on_an_error_is_paused_for_the_rest_of_the_day(tmp_path):
    from bosshunter.collection.models import PlatformCollectionResult
    from bosshunter.collection.orchestrator import CollectionOrchestrator
    from bosshunter.collection.registry import CollectorRegistry

    class Blocked:
        platform = "liepin"

        def collect(self, request, hooks):
            return PlatformCollectionResult("liepin", "blocked", "rate_limit", "猎聘出现验证或限流信号")

    options = {"platform_order": ["liepin"], "auto_score": False, "platforms": {"liepin": {
        "keywords": ["AI"], "cities": ["上海"], "city_codes": {"上海": "020"}, "max_pages": 1, "sort": "default"}}}
    db_path = tmp_path / "c.db"
    CollectionOrchestrator({}, db_path=db_path, registry=CollectorRegistry({"liepin": Blocked})).run(options)
    conn = get_db(db_path)
    assert "采集异常已暂停" in limits_today(conn)["liepin"]
    conn.close()
