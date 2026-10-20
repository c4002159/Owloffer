import io
import json
import tempfile
from pathlib import Path

import pytest

from bosshunter.db import get_db, insert_job, update_job_greeting, update_job_status
from bosshunter.web import server


def _job(job_id):
    return {
        "id": job_id, "title": "Agent工程师", "company": f"公司{job_id}", "salary": "20-30K", "city": "上海",
        "experience": "不限", "jd": "做 Agent", "hr_name": "HR", "hr_title": "HR", "url": f"/job_detail/{job_id}.html",
    }


def _request(path, method="GET", json_body=None):
    headers = {}

    def start_response(status, response_headers, exc_info=None):
        headers["status"] = status

    raw = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    path, _, query = path.partition("?")
    environ = {
        "REMOTE_ADDR": "127.0.0.1", "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
        "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8686", "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(raw), "wsgi.errors": io.StringIO(), "wsgi.multithread": False,
        "wsgi.multiprocess": False, "wsgi.run_once": False,
    }
    if json_body is not None:
        environ["CONTENT_LENGTH"] = str(len(raw))
        environ["CONTENT_TYPE"] = "application/json"
    body = b"".join(
        chunk if isinstance(chunk, bytes) else chunk.encode("utf-8") for chunk in server.app(environ, start_response)
    ).decode("utf-8")
    return headers["status"], json.loads(body)


@pytest.fixture
def base_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        db = get_db(base / "data" / "bosshunter.db")
        for job_id, status in (
            ("ok1", "approved"), ("ok2", "ready"), ("bad1", "filtered"), ("bad2", "skipped"), ("sent1", "sent"),
        ):
            insert_job(db, _job(job_id))
            update_job_greeting(db, job_id, "您好")
            update_job_status(db, job_id, status)
        db.close()
        server.set_base_dir(base)
        started = []

        def fake_start(mode, config, before_start=None):
            started.append((mode, list(config.get("_workbench_job_ids", []))))
            return {"id": "task-1", "mode": mode, "status": "running", "logs": []}

        monkeypatch.setattr(server.task_runner, "start", fake_start)
        monkeypatch.setattr(server.task_runner, "status", lambda: {"active": None, "last_task": None, "tasks": []})
        monkeypatch.setattr("bosshunter.ai.greeter.greeting_config_error", lambda config: "")
        yield base, started


def test_mixed_selection_without_skip_invalid_still_fails_as_before(base_dir):
    status, payload = _request("/api/workbench/deliver", "POST", {"job_ids": ["ok1", "bad1"]})
    assert status.startswith("409") and payload["not_ready_ids"] == ["bad1"]


def test_skip_invalid_delivers_only_deliverable_jobs_and_reports_the_rest(base_dir):
    _, started = base_dir
    status, payload = _request(
        "/api/workbench/deliver", "POST",
        {"job_ids": ["ok1", "ok2", "bad1", "bad2", "sent1"], "skip_invalid": True},
    )
    assert status.startswith("200"), payload
    assert started == [("deliver", ["ok1", "ok2"])]
    assert payload["skipped_count"] == 3
    assert payload["skipped_by_status"] == {"filtered": 1, "skipped": 1, "sent": 1}


def test_skip_invalid_with_nothing_deliverable_explains_why(base_dir):
    status, payload = _request("/api/workbench/deliver", "POST", {"job_ids": ["bad1", "bad2"], "skip_invalid": True})
    assert status.startswith("409") and "没有可投递的岗位" in payload["error"]
    assert payload["skipped_by_status"] == {"filtered": 1, "skipped": 1}


def test_scope_approved_delivers_every_approved_job_without_ticking(base_dir):
    _, started = base_dir
    status, payload = _request("/api/workbench/deliver", "POST", {"scope": "approved"})
    assert status.startswith("200"), payload
    assert started == [("deliver", ["ok1"])]


def test_scope_approved_with_none_approved_returns_a_clear_error(base_dir):
    base, _ = base_dir
    db = get_db(base / "data" / "bosshunter.db")
    update_job_status(db, "ok1", "sent")
    db.close()
    status, payload = _request("/api/workbench/deliver", "POST", {"scope": "approved"})
    assert status.startswith("400") and "没有已通过" in payload["error"]


def test_workbench_payload_lists_approved_jobs_separately_and_keeps_the_pending_contract(base_dir):
    status, payload = _request("/api/workbench")
    assert status.startswith("200")
    assert [job["id"] for job in payload["approved_unsent"]] == ["ok1"]
    # Every fixture job already has a greeting, so none of them waits in the confirmation list; the
    # interrupted-greeting case (approved without greeting) is covered by test_web_api_routes.
    assert payload["pending_confirmation"] == []
    assert [job["id"] for job in payload["pending_greetings"]] == ["ok1", "ok2"] or sorted(
        job["id"] for job in payload["pending_greetings"]
    ) == ["ok1", "ok2"]


def test_rejected_company_routes_round_trip(base_dir):
    status, payload = _request("/api/companies/rejected", "POST", {"job_ids": ["sent1"]})
    assert status.startswith("200") and payload["companies"] == ["公司sent1"] and payload["marked_jobs"] == 1
    status, payload = _request("/api/companies/rejected")
    assert [item["company"] for item in payload["companies"]] == ["公司sent1"]
    status, payload = _request("/api/companies/rejected", "POST", {"job_ids": []})
    assert status.startswith("400")


def test_pool_search_can_hide_other_jobs_of_a_handled_company(base_dir):
    base, _ = base_dir
    db = get_db(base / "data" / "bosshunter.db")
    for job_id, company, status in (
        ("h1", "华为技术有限公司", "sent"),
        ("h2", "上海华为", "pending"),
        ("h3", "华为", "filtered"),
        ("m1", "美团", "pending"),
    ):
        insert_job(db, {**_job(job_id), "company": company})
        update_job_status(db, job_id, status)
    db.close()
    status, everything = _request("/api/jobs/search?limit=50")
    ids = {item["id"] for item in everything["items"]}
    assert {"h1", "h2", "h3", "m1"} <= ids
    status, hidden = _request("/api/jobs/search?limit=50&hide_handled=1")
    hidden_ids = {item["id"] for item in hidden["items"]}
    assert "h1" in hidden_ids and "m1" in hidden_ids          # the handled job itself and unrelated companies stay
    assert "h2" not in hidden_ids and "h3" not in hidden_ids  # other jobs of the handled company are hidden
    assert hidden["total"] == everything["total"] - 2


def test_pool_search_hides_companies_that_rejected_us(base_dir):
    base, _ = base_dir
    db = get_db(base / "data" / "bosshunter.db")
    insert_job(db, {**_job("x1"), "company": "拒绝科技"})
    update_job_status(db, "x1", "pending")
    from bosshunter.rejected_companies import add_rejected

    add_rejected(db, ["拒绝科技"])
    db.close()
    _, hidden = _request("/api/jobs/search?limit=50&hide_handled=1")
    assert "x1" not in {item["id"] for item in hidden["items"]}
    _, shown = _request("/api/jobs/search?limit=50")
    assert "x1" in {item["id"] for item in shown["items"]}
