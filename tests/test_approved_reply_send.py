import json

import pytest

from bosshunter import review_queue as rq
from bosshunter.db import add_history, get_db, insert_job_if_new, update_job_status
from bosshunter.executor import monitor


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr("bosshunter.db.DB_PATH", path)
    return path


def _setup(db_path):
    conn = get_db(db_path)
    insert_job_if_new(conn, {"id": "j1", "title": "Agent工程师", "company": "某公司", "jd": "jd"})
    update_job_status(conn, "j1", "sent")
    detail = json.dumps({"schema": "reply_pending.v1", "hr_question": "何时面试", "ai_reply": "草稿"}, ensure_ascii=False)
    add_history(conn, "j1", "reply_pending", detail)
    draft = rq.pending_reply_drafts(conn)[0]
    rq.approve_reply(conn, draft["history_id"], "周三下午可以")
    conn.close()


def _patch_chat(monkeypatch, sent):
    messages = [{"sender": "hr", "text": "何时面试"}]
    monkeypatch.setattr(monitor, "stop_requested", lambda config: False)
    monkeypatch.setattr(monitor, "_open_scanned_conversation", lambda job, config, conv: "tab")
    monkeypatch.setattr(monitor, "_wait_or_stop", lambda config, secs: False)
    monkeypatch.setattr(monitor, "evaluate", lambda target, js: json.dumps(messages))
    monkeypatch.setattr(monitor, "close_tab", lambda target: None)
    monkeypatch.setattr(monitor, "_reconcile_conversation_messages", lambda m, job: m)
    monkeypatch.setattr(monitor, "_reconcile_outbound_chat_preview", lambda m, conv: m)

    class Guard:
        def record_page_success(self): pass
        def record_page_failure(self): pass

    monkeypatch.setattr(monitor, "_monitor_safety_guard", lambda config: Guard())
    monkeypatch.setattr(monitor, "_send_message_in_chat", lambda target, text, config=None: sent.append(text) or True)


def test_monitor_sends_user_approved_reply_and_resolves_it(db_path, monkeypatch):
    _setup(db_path)
    sent = []
    _patch_chat(monkeypatch, sent)
    action = monitor._handle_conversation({"id": "j1", "company": "某公司", "title": "Agent工程师"}, {})
    assert action == "auto_replied"
    assert sent == ["周三下午可以"]
    conn = get_db(db_path)
    assert conn.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()["status"] == "replied"
    assert rq.latest_approved_reply(conn, "j1") is None
    conn.close()


def test_failed_send_keeps_reply_approved(db_path, monkeypatch):
    _setup(db_path)
    _patch_chat(monkeypatch, [])
    monkeypatch.setattr(monitor, "_send_message_in_chat", lambda target, text, config=None: False)
    assert monitor._handle_conversation({"id": "j1", "company": "c", "title": "t"}, {}) == "failed"
    conn = get_db(db_path)
    assert rq.latest_approved_reply(conn, "j1")["message"] == "周三下午可以"
    conn.close()
