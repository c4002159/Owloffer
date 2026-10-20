import io
import json
import tempfile
from pathlib import Path

import pytest

from bosshunter import fill as fill_module
from bosshunter.db import (
    add_history, get_db, insert_job, mark_external_jobs_sent, update_job_greeting, update_job_status,
)
from bosshunter.web import server

CONFIG = {
    "search": {"keywords": ["Agent工程师", "RAG工程师"]},
    "platforms": {
        "zhilian": {"enabled": True, "search": {"keywords": []}},
        "51job": {"enabled": True, "search": {"keywords": ["AI应用"]}},
        "liepin": {"enabled": False, "search": {"keywords": ["大模型"]}},
    },
    "automation": {"review_cap": 6, "internship_quota": 0},
}


def test_external_plan_uses_enabled_platforms_with_their_own_or_fallback_keywords():
    assert fill_module.external_plan(CONFIG) == [
        ("zhilian", ["Agent工程师", "RAG工程师"]),   # no keywords of its own -> the BOSS ones
        ("51job", ["AI应用"]),
    ]
    assert fill_module.external_plan({"platforms": {"zhilian": {"enabled": False}}}) == []
    assert fill_module.external_plan({}) == []


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr("bosshunter.db.DB_PATH", path)
    conn = get_db(path)
    yield conn
    conn.close()


def _job(db, job_id, platform, status="approved"):
    insert_job(db, {
        "id": job_id, "title": f"岗位{job_id}", "company": f"公司{job_id}", "salary": "20-30K", "city": "上海",
        "experience": "不限", "jd": "x" * 200, "hr_name": "", "hr_title": "", "url": f"https://example.test/{job_id}",
        "source_platform": platform,
    })
    update_job_greeting(db, job_id, "您好")
    update_job_status(db, job_id, status)


def _quiet_fill(monkeypatch, collect):
    monkeypatch.setattr(fill_module, "collect_external", collect)
    monkeypatch.setattr("bosshunter.scraper.jobs.scrape_jobs", lambda *a, **k: 0)
    monkeypatch.setattr(fill_module, "refresh_long_listed", lambda *a, **k: {"stopped": ""})


def test_fill_collects_external_platforms_and_scores_after_each_keyword(db, monkeypatch):
    calls = []

    def fake_collect(cfg, platform, keyword, stop_event):
        calls.append((platform, keyword))
        _job(db, f"{platform}-{keyword}", platform, status="pending")
        return 1, "search_exhausted"

    def fake_score(cfg, **_):
        db.execute("UPDATE jobs SET status = 'ready' WHERE status = 'pending'")
        db.commit()

    _quiet_fill(monkeypatch, fake_collect)
    monkeypatch.setattr("bosshunter.ai.scorer.score_jobs", fake_score)
    config = {**CONFIG, "automation": {"review_cap": 3, "internship_quota": 0, "review_min_score": 0}}
    summary = fill_module.fill_review_list(config, lambda _m: None, max_rounds=1)
    assert calls == [("zhilian", "Agent工程师"), ("zhilian", "RAG工程师"), ("51job", "AI应用")]
    assert summary["collected"] == 3


def test_login_wall_or_bad_config_only_closes_that_platform(db, monkeypatch):
    calls = []

    def fake_collect(cfg, platform, keyword, stop_event):
        calls.append((platform, keyword))
        if platform == "zhilian":
            raise ValueError("智联暂未内置城市编码")
        return 0, "login_required"

    _quiet_fill(monkeypatch, fake_collect)
    monkeypatch.setattr("bosshunter.ai.scorer.score_jobs", lambda cfg, **_: None)
    lines = []
    summary = fill_module.fill_review_list(CONFIG, lines.append, max_rounds=2)
    assert calls == [("zhilian", "Agent工程师"), ("51job", "AI应用")]  # each platform closed after its first problem
    assert any("无法采集" in line for line in lines) and any("需要先登录" in line for line in lines)
    assert "安全停止" not in summary["stopped"]


def test_a_risk_signal_on_an_external_platform_stops_the_whole_fill(db, monkeypatch):
    _quiet_fill(monkeypatch, lambda *a, **k: (0, "captcha"))
    monkeypatch.setattr("bosshunter.ai.scorer.score_jobs", lambda cfg, **_: None)
    summary = fill_module.fill_review_list(CONFIG, lambda _m: None, max_rounds=2)
    assert summary["stopped"] == "智联招聘采集已安全停止：captcha"


# ---- delivery stays with BOSS; the rest is handed to the user -------------------------------------
def _request(path, method="GET", json_body=None):
    headers = {}

    def start_response(status, response_headers, exc_info=None):
        headers["status"] = status

    raw = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    environ = {
        "REMOTE_ADDR": "127.0.0.1", "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "",
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
def mixed(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        conn = get_db(base / "data" / "bosshunter.db")
        _job(conn, "b1", "boss")
        _job(conn, "z1", "zhilian")
        _job(conn, "l1", "liepin")
        conn.close()
        server.set_base_dir(base)
        started = []
        monkeypatch.setattr(
            server.task_runner, "start",
            lambda mode, config, before_start=None: started.append(list(config.get("_workbench_job_ids", [])))
            or {"id": "t", "mode": mode, "status": "running", "logs": []},
        )
        monkeypatch.setattr(server.task_runner, "status", lambda: {"active": None, "last_task": None, "tasks": []})
        monkeypatch.setattr("bosshunter.ai.greeter.greeting_config_error", lambda config: "")
        yield base, started


def test_deliver_all_approved_only_sends_boss_jobs_and_reports_the_rest(mixed):
    _, started = mixed
    status, payload = _request("/api/workbench/deliver", "POST", {"scope": "approved"})
    assert status.startswith("200"), payload
    assert started == [["b1"]]
    assert payload["skipped_count"] == 2 and payload["skipped_by_status"] == {"external_platform": 2}


def test_mixed_selection_with_skip_invalid_ignores_collection_only_platforms(mixed):
    _, started = mixed
    status, payload = _request(
        "/api/workbench/deliver", "POST", {"job_ids": ["b1", "z1", "l1"], "skip_invalid": True}
    )
    assert status.startswith("200") and started == [["b1"]]
    assert payload["skipped_by_status"] == {"external_platform": 2}


def test_strict_mixed_selection_is_still_rejected(mixed):
    status, payload = _request("/api/workbench/deliver", "POST", {"job_ids": ["b1", "z1"]})
    assert status.startswith("403") and payload["invalid_ids"] == ["z1"]


def test_only_external_jobs_approved_gives_a_manual_delivery_message(mixed):
    base, _ = mixed
    conn = get_db(base / "data" / "bosshunter.db")
    update_job_status(conn, "b1", "sent")
    conn.close()
    status, payload = _request("/api/workbench/deliver", "POST", {"scope": "approved"})
    assert status.startswith("400") and "手动投递" in payload["error"] and payload["external_platform_count"] == 2


def test_manual_sent_marks_external_jobs_and_counts_for_company_cooldown(db):
    from bosshunter.company_dedup import approved_within

    _job(db, "z9", "zhilian")
    add_history(db, "z9", "approved", "审查窗口确认")
    mark_external_jobs_sent(db, ["z9"], confirmed=True)
    assert db.execute("SELECT status FROM jobs WHERE id='z9'").fetchone()["status"] == "sent"
    assert [item["id"] for item in approved_within(db, 1)] == ["z9"]


def test_window_helpers_label_platforms_and_build_links():
    from bosshunter.ui.review_window import is_external, job_link, platform_label

    assert platform_label({"source_platform": "zhilian"}) == "智联" and platform_label({}) == "BOSS"
    assert is_external({"source_platform": "liepin"}) and not is_external({"source_platform": "boss"})
    assert job_link({"source_platform": "boss", "url": "/job_detail/x.html"}) == "https://www.zhipin.com/job_detail/x.html"
    assert job_link({"source_platform": "51job", "url": "https://jobs.51job.com/x"}) == "https://jobs.51job.com/x"
    assert job_link({"source_platform": "51job", "url": "/relative"}) == ""
