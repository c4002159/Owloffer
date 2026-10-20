from bosshunter.ui import server_client


def test_start_task_success_and_errors(monkeypatch):
    monkeypatch.setattr(server_client, "_call", lambda *a, **k: (200, {"id": "abc", "status": "running"}))
    assert server_client.start_task("fill") == (True, {"id": "abc", "status": "running"})

    monkeypatch.setattr(server_client, "_call", lambda *a, **k: (409, {"error": "当前已有后台任务"}))
    ok, reason = server_client.start_task("fill")
    assert not ok and "当前已有后台任务" in reason

    monkeypatch.setattr(server_client, "_call", lambda *a, **k: (400, {"error": "请先处理启动前检查", "messages": ["缺简历"]}))
    ok, reason = server_client.start_task("fill")
    assert not ok and "缺简历" in reason

    monkeypatch.setattr(server_client, "_call", lambda *a, **k: (0, None))
    assert server_client.start_task("fill") == (False, "面板服务未运行")


def test_workbench_tasks_and_stop(monkeypatch):
    monkeypatch.setattr(server_client, "_call", lambda *a, **k: (200, {"task": {"id": "a"}, "last_task": {"id": "b"}}))
    assert server_client.workbench_tasks() == ({"id": "a"}, {"id": "b"})
    monkeypatch.setattr(server_client, "_call", lambda *a, **k: (0, None))
    assert server_client.workbench_tasks() == (None, None)
    assert server_client.stop_task("a") is False
    monkeypatch.setattr(server_client, "_call", lambda *a, **k: (200, {}))
    assert server_client.stop_task("a") is True
