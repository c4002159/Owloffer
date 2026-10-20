import pytest

from bosshunter import review_queue as rq
from bosshunter.db import get_db, insert_job_if_new, update_job_dual_score, update_job_score, update_job_status
from bosshunter.ui import server_client


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr("bosshunter.db.DB_PATH", path)
    conn = get_db(path)
    yield conn
    conn.close()


def _job(db, job_id, status):
    insert_job_if_new(db, {"id": job_id, "title": f"岗位{job_id}", "company": f"公司{job_id}", "jd": "x" * 200})
    update_job_score(db, job_id, 80, "ok")
    update_job_dual_score(db, job_id, 80, 100, "d")
    update_job_status(db, job_id, status)


def test_revoke_approval_only_touches_approved_jobs(db):
    _job(db, "a", "approved")
    _job(db, "b", "sent")
    _job(db, "c", "ready")
    assert rq.revoke_approval(db, ["a", "b", "c", "missing"]) == 1
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"a": "ready", "b": "sent", "c": "ready"}
    assert [j["id"] for j in rq.review_pending(db, 60)] == ["a", "c"] or sorted(
        j["id"] for j in rq.review_pending(db, 60)
    ) == ["a", "c"]


def test_start_deliver_approved_posts_scope_and_tailoring_flag(monkeypatch):
    seen = {}

    def fake_call(method, path, body=None, **kwargs):
        seen.update(method=method, path=path, body=body)
        return 200, {"id": "task-9", "status": "running"}

    monkeypatch.setattr(server_client, "_call", fake_call)
    ok, payload = server_client.start_deliver_approved(tailor_resumes=True)
    assert ok and payload["id"] == "task-9"
    assert seen == {
        "method": "POST", "path": "/api/workbench/deliver",
        "body": {"scope": "approved", "tailor_resumes": True},
    }


def test_start_deliver_approved_reports_errors(monkeypatch):
    monkeypatch.setattr(server_client, "_call", lambda *a, **k: (400, {"error": "没有已通过、待投递的岗位"}))
    assert server_client.start_deliver_approved() == (False, "没有已通过、待投递的岗位")
    monkeypatch.setattr(server_client, "_call", lambda *a, **k: (0, None))
    assert server_client.start_deliver_approved() == (False, "面板服务未运行")


def test_window_builds_with_all_tabs_and_lists_approved_jobs(db):
    tk = pytest.importorskip("tkinter")
    from bosshunter.ui.review_window import ReviewWindow

    _job(db, "approved1", "approved")
    try:
        window = ReviewWindow({"automation": {"review_cap": 10, "internship_quota": 2}})
    except tk.TclError:
        pytest.skip("no display available")
    try:
        assert window.approved_tree.exists("approved1")
        assert "已通过待发 1" in window.status.get()
        values = window.approved_tree.item("approved1", "values")
        assert values[5] == "已确认·待投递" and values[4] == "全职"
        assert window.approved_tree.heading("match")["text"] == "匹配度"
        assert window.approved_tree.heading("activity")["text"] == "活跃度"
    finally:
        window.root.destroy()
