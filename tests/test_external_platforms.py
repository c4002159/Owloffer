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
        "REMOTE_ADDR": "127.0.0.1", "REQUEST_METHOD": method, "PATH_INFO": path.split("?")[0], "QUERY_STRING": path.partition("?")[2],
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
        _job(conn, "z2", "zhilian")
        conn.close()
        server.set_base_dir(base)
        started = []
        monkeypatch.setattr(
            server.task_runner, "start",
            lambda mode, config, before_start=None, **_: started.append(list(config.get("_workbench_job_ids", [])))
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
        "/api/workbench/deliver", "POST", {"job_ids": ["b1", "z1", "z2"], "skip_invalid": True}
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
    assert is_external({"source_platform": "zhilian"}) and not is_external({"source_platform": "boss"})
    assert not is_external({"source_platform": "51job"}) and not is_external({"source_platform": "liepin"})   # delivered by BossHunter itself
    assert job_link({"source_platform": "boss", "url": "/job_detail/x.html"}) == "https://www.zhipin.com/job_detail/x.html"
    assert job_link({"source_platform": "51job", "url": "https://jobs.51job.com/x"}) == "https://jobs.51job.com/x"
    assert job_link({"source_platform": "51job", "url": "/relative"}) == ""


def test_preflight_accepts_the_fill_mode():
    from bosshunter.web.preflight import VALID_MODES, _configuration_checks

    assert "fill" in VALID_MODES
    checks = _configuration_checks("fill", {"profile": {}})
    assert all(check["id"] != "task_mode" for check in checks)


def test_fill_collects_internship_keywords_on_external_platforms_with_internships_allowed(db, monkeypatch):
    calls = []

    def fake_collect(cfg, platform, keyword, stop_event):
        calls.append((platform, keyword, bool((cfg.get("profile") or {}).get("allow_internship"))))
        return 0, "search_exhausted"

    _quiet_fill(monkeypatch, fake_collect)
    monkeypatch.setattr("bosshunter.ai.scorer.score_jobs", lambda cfg, **_: None)
    config = {
        "search": {"keywords": ["Agent工程师"], "intern_keywords": ["AI开发实习生"]},
        "platforms": {"51job": {"enabled": True, "search": {"keywords": []}}},
        "automation": {"review_cap": 6, "internship_quota": 2},
    }
    fill_module.fill_review_list(config, lambda _m: None, max_rounds=1)
    assert ("51job", "Agent工程师", False) in calls and ("51job", "AI开发实习生", True) in calls


def test_a_zero_daily_limit_means_the_send_quota_is_not_capped(tmp_path):
    (tmp_path / "config.yaml").write_text("throttle:\n  daily_limit: 0\n", encoding="utf-8")
    get_db(tmp_path / "data" / "bosshunter.db").close()
    server.set_base_dir(tmp_path)
    status, payload = _request("/api/workbench")
    assert status.startswith("200") and payload["send_quota"]["unlimited"] is True
    assert payload["send_quota"]["exhausted"] is False

    (tmp_path / "config.yaml").write_text("throttle:\n  daily_limit: 30\n", encoding="utf-8")
    status, payload = _request("/api/workbench")
    assert payload["send_quota"]["unlimited"] is False and payload["send_quota"]["remaining"] == 30


def test_a_job_leaves_the_delivery_queue_when_its_turn_is_over_and_can_be_queued_again():
    from threading import Lock

    from bosshunter.web.tasks import WorkbenchTask

    task = WorkbenchTask(id="t1", mode="deliver", label="确认投递")
    task.context.update({"delivering": True, "delivery_queue_lock": Lock(), "delivery_scheduled_ids": {"a", "b"}})
    server._release_scheduled(task, "a")                                       # a was sent or failed
    assert task.context["delivery_scheduled_ids"] == {"b"}
    payload, new_ids = server._queue_active_delivery(task, ["a", "b", "c"], direct_send=True)
    assert new_ids == ["a", "c"] and payload["already_queued_count"] == 1      # b is still queued; a (failed) and c are added
    assert task.context["delivery_scheduled_ids"] == {"a", "b", "c"}
    server._release_scheduled(task, "missing")                                 # unknown ids are harmless


def test_the_sender_reports_each_finished_job_to_the_queue(monkeypatch, tmp_path):
    from bosshunter.executor import sender

    done = []
    config = {"_workbench_job_done": done.append, "_workbench_job_ids": ["j1", "j2"], "throttle": {"interval_min": 0, "interval_max": 0, "daily_limit": 0, "browse_before_greet": False, "send_windows": ["00:00-23:59"]}}
    db_path = tmp_path / "data" / "bosshunter.db"
    conn = get_db(db_path)
    for job_id in ("j1", "j2"):
        _job(conn, job_id, "boss", status="ready")
    conn.close()
    monkeypatch.setattr(sender, "_deliver_job_once", lambda job, greeting, cfg: ({"success": True}, None))
    monkeypatch.setattr(sender, "should_take_day_off", lambda *a, **k: False)
    monkeypatch.setattr("bosshunter.executor.sender.RequestThrottle.wait", lambda self, stop=None: False)
    sender.send_greetings(config, force=True, db_path=db_path)
    assert done[:1] == ["j1"]                                                  # j1 is released before j2 starts


def _pool(tmp_path, rows):
    conn = get_db(tmp_path / "data" / "bosshunter.db")
    for job_id, status, greeting in rows:
        _job(conn, job_id, "boss", status="pending")
        conn.execute("UPDATE jobs SET status = ?, greeting = ? WHERE id = ?", (status, greeting, job_id))
    conn.commit()
    conn.close()
    server.set_base_dir(tmp_path)


def test_pool_views_split_confirm_waiting_and_failed(tmp_path):
    _pool(tmp_path, [("p1", "pending", ""), ("r1", "ready", ""), ("r2", "ready", "您好"), ("a1", "approved", ""),
                     ("e1", "error", "您好"), ("s1", "sent", "您好")])

    def ids(view):
        status, payload = _request(f"/api/jobs/search?limit=50&queue_view={view}")
        assert status.startswith("200"), payload
        return {item["id"] for item in payload["items"]}

    assert ids("confirm") == {"p1", "r1"}                      # nothing prepared yet: waiting for you
    assert ids("waiting") == {"r2", "a1"}                      # confirmed or greeting ready: waiting to be delivered
    assert ids("failed") == {"e1"}
    assert _request("/api/jobs/search?queue_view=nope")[0].startswith("400")


def test_database_edits_of_jobs_no_task_is_working_on_run_alongside_the_running_task(tmp_path, monkeypatch):
    from types import SimpleNamespace

    _pool(tmp_path, [("queued", "ready", "您好"), ("free", "ready", "您好"), ("other", "ready", "您好"), ("fresh", "pending", "")])
    runtime = SimpleNamespace(mode="deliver", status="running", context={"delivering": True, "delivery_scheduled_ids": {"queued"}})
    active = {"id": "t1", "label": "确认投递", "mode": "deliver"}
    monkeypatch.setattr(server.task_runner, "status", lambda: {"active": active, "last_task": None, "queued": []})
    monkeypatch.setitem(server.task_runner._tasks, "t1", runtime)

    status, payload = _request("/api/jobs/soft-delete", "POST", {"job_ids": ["free"], "confirmed": True})
    assert status.startswith("200") and payload["affected_count"] == 1                 # not queued: goes ahead

    status, payload = _request("/api/jobs/soft-delete", "POST", {"job_ids": ["queued", "other"], "confirmed": True})
    assert status.startswith("409") and payload["code"] == "jobs_in_delivery_queue" and payload["job_ids"] == ["queued"]

    # A collection (with scoring) is running: jobs it is not working on are free, the ones waiting to be scored are not.
    runtime.mode, runtime.context, active["mode"] = "collect", {}, "collect"
    status, payload = _request("/api/jobs/soft-delete", "POST", {"job_ids": ["other"], "confirmed": True})
    assert status.startswith("200") and payload["affected_count"] == 1
    status, payload = _request("/api/jobs/soft-delete", "POST", {"job_ids": ["fresh"], "confirmed": True})
    assert status.startswith("409") and payload["code"] == "jobs_in_delivery_queue" and payload["job_ids"] == ["fresh"]


def test_a_task_that_cannot_start_now_waits_in_line_and_starts_by_itself():
    from threading import Event

    from bosshunter.web.tasks import WorkbenchTaskRunner

    release, started = Event(), []

    def blocking(task, config):
        started.append(("collect", task.id))
        release.wait(timeout=5)

    runner = WorkbenchTaskRunner({"collect": blocking, "deliver": lambda task, config: started.append(("deliver", task.id))})
    first = runner.start("collect", {})
    second = runner.start("deliver", {"_workbench_job_ids": ["j1"]}, queue_if_busy=True)
    assert second["status"] == "queued" and "等待" in second["logs"][0]
    status = runner.status()
    assert [item["mode"] for item in status["queued"]] == ["deliver"] and status["active"]["id"] == first["id"]
    try:
        runner.start("deliver", {})                                                # without the flag it is still refused
        assert False, "should have been refused"
    except Exception as exc:
        assert "正在运行" in str(exc)
    release.set()
    runner.wait(timeout=5)
    import time
    for _ in range(50):
        if any(kind == "deliver" for kind, _id in started):
            break
        time.sleep(0.1)
    runner.wait(timeout=5)
    assert [kind for kind, _id in started] == ["collect", "deliver"]               # the queued one ran after the first ended
    assert runner.status()["queued"] == []


def test_a_queued_task_can_be_cancelled():
    from threading import Event

    from bosshunter.web.tasks import WorkbenchTaskRunner

    release = Event()
    runner = WorkbenchTaskRunner({"collect": lambda task, config: release.wait(timeout=5), "greet": lambda task, config: None})
    runner.start("collect", {})
    queued = runner.start("greet", {}, queue_if_busy=True)
    cancelled = runner.stop(queued["id"])
    assert cancelled["status"] == "stopped" and cancelled["stop_reason"] == "已取消排队" and runner.status()["queued"] == []
    release.set()
    runner.wait(timeout=5)
