import threading
import time

import pytest

from bosshunter.web.tasks import TaskAlreadyRunningError, WorkbenchTaskRunner, modes_conflict

ALL_DAY = {"throttle": {"send_windows": ["00:00-23:59"]}}      # a delivery task is not started outside the send window


def _runner():
    gates = {}

    def executor(mode):
        def run(task, config):
            gate = gates.setdefault(task.id, threading.Event())
            task.context["started"] = True
            while not gate.wait(0.02):
                if task.stop_requested.is_set():
                    return
        return run

    runner = WorkbenchTaskRunner({mode: executor(mode) for mode in ("collect", "score", "rescore", "deliver", "monitor", "greet", "full", "fill")})
    return runner, gates


def _wait(condition, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_which_kinds_of_task_may_run_side_by_side():
    assert not modes_conflict("collect", "score") and not modes_conflict("rescore", "collect") and not modes_conflict("fill", "score")
    for pair in (("score", "score"), ("collect", "collect"), ("deliver", "score"), ("collect", "deliver"), ("full", "score"), ("monitor", "collect"), ("greet", "rescore")):
        assert modes_conflict(*pair), pair


def test_collecting_starts_while_scoring_runs_and_both_show_up():
    runner, gates = _runner()
    scoring = runner.start("score", {})
    collecting = runner.start("collect", {}, queue_if_busy=True)
    assert collecting["status"] == "running"                          # not queued behind the scoring run
    assert _wait(lambda: all(task["status"] == "running" for task in runner.status()["active_tasks"]) and len(runner.status()["active_tasks"]) == 2)
    status = runner.status()
    assert status["active"]["mode"] == "collect"                      # the workbench shows the collecting one as the running task
    assert {task["mode"] for task in status["active_tasks"]} == {"collect", "score"}
    for task_id in (scoring["id"], collecting["id"]):
        gates.setdefault(task_id, threading.Event()).set()
    runner.wait(3)


def test_the_other_way_round_and_same_kind_still_conflict():
    runner, gates = _runner()
    collecting = runner.start("collect", {})
    scoring = runner.start("score", {})
    assert scoring["status"] == "running"
    with pytest.raises(TaskAlreadyRunningError):
        runner.start("score", {})                                     # one scoring run at a time
    with pytest.raises(TaskAlreadyRunningError):
        runner.start("collect", {})                                   # and one collection
    queued = runner.start("deliver", ALL_DAY, queue_if_busy=True)          # delivery has the program to itself: it waits for both
    assert queued["status"] == "queued"
    for task_id in (collecting["id"], scoring["id"]):
        gates.setdefault(task_id, threading.Event()).set()
    assert _wait(lambda: runner.status()["active"] and runner.status()["active"]["mode"] == "deliver")
    gates.setdefault(queued["id"], threading.Event()).set()
    runner.wait(3)


def test_a_queued_task_starts_as_soon_as_what_blocked_it_is_gone_not_before():
    runner, gates = _runner()
    delivering = runner.start("deliver", ALL_DAY)
    waiting_score = runner.start("score", {}, queue_if_busy=True)
    waiting_collect = runner.start("collect", {}, queue_if_busy=True)
    assert waiting_score["status"] == "queued" and waiting_collect["status"] == "queued"
    gates.setdefault(delivering["id"], threading.Event()).set()
    assert _wait(lambda: len(runner.status()["active_tasks"]) == 2)   # both waiting tasks start together once delivery ends
    assert {task["mode"] for task in runner.status()["active_tasks"]} == {"score", "collect"}
    for task_id in (waiting_score["id"], waiting_collect["id"]):
        gates.setdefault(task_id, threading.Event()).set()
    runner.wait(3)


def test_stopping_one_task_leaves_the_other_running():
    runner, gates = _runner()
    scoring = runner.start("score", {})
    collecting = runner.start("collect", {})
    runner.stop(scoring["id"])
    assert _wait(lambda: [task["mode"] for task in runner.status()["active_tasks"]] == ["collect"])
    gates.setdefault(collecting["id"], threading.Event()).set()
    runner.wait(3)
    assert {task["mode"]: task["status"] for task in runner.status()["tasks"]} == {"score": "stopped", "collect": "completed"}
