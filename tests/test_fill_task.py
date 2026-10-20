import time

import pytest

from bosshunter.web import server
from bosshunter.web.tasks import MODE_LABELS, WorkbenchTask


def test_fill_mode_is_registered_with_a_label_and_executor():
    assert MODE_LABELS["fill"] == "补满审查列表"
    assert server.task_runner._executors["fill"] is server._execute_fill


def test_preflight_accepts_fill_and_requires_resume_and_ai_key(tmp_path):
    messages = server._preflight_messages("fill", {"profile": {"resume_path": str(tmp_path / "missing.md")}})
    assert not any("不支持的任务模式" in m for m in messages)
    assert any("简历" in m for m in messages)


def test_execute_fill_logs_progress_and_updates_task_metrics(monkeypatch):
    task = WorkbenchTask(id="t1", mode="fill", label="补满审查列表")
    config = {"automation": {"review_cap": 150, "internship_quota": 30}}

    def fake_fill(cfg, report, stop_event=None, **_):
        report("第 1 轮 · 全职 · 采集「Agent工程师」（最多 20 个）…")
        return {"by_kind": {"fulltime": 26, "internship": 4}, "collected": 12, "stopped": "", "waiting": 30}

    monkeypatch.setattr("bosshunter.fill.fill_review_list", fake_fill)
    monkeypatch.setattr("bosshunter.fill.counts_by_kind", lambda cfg: {"fulltime": 26, "internship": 4})
    server._execute_fill(task, config)
    assert task.metrics["fill_fulltime"] == 26 and task.metrics["fill_internship_quota"] == 30
    assert task.metrics["fill_fulltime_quota"] == 120
    assert any("开始补满审查列表：全职 120 个 + 实习 30 个" in line for line in task.logs)
    assert any("全职 26/120，实习 4/30" in line for line in task.logs)


def test_execute_fill_fails_the_task_on_a_safety_stop(monkeypatch):
    task = WorkbenchTask(id="t2", mode="fill", label="补满审查列表")
    monkeypatch.setattr(
        "bosshunter.fill.fill_review_list",
        lambda cfg, report, stop_event=None, **_: {
            "by_kind": {"fulltime": 0, "internship": 0}, "collected": 0,
            "stopped": "采集已安全停止：captcha", "waiting": 0,
        },
    )
    monkeypatch.setattr("bosshunter.fill.counts_by_kind", lambda cfg: {"fulltime": 0, "internship": 0})
    with pytest.raises(RuntimeError, match="captcha"):
        server._execute_fill(task, {"automation": {"review_cap": 150}})
