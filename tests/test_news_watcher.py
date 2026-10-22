import io
import json

from bosshunter.agent_access import AgentAccess
from bosshunter.news_watcher import NewsWatcher
from bosshunter.web import server


def _watcher(tmp_path, state):
    access = AgentAccess(tmp_path / "a.json")
    watcher = NewsWatcher(access.emit, lambda: state["tasks"], lambda: state["limits"], lambda: state["replies"])
    return access, watcher


def test_the_first_poll_only_remembers_and_later_changes_become_messages(tmp_path):
    state = {"tasks": [{"id": "t0", "status": "completed", "label": "旧任务", "logs": ["x"]}], "limits": {"boss": {"reason": "旧的暂停"}}, "replies": 2}
    access, watcher = _watcher(tmp_path, state)
    watcher.poll()
    assert access.messages()["messages"] == []                         # nothing from before the program started

    state["tasks"].append({"id": "t1", "status": "running", "label": "单独采集", "logs": []})
    watcher.poll()
    assert access.messages()["messages"] == []                         # a task that is still running is not news
    state["tasks"][1].update(status="failed", error="平台页面结构变化")
    state["tasks"].append({"id": "t2", "status": "completed", "label": "确认投递", "logs": ["投递完成：成功 24 个"]})
    state["limits"]["liepin"] = {"reason": "出现验证"}
    state["replies"] = 5
    watcher.poll()
    watcher.poll()                                                      # polling again must not repeat anything
    kinds = [(m["kind"], m["level"]) for m in reversed(access.messages()["messages"])]
    assert ("task", "warning") in kinds and ("task", "info") in kinds and ("platform", "warning") in kinds and ("reply", "info") in kinds
    texts = " ".join(m["message"] for m in access.messages()["messages"])
    assert "单独采集失败：平台页面结构变化" in texts and "猎聘被暂停：出现验证" in texts and "有 3 条新的 HR 回复" in texts


def test_a_platform_that_is_free_again_is_announced_again_when_paused_again(tmp_path):
    state = {"tasks": [], "limits": {}, "replies": 0}
    access, watcher = _watcher(tmp_path, state)
    watcher.poll()
    state["limits"] = {"boss": {"reason": "沟通上限"}}
    watcher.poll()
    state["limits"] = {}
    watcher.poll()
    state["limits"] = {"boss": {"reason": "再次沟通上限"}}
    watcher.poll()
    assert [m["message"] for m in access.messages()["messages"]].count("BOSS 直聘被暂停：沟通上限") == 1
    assert any("再次沟通上限" in m["message"] for m in access.messages()["messages"])


def test_unread_counts_and_marking_read(tmp_path):
    access = AgentAccess(tmp_path / "a.json")
    first = access.emit("loop", "一")
    access.emit("loop", "二")
    access.emit("loop", "三")
    assert access.messages()["unread"] == 3
    access.mark_read(first)
    assert access.messages()["unread"] == 2
    access.mark_read()
    assert access.messages()["unread"] == 0 and access.messages(2)["messages"][0]["message"] == "三"


def _call(path, method="GET", body=None):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    captured = {}
    environ = {
        "REMOTE_ADDR": "127.0.0.1", "REQUEST_METHOD": method, "PATH_INFO": path.split("?")[0], "QUERY_STRING": path.partition("?")[2],
        "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8686", "HTTP_HOST": "127.0.0.1:8686", "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(raw), "wsgi.errors": io.StringIO(), "wsgi.multithread": False, "wsgi.multiprocess": False, "wsgi.run_once": False,
    }
    if body is not None:
        environ["CONTENT_LENGTH"] = str(len(raw))
        environ["CONTENT_TYPE"] = "application/json"
    text = b"".join(c if isinstance(c, bytes) else c.encode("utf-8") for c in server.app(environ, lambda s, h, e=None: captured.update(status=s))).decode("utf-8")
    return captured["status"], json.loads(text)


def test_the_message_routes_list_newest_first_and_mark_read(tmp_path, monkeypatch):
    server.set_base_dir(tmp_path)
    monkeypatch.setattr(server, "_agent_access", None)
    access = server._get_agent_access()
    access.emit("incident", "旧", level="warning")
    access.emit("task", "新")
    status, payload = _call("/api/messages")
    assert status.startswith("200") and [m["message"] for m in payload["messages"]] == ["新", "旧"] and payload["unread"] == 2
    status, payload = _call("/api/messages/read", "POST", {})
    assert status.startswith("200") and payload["unread"] == 0


def test_a_scoring_run_stopped_half_way_is_announced_once_even_if_it_predates_the_program(tmp_path):
    state = {"tasks": [], "limits": {}, "replies": 0, "paused": [{"id": "r1", "progress": {"ai_completed": 47, "ai_total": 64}, "remaining_job_ids": ["a"] * 17}]}
    access = AgentAccess(tmp_path / "a.json")
    watcher = NewsWatcher(access.emit, lambda: state["tasks"], lambda: state["limits"], lambda: state["replies"], lambda: state["paused"])
    watcher.poll()
    watcher.poll()
    messages = access.messages()["messages"]
    assert len(messages) == 1 and messages[0]["level"] == "warning"
    assert "已评 47/64" in messages[0]["message"] and "还剩 17 个" in messages[0]["message"] and "等待队列" in messages[0]["message"]
    state["paused"] = []                                            # the user dealt with it
    watcher.poll()
    state["paused"] = [{"id": "r2", "progress": {}, "remaining_job_ids": ["b"]}]
    watcher.poll()
    assert len(access.messages()["messages"]) == 2                  # a new stopped run is news again
