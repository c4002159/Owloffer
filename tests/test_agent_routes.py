import io
import json

import pytest

from bosshunter.web import server


def _call(path, method="GET", body=None, token=None):
    captured = {}

    def start_response(status, response_headers, exc_info=None):
        captured["status"] = status

    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    environ = {
        "REMOTE_ADDR": "127.0.0.1", "REQUEST_METHOD": method, "PATH_INFO": path.split("?")[0], "QUERY_STRING": path.partition("?")[2],
        "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8686", "HTTP_HOST": "127.0.0.1:8686", "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(raw), "wsgi.errors": io.StringIO(), "wsgi.multithread": False, "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    if token:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    if body is not None:
        environ["CONTENT_LENGTH"] = str(len(raw))
        environ["CONTENT_TYPE"] = "application/json"
    text = b"".join(c if isinstance(c, bytes) else c.encode("utf-8") for c in server.app(environ, start_response)).decode("utf-8")
    return captured["status"], json.loads(text)


@pytest.fixture
def agent(tmp_path, monkeypatch):
    server.set_base_dir(tmp_path)
    monkeypatch.setattr(server, "_agent_access", None)
    monkeypatch.setattr(server, "_daily_loop", None)
    sent = []

    def fake_api(method, path, body=None):
        if path == "/api/workbench":
            return {"task": None, "platform_limit_details": {}, "funnel_today": {"sent": 3}, "send_quota": {}}
        if path == "/api/workbench/deliver":
            sent.append(body)
            return {"id": "t1"}
        return {"items": []}

    monkeypatch.setattr(server, "_loopback_api", fake_api)
    token = _call("/api/agent-access/token", "POST", {})[1]["token"]
    return token, sent


def test_without_a_valid_token_nothing_is_readable_or_controllable(agent):
    token, _ = agent
    assert _call("/api/agent/control/status")[0].startswith("401")
    assert _call("/api/agent/control/status", token="wrong")[0].startswith("401")
    assert _call("/api/agent/control/deliver", "POST", {"job_ids": ["a"]}, token="wrong")[0].startswith("401")
    status, payload = _call("/api/agent/control/status", token=token)
    assert status.startswith("200") and payload["permission_mode"] == "auto" and payload["funnel_today"] == {"sent": 3}


def test_in_auto_mode_the_agent_approves_and_delivers_directly(agent):
    token, sent = agent
    status, payload = _call("/api/agent/control/deliver", "POST", {"job_ids": ["liepin:1", "liepin:2"]}, token=token)
    assert status.startswith("200") and payload["ok"] and sent == [{"job_ids": ["liepin:1", "liepin:2"], "skip_invalid": True}]
    events = _call("/api/agent/control/events?since=0", token=token)[1]["events"]
    assert any(e["kind"] == "agent" and "投递了 2" in e["message"] for e in events)


def test_in_ask_mode_a_request_waits_for_the_user_and_denying_it_counts_as_an_objection(agent):
    token, sent = agent
    _call("/api/agent-access/mode", "POST", {"mode": "ask"})
    status, payload = _call("/api/agent/control/deliver", "POST", {"job_ids": ["liepin:1"]}, token=token)
    assert status.startswith("202") and payload["status"] == "awaiting_user" and sent == []
    request_id = payload["request_id"]
    assert _call(f"/api/agent/control/requests/{request_id}", token=token)[1]["status"] == "pending"
    state = _call(f"/api/agent-access/requests/{request_id}/approve", "POST", {})[1]
    assert state["request"]["status"] == "approved" and state["result"]["ok"] and sent == [{"job_ids": ["liepin:1"], "skip_invalid": True}]
    second = _call("/api/agent/control/deliver", "POST", {"job_ids": ["liepin:2"]}, token=token)[1]["request_id"]
    denied = _call(f"/api/agent-access/requests/{second}/deny", "POST", {})[1]
    assert denied["request"]["status"] == "denied" and denied["objections"] == 1 and len(sent) == 1


def test_in_readonly_mode_the_agent_can_read_but_not_control(agent):
    token, sent = agent
    _call("/api/agent-access/mode", "POST", {"mode": "readonly"})
    assert _call("/api/agent/control/status", token=token)[0].startswith("200")
    status, payload = _call("/api/agent/control/daily-loop/start", "POST", {}, token=token)
    assert status.startswith("403") and payload["code"] == "read_only" and sent == []


def test_a_revoked_token_stops_working_and_the_page_state_never_shows_the_token(agent):
    token, _ = agent
    state = _call("/api/agent-access")[1]
    assert state["has_token"] is True and token not in json.dumps(state)
    _call("/api/agent-access/token/revoke", "POST", {})
    assert _call("/api/agent/control/status", token=token)[0].startswith("401")
