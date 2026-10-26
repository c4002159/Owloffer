from datetime import datetime, timedelta

from bosshunter.daily_loop import ApiError, DailyLoop


class FakeServer:
    """The program's API as the loop sees it: tasks that run for a few polls, then leave their metrics behind."""

    def __init__(self, new_per_collect=7, limits=None, refuse_collect=None):
        self.t = datetime(2026, 10, 22, 9, 0)
        self.calls: list[tuple[str, str, dict | None]] = []
        self.running = 0
        self.last: dict = {}
        self.pending_metrics: dict = {}
        self.new_per_collect, self.limits, self.refuse_collect = new_per_collect, limits or {}, refuse_collect
        self.confirm = [{"id": "liepin:1", "source_platform": "liepin"}, {"id": "zhilian_campus:1", "source_platform": "zhilian_campus"},
                        {"id": "zhilian:9", "source_platform": "zhilian"}]
        self.waiting = [{"id": "liepin:1", "source_platform": "liepin", "greeting": ""}]
        self.manual = [{"id": "zhilian_campus:1", "source_platform": "zhilian_campus", "greeting": "", "status": "ready"},
                       {"id": "zhilian_campus:2", "source_platform": "zhilian_campus", "greeting": "", "status": "pending"}]
        self.approved: list[str] = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)
        if self.running:
            self.running -= 1
            if not self.running:
                self.last = {"status": "completed", "metrics": self.pending_metrics, "progress": {"platforms": self.pending_progress}, "logs": ["done"]}

    pending_progress: dict = {}

    def _start(self, metrics, progress=None):
        self.running = 2
        self.pending_metrics, self.pending_progress = metrics, progress or {}
        return {"id": "t1", "status": "running"}

    def api(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/workbench":
            task = {"id": "t1", "status": "running", "logs": ["working"]} if self.running else None
            return {"task": task, "queued_tasks": [], "last_task": self.last, "platform_limit_details": self.limits}
        if path == "/api/workbench/task" and method == "POST" and body.get("mode") == "monitor":
            return self._start({})
        if path == "/api/workbench/task" and method == "POST":
            platform = body["options"]["platform_order"][0]
            if self.refuse_collect:
                raise ApiError(400, {"error": "请先处理启动前检查", "messages": [self.refuse_collect]})
            progress = {platform: {"status": "blocked", "reason_code": "rate_limit", "message": "被限流"}} if platform == "boss" else {}
            return self._start({"collect_new": self.new_per_collect}, progress)
        if path.startswith("/api/jobs/search"):
            items = self.confirm if "confirm" in path else self.waiting if "waiting" in path else self.manual
            return {"items": items}
        if path == "/api/workbench/greetings":
            return self._start({})
        if path == "/api/workbench/deliver":
            self.approved += body["job_ids"]
            assert body["skip_invalid"] is True
            return self._start({"send_success": 3, "send_failed": 1})
        raise AssertionError(path)


def _loop(server, tmp_path):
    loop = DailyLoop(tmp_path / "loop.json", server.api, now=server.now, sleep=server.sleep, poll_seconds=60, idle_seconds=600)
    return loop


def _run(loop, order, end="09:30", targets=None):
    loop.start({"platform_order": order, "end_time": end, "targets": targets or {}})
    loop.join(10)


def test_a_round_collects_then_approves_greets_and_delivers_but_leaves_campus_to_the_user(tmp_path):
    server = FakeServer()
    loop = _loop(server, tmp_path)
    _run(loop, ["zhilian_campus", "liepin"], end="09:20")
    posts = [(path, body) for method, path, body in server.calls if method == "POST"]
    collects = [body["options"] for path, body in posts if path == "/api/workbench/task"]
    assert collects[0]["platform_order"] == ["zhilian_campus"] and collects[0]["target_new"] == 50
    assert collects[1]["platform_order"] == ["liepin"] and collects[1]["target_new"] == 80
    assert set(server.approved) == {"liepin:1"}                                         # campus and other platforms stay untouched
    greetings = [body["job_ids"] for path, body in posts if path == "/api/workbench/greetings"]
    assert ["zhilian_campus:1"] in greetings and ["liepin:1"] not in greetings          # the delivery task writes its own greetings
    assert any(path == "/api/workbench/deliver" for path, _ in posts)
    state = loop.snapshot()
    assert state["totals"]["liepin"]["collected"] >= 7 and state["totals"]["liepin"]["delivered"] >= 3
    assert not state["running"] and state["step"] == "finished"


def test_problems_become_incidents_and_the_loop_goes_on_with_the_next_platform(tmp_path):
    server = FakeServer()
    loop = _loop(server, tmp_path)
    _run(loop, ["boss", "liepin"], end="09:15")
    state = loop.snapshot()
    codes = {(i["platform"], i["code"]) for i in state["incidents"]}
    assert ("boss", "rate_limit") in codes                                             # reported with the platform's own reason
    assert ("liepin", "send_failures") in codes                                        # 1 failed delivery is reported
    assert any(path == "/api/workbench/task" and body["options"]["platform_order"] == ["liepin"] for _, path, body in server.calls if body)
    boss = next(i for i in state["incidents"] if i["platform"] == "boss")
    assert boss["message"] and boss["detail"] and not boss["resolved"]


def test_a_paused_platform_is_skipped_and_reported_not_collected(tmp_path):
    server = FakeServer(limits={"boss": {"reason": "今天已与150位BOSS沟通"}})
    loop = _loop(server, tmp_path)
    _run(loop, ["boss"], end="09:10")
    assert not any(path == "/api/workbench/task" for _, path, _ in server.calls)
    assert [i["code"] for i in loop.snapshot()["incidents"]] == ["platform_paused"]


def test_a_refused_start_is_reported_once_with_its_messages_and_retried_later(tmp_path):
    server = FakeServer(refuse_collect="Chrome 没有开调试端口")
    loop = _loop(server, tmp_path)
    _run(loop, ["liepin"], end="10:30")
    incidents = [i for i in loop.snapshot()["incidents"] if i["code"] == "start_refused"]
    assert len(incidents) == 1 and incidents[0]["code"] == "start_refused" and incidents[0]["count"] > 1
    assert "调试端口" in incidents[0]["detail"]


def test_the_loop_stops_at_its_end_time_and_survives_a_restart(tmp_path):
    server = FakeServer()
    loop = _loop(server, tmp_path)
    _run(loop, ["liepin"], end="09:05")
    assert loop.snapshot()["running"] is False
    # a new object on the same file sees the finished state and does not resume past the end time
    again = _loop(server, tmp_path)
    assert again.resume() is False
    # a loop that was running when the server stopped continues if its end time has not come
    state = loop.snapshot()
    state.update(running=True, end_at="2026-10-22T09:50:00", order=["liepin"], round=1, totals={}, targets=state["targets"])
    again.state = state
    server.t = datetime(2026, 10, 22, 9, 10)
    assert again.resume() is True
    again.join(10)
    assert again.snapshot()["round"] >= 2 and not again.snapshot()["running"]


def test_an_incident_can_be_marked_as_handled(tmp_path):
    server = FakeServer()
    loop = _loop(server, tmp_path)
    loop.start({"platform_order": [], "end_time": "09:01"})
    loop.join(5)
    loop.incident("boss", "collect", "x", "boom")
    incident_id = loop.snapshot()["incidents"][0]["id"]
    assert loop.resolve_incident(incident_id) and loop.snapshot()["incidents"][0]["resolved"]
    loop.incident("boss", "collect", "x", "boom")                    # after it was handled, the same trouble is a new incident
    assert len(loop.snapshot()["incidents"]) == 2


def test_the_daily_loop_routes_report_start_and_resolve(tmp_path, monkeypatch):
    from bosshunter.web import server
    from tests.test_external_platforms import _request

    server.set_base_dir(tmp_path)
    monkeypatch.setattr(server, "_daily_loop", None)
    fake = FakeServer()
    monkeypatch.setattr(server, "_loopback_api", fake.api)
    status, payload = _request("/api/daily-loop")
    assert status.startswith("200") and payload.get("running") is not True and payload["alive"] is False
    loop = server._get_daily_loop()
    loop.incident("boss", "collect", "rate_limit", "被限流", "details")
    incident_id = _request("/api/daily-loop")[1]["incidents"][0]["id"]
    status, payload = _request(f"/api/daily-loop/incidents/{incident_id}/resolve", "POST", {})
    assert status.startswith("200") and payload["incidents"][0]["resolved"] is True
    assert _request("/api/daily-loop/incidents/nope/resolve", "POST", {})[0].startswith("404")
    assert _request("/api/daily-loop/stop", "POST", {})[0].startswith("200")


def test_a_platform_that_reached_the_delivery_cap_is_skipped_and_the_loop_ends_when_all_are_capped(tmp_path):
    server = FakeServer()
    server.confirm = [{"id": f"liepin:{n}", "source_platform": "liepin"} for n in range(10)]
    loop = _loop(server, tmp_path)
    loop.start({"platform_order": ["liepin"], "end_time": "12:00", "delivery_cap": 3})
    loop.join(10)
    delivers = [body for method, path, body in server.calls if path == "/api/workbench/deliver"]
    assert len(delivers[0]["job_ids"]) == 3                                  # never more than the cap in one go
    state = loop.snapshot()
    assert state["totals"]["liepin"]["delivered"] >= 3 and not state["running"]
    assert "上限" in state["message"] and len(delivers) == 1                  # capped: no second delivery, loop ends early
    assert server.t < datetime(2026, 10, 22, 12, 0)


def test_the_loop_waits_for_its_start_time_and_refuses_times_that_do_not_make_sense(tmp_path):
    server = FakeServer()
    loop = _loop(server, tmp_path)
    loop.start({"platform_order": ["liepin"], "start_time": "09:30", "end_time": "10:00", "delivery_cap": 0})
    loop.join(10)
    first_call = next(i for i, (m, p, b) in enumerate(server.calls) if p == "/api/workbench/task")
    assert server.calls and first_call >= 0
    started_at = [c for c in server.calls if c[1] == "/api/workbench/task"]
    assert started_at                                                       # it did work once its time came
    import pytest

    with pytest.raises(ValueError):
        _loop(FakeServer(), tmp_path / "x").start({"end_time": "08:00"})       # the end time already passed (it is 09:00)
    across_midnight = _loop(FakeServer(), tmp_path / "y")
    across_midnight._launch = lambda: None
    across_midnight.start({"start_time": "11:00", "end_time": "10:00"})          # an end before the start means: the next morning
    assert across_midnight.snapshot()["end_at"] == "2026-10-23T10:00:00"


def test_each_platform_can_be_automatic_manual_or_left_out(tmp_path):
    server = FakeServer()
    loop = _loop(server, tmp_path)
    loop.start({"modes": {"zhilian_campus": "off", "boss": "manual", "liepin": "auto", "zhilian": "off", "51job": "off"}, "end_time": "09:20"})
    loop.join(10)
    collected = [body["options"]["platform_order"][0] for method, path, body in server.calls
                 if path == "/api/workbench/task" and method == "POST" and body.get("mode") != "monitor"]
    assert set(collected) == {"boss", "liepin"}                                    # the ones left out are never touched
    delivers = [body for method, path, body in server.calls if path == "/api/workbench/deliver"]
    assert delivers and all(all(job.startswith("liepin") for job in body["job_ids"]) for body in delivers)   # manual: collected, never delivered
    state = loop.snapshot()
    assert state["modes"]["boss"] == "manual" and state["order"] == ["boss", "liepin"]


def test_campus_is_always_manual_and_at_least_one_platform_must_take_part(tmp_path):
    import pytest

    loop = _loop(FakeServer(), tmp_path)
    loop.start({"modes": {"zhilian_campus": "auto", "zhilian": "off", "boss": "off", "51job": "off", "liepin": "off"}, "end_time": "09:05"})
    loop.join(10)
    assert loop.snapshot()["modes"]["zhilian_campus"] == "manual"
    with pytest.raises(ValueError):
        _loop(FakeServer(), tmp_path / "x").start({"modes": {p: "off" for p in ("zhilian_campus", "zhilian", "boss", "51job", "liepin")}})
    with pytest.raises(ValueError):
        _loop(FakeServer(), tmp_path / "y").start({"modes": {"boss": "sometimes"}})


def test_the_cap_ends_the_loop_when_every_automatic_platform_is_capped_even_if_others_are_manual(tmp_path):
    server = FakeServer()
    server.confirm = [{"id": f"liepin:{n}", "source_platform": "liepin"} for n in range(6)]
    loop = _loop(server, tmp_path)
    loop.start({"modes": {"zhilian_campus": "off", "zhilian": "off", "boss": "manual", "51job": "off", "liepin": "auto"}, "end_time": "12:00", "delivery_cap": 3})
    loop.join(10)
    assert "上限" in loop.snapshot()["message"]


class LimitServer(FakeServer):
    """Like the real program once a platform's day-long limit is reached: delivering stops with that reason, or collecting does."""

    def __init__(self, deliver_stop=None, collect_reason=None, only=None, **kwargs):
        super().__init__(**kwargs)
        self.deliver_stop, self.collect_reason, self.only = deliver_stop, collect_reason, only
        self.kind, self.delivering = "", ""

    def sleep(self, seconds):
        super().sleep(seconds)
        if self.last and self.kind == "deliver" and self.deliver_stop and self.only in (None, self.delivering):
            self.last = {**self.last, "stop_reason": self.deliver_stop}

    def api(self, method, path, body=None):
        if method == "POST" and path == "/api/workbench/deliver":
            self.kind = "deliver"
            self.delivering = body["job_ids"][0].split(":")[0]
        if method == "POST" and path == "/api/workbench/task" and self.collect_reason:
            self.calls.append((method, path, body))
            self.kind = "collect"
            platform = body["options"]["platform_order"][0]
            return self._start({"collect_new": 0}, {platform: {"status": "completed_with_shortage", "reason_code": self.collect_reason, "message": ""}})
        if method == "POST" and path == "/api/workbench/task":
            self.kind = "collect"
        return super().api(method, path, body)


def _events(loop):
    events = []
    loop.on_event = lambda kind, message, **kwargs: events.append((kind, message, kwargs))
    return events


def test_the_loop_ends_when_the_only_platform_has_hit_its_day_long_limit():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        server = LimitServer(deliver_stop="daily_platform_page_limit")
        server.confirm = [{"id": "boss:1", "source_platform": "boss"}]
        loop = _loop(server, Path(tmp))
        events = _events(loop)
        _run(loop, ["boss"], end="12:00")
        snapshot = loop.snapshot()
        assert snapshot["running"] is False and snapshot["step"] == "finished"
        assert "BOSS 直聘「BOSS 单日页面访问总上限」" in snapshot["message"] and "每日循环结束" in snapshot["message"]
        assert snapshot["out_today"]["boss"]["label"] == "BOSS 单日页面访问总上限"
        assert server.t < datetime(2026, 10, 22, 10, 0)                       # it did not wait for the end time
        warning = [e for e in events if e[2].get("data", {}).get("limit")]
        assert warning and warning[0][2]["data"]["href"] == "/config?section=safety&focus=BOSS%20%E5%8D%95%E6%97%A5%E9%A1%B5%E9%9D%A2%E8%AE%BF%E9%97%AE%E6%80%BB%E4%B8%8A%E9%99%90"


def test_one_platform_out_for_the_day_is_skipped_while_the_others_go_on():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        server = LimitServer(deliver_stop="daily_platform_page_limit", only="boss")
        server.confirm = [{"id": "boss:1", "source_platform": "boss"}]
        server.waiting = [{"id": "liepin:1", "source_platform": "liepin", "greeting": ""}]
        loop = _loop(server, Path(tmp))
        _run(loop, ["boss", "liepin"], end="09:40")
        boss_collects = [c for c in server.calls if c[1] == "/api/workbench/task" and c[2]["options"]["platform_order"] == ["boss"]]
        assert len(boss_collects) == 1                                          # not tried again after its limit
        assert loop.snapshot()["out_today"].keys() == {"boss"}


def test_a_platform_whose_collecting_is_over_but_has_nothing_left_to_deliver_is_done():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        server = LimitServer(collect_reason="daily_search_page_limit")
        loop = _loop(server, Path(tmp))
        _run(loop, ["boss"], end="12:00")
        out = loop.snapshot()["out_today"]["boss"]
        assert out["label"] == "BOSS 单日搜索页上限" and loop.snapshot()["step"] == "finished"


def test_the_names_the_loop_uses_for_limits_are_the_names_on_the_settings_page():
    from pathlib import Path

    from bosshunter.daily_loop import limit_label

    index = (Path(__file__).parent.parent / "src/bosshunter/web/frontend/src/lib/settingsIndex.ts").read_text(encoding="utf-8")
    labels = [limit_label("daily_platform_page_limit", "boss"), limit_label("daily_detail_page_limit", "boss"), limit_label("daily_limit", "boss")]
    labels += [limit_label("daily_search_page_limit", platform) for platform in ("boss", "51job", "liepin", "zhilian", "zhilian_campus")]
    for label in labels:
        assert f"label: '{label}'" in index, label


def test_without_a_time_window_the_loop_waits_for_tomorrow_when_every_platform_is_out_and_goes_on(tmp_path):
    server = LimitServer(deliver_stop="daily_platform_page_limit", only="boss")
    server.confirm = [{"id": "boss:1", "source_platform": "boss"}]
    loop = DailyLoop(tmp_path / "loop.json", server.api, now=server.now, sleep=lambda s: None, poll_seconds=60, idle_seconds=600)
    seen = {"out": False, "waiting": False, "boss_collects_after_midnight": 0}
    midnight = datetime(2026, 10, 23, 0, 0)

    def sleep(seconds):
        server.sleep(seconds)
        state = loop.state
        seen["out"] = seen["out"] or bool(state.get("out_today"))
        seen["waiting"] = seen["waiting"] or "等到明天自动继续" in str(state.get("message"))
        if server.t >= midnight + timedelta(hours=3):
            loop.stop()

    loop.sleep = sleep
    loop.start({"platform_order": ["boss"], "time_window": False})
    loop.join(30)
    boss_collects = [(i, c) for i, c in enumerate(server.calls) if c[1] == "/api/workbench/task" and c[2]["options"].get("platform_order") == ["boss"]]
    assert seen["out"] and seen["waiting"]                                  # it used up the day and said it waits, instead of ending
    assert len(boss_collects) >= 2                                          # and collected again after the day changed
    assert loop.snapshot()["ended"]["reason"] == "user"                     # it ended only because the test stopped it


def test_a_round_that_fails_does_not_end_the_loop(tmp_path):
    server = FakeServer()
    original = server.api
    failures = {"left": 2}

    def flaky(method, path, body=None):
        if path == "/api/workbench" and method == "GET" and failures["left"] > 0 and any(c[1] == "/api/workbench/task" for c in server.calls):
            failures["left"] -= 1
            raise RuntimeError("boom")
        return original(method, path, body)

    loop = DailyLoop(tmp_path / "loop.json", flaky, now=server.now, sleep=server.sleep, poll_seconds=60, idle_seconds=600)
    loop.start({"platform_order": ["liepin"], "end_time": "11:00"})
    loop.join(20)
    ended = loop.snapshot()["ended"]
    assert ended["reason"] == "end_time"                                    # it went on until its own end time
    failed = [i for i in loop.snapshot()["incidents"] if i["code"] == "round_failed"]
    assert len(failed) == 1 and failed[0]["count"] == 2                      # the same trouble twice is one entry seen twice


def test_a_loop_without_a_window_is_resumed_after_a_restart(tmp_path):
    import json

    server = FakeServer()
    (tmp_path / "loop.json").write_text(json.dumps({"running": True, "end_at": "", "order": ["boss"], "modes": {"boss": "auto"}, "targets": {"boss": 5}, "delivery_cap": 150}))
    loop = _loop(server, tmp_path)
    loop.poll_seconds = 1
    started = []
    loop._launch = lambda: started.append(True)
    assert loop.resume() is True and started


def test_a_window_across_midnight_ends_the_next_morning():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        server = FakeServer()
        loop = _loop(server, Path(tmp))
        loop._launch = lambda: None
        loop.start({"platform_order": ["liepin"], "start_time": "22:00", "end_time": "06:00"})
        assert loop.snapshot()["end_at"] == "2026-10-23T06:00:00"             # the fake clock stands at 09:00 on the 22nd


def test_the_day_count_starts_again_after_midnight():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        server = FakeServer()
        loop = _loop(server, Path(tmp))
        loop._launch = lambda: None
        loop.start({"platform_order": ["boss"], "time_window": False, "delivery_cap": 10})
        loop.state["totals"] = {"boss": {"delivered": 10}}
        loop._mark_out("boss", "daily_platform_page_limit")
        assert loop._capped("boss") and loop._out("boss")
        server.t += timedelta(days=1)
        loop._roll_day()
        assert not loop._capped("boss") and not loop._out("boss") and loop._delivered("boss") == 0
        loop.state["totals"] = {"boss": {"delivered": 14}}
        assert loop._delivered("boss") == 4


def test_after_delivering_the_loop_collects_again_instead_of_stopping(tmp_path):
    server = FakeServer()
    loop = _loop(server, tmp_path)
    _run(loop, ["liepin"], end="10:30")
    steps = [("collect" if path == "/api/workbench/task" else "deliver") for method, path, body in server.calls
             if method == "POST" and path in ("/api/workbench/task", "/api/workbench/deliver")]
    assert steps[:3] == ["collect", "deliver", "collect"]                   # the second round starts with a collection
    assert loop.snapshot()["round"] >= 2


def test_the_loop_says_why_it_ended(tmp_path):
    server = FakeServer()
    loop = _loop(server, tmp_path / "a")
    _run(loop, ["liepin"], end="09:20")
    assert loop.snapshot()["message"] == "已到结束时间 09:20，每日循环结束"
    user = _loop(FakeServer(), tmp_path / "b")
    user._launch = lambda: None
    user.start({"platform_order": ["liepin"], "time_window": False})
    user.stop()
    user._finish()
    assert user.snapshot()["message"] == "你已手动停止每日循环" and user.snapshot()["stopped_reason"] == "user"


def test_the_collection_and_delivery_time_limits_come_from_the_settings_and_can_be_overridden(tmp_path):
    loop = DailyLoop(tmp_path / "loop.json", FakeServer().api, now=FakeServer().now, sleep=lambda _s: None,
                     settings=lambda: {"daily_loop_collect_minutes": 30, "daily_loop_deliver_minutes": 120})
    loop._launch = lambda: None
    loop.start({"platform_order": ["liepin"], "time_window": False})
    assert (loop.snapshot()["collect_minutes"], loop.snapshot()["deliver_minutes"]) == (30, 120)
    loop.stop()
    other = DailyLoop(tmp_path / "other.json", FakeServer().api, now=FakeServer().now, sleep=lambda _s: None, settings=lambda: {"daily_loop_collect_minutes": 30})
    other._launch = lambda: None
    other.start({"platform_order": ["liepin"], "time_window": False, "collect_minutes": 45})
    assert (other.snapshot()["collect_minutes"], other.snapshot()["deliver_minutes"]) == (45, 240)       # not set: the built-in 240
    unset = DailyLoop(tmp_path / "unset.json", FakeServer().api, now=FakeServer().now, sleep=lambda _s: None)
    unset._launch = lambda: None
    unset.start({"platform_order": ["liepin"], "time_window": False})
    assert (unset.snapshot()["collect_minutes"], unset.snapshot()["deliver_minutes"]) == (90, 240)


def test_the_loop_checks_boss_for_replies_now_and_then_with_a_read_only_pass(tmp_path):
    server = FakeServer()
    loop = DailyLoop(tmp_path / "loop.json", server.api, now=server.now, sleep=server.sleep, poll_seconds=60, idle_seconds=600,
                     settings=lambda: {"daily_loop_monitor_minutes": 30})
    loop.start({"platform_order": ["boss"], "end_time": "11:00"})
    loop.join(20)
    monitors = [body for method, path, body in server.calls if method == "POST" and path == "/api/workbench/task" and (body or {}).get("mode") == "monitor"]
    assert monitors and all(body["options"] == {"once": True} for body in monitors)
    assert len(monitors) >= 2                                        # more than once in two hours, but not every round
    rounds = loop.snapshot()["round"]
    assert len(monitors) < rounds


def test_replies_are_not_checked_when_it_is_switched_off_or_boss_is_not_part_of_the_loop(tmp_path):
    for name, settings, order in (("off", {"daily_loop_monitor_minutes": 0}, ["boss"]), ("other", {"daily_loop_monitor_minutes": 30}, ["liepin"])):
        server = FakeServer()
        loop = DailyLoop(tmp_path / f"{name}.json", server.api, now=server.now, sleep=server.sleep, poll_seconds=60, idle_seconds=600, settings=lambda s=settings: s)
        loop.start({"platform_order": order, "end_time": "09:40"})
        loop.join(20)
        assert not [1 for method, path, body in server.calls if method == "POST" and (body or {}).get("mode") == "monitor"], name


def test_every_run_is_kept_with_how_it_started_and_why_it_ended(tmp_path):
    server = FakeServer()
    loop = _loop(server, tmp_path)
    events = _events(loop)
    loop.start({"platform_order": ["liepin"], "end_time": "09:20", "started_by": "schedule"})
    loop.join(10)
    ended = loop.snapshot()["ended"]
    assert ended["reason"] == "end_time" and ended["started_by"] == "schedule" and ended["needs_attention"] is False
    assert ended["message"] == "已到结束时间 09:20，每日循环结束" and ended["rounds"] >= 1
    runs = loop.history()
    assert len(runs) == 1 and runs[0]["reason"] == "end_time"
    assert events[-1][2]["level"] == "info" and events[-1][2]["data"] == {"reason": "end_time", "needs_attention": False}


def test_a_run_that_died_asks_for_a_decision_and_says_what_to_do(tmp_path):
    server = FakeServer()
    original = server.api

    def broken(method, path, body=None):
        if path == "/api/workbench" and method == "GET" and any(c[1] == "/api/workbench/task" for c in server.calls):
            raise RuntimeError("boom")                                     # outside ApiError: the loop itself breaks
        return original(method, path, body)

    loop = DailyLoop(tmp_path / "loop.json", broken, now=server.now, sleep=server.sleep, poll_seconds=60, idle_seconds=600)
    events = _events(loop)
    loop.start({"platform_order": ["liepin"], "end_time": "11:00"})
    loop.join(10)
    ended = loop.snapshot()["ended"]
    assert ended["reason"] == "crash" and ended["needs_attention"] is True and "重新启动" in ended["suggestion"]
    assert events[-1][2]["level"] == "warning" and events[-1][2]["data"]["needs_attention"] is True


def test_using_up_every_platform_is_worth_a_look_and_a_manual_stop_is_not(tmp_path):
    from bosshunter.daily_loop import STOP_REASONS

    assert STOP_REASONS["limit"][1] is True and STOP_REASONS["crash"][1] is True and STOP_REASONS["unknown"][1] is True
    assert not any(STOP_REASONS[reason][1] for reason in ("user", "end_time", "cap"))
    user = _loop(FakeServer(), tmp_path)
    user._launch = lambda: None
    user.start({"platform_order": ["liepin"], "time_window": False})
    user.stop()
    user._finish()
    assert user.history()[0]["reason"] == "user" and user.history()[0]["needs_attention"] is False


def test_history_is_newest_first_and_bounded(tmp_path):
    loop = _loop(FakeServer(), tmp_path)
    loop._launch = lambda: None
    for number in range(35):
        loop.state = {"started_at": f"2026-10-22T0{number % 10}:00:00", "round": number, "totals": {}, "incidents": []}
        loop._record_run("end_time", f"第 {number} 次")
    runs = loop.history(100)
    assert len(runs) == 30 and runs[0]["message"] == "第 34 次" and runs[-1]["message"] == "第 5 次"


def test_a_change_of_the_time_limits_in_the_settings_counts_without_restarting_the_loop(tmp_path):
    current = {"daily_loop_collect_minutes": 90, "daily_loop_deliver_minutes": 240}
    loop = DailyLoop(tmp_path / "loop.json", FakeServer().api, now=FakeServer().now, sleep=lambda _s: None, settings=lambda: current)
    loop._launch = lambda: None
    loop.start({"platform_order": ["liepin"], "time_window": False})
    assert (loop._limit_minutes("collect"), loop._limit_minutes("deliver")) == (90, 240)
    current["daily_loop_collect_minutes"] = 30                                  # the user changes it on the settings page
    assert loop._limit_minutes("collect") == 30 and loop.snapshot()["collect_minutes"] == 30
    loop.stop()
    pinned = DailyLoop(tmp_path / "p.json", FakeServer().api, now=FakeServer().now, sleep=lambda _s: None, settings=lambda: current)
    pinned._launch = lambda: None
    pinned.start({"platform_order": ["liepin"], "time_window": False, "collect_minutes": 45})
    current["daily_loop_collect_minutes"] = 20
    assert pinned._limit_minutes("collect") == 45                              # a value given in the start itself stays


def test_hitting_the_collection_time_limit_is_a_routine_stop_but_a_delivery_that_overruns_is_an_incident(tmp_path):
    server = FakeServer()
    server.running = 10 ** 6                                             # a task that never ends by itself
    loop = DailyLoop(tmp_path / "loop.json", server.api, now=server.now, sleep=lambda s: setattr(server, "t", server.t + timedelta(seconds=s)),
                     poll_seconds=60, idle_seconds=600, settings=lambda: {"daily_loop_collect_minutes": 30})
    events = _events(loop)
    loop._launch = lambda: None
    loop.start({"platform_order": ["liepin"], "time_window": False})
    stopped = []
    original = server.api

    def api(method, path, body=None):
        if path.endswith("/stop"):
            stopped.append(path)
            server.running = 0
            return {}
        return original(method, path, body)

    loop.api = api
    loop._set(platform="liepin", step="collect")
    loop._wait_idle("猎聘：采集中", routine=True)
    assert stopped and not loop.snapshot()["incidents"]                  # stopped, and no entry in the incident list
    assert any("已到单次上限 30 分钟" in message for _kind, message, _kw in events)
    server.running = 10 ** 6
    stopped.clear()
    loop._wait_idle("猎聘：投递中", limit_minutes=30)
    assert [i["code"] for i in loop.snapshot()["incidents"]] == ["task_too_long"]


def test_after_a_restart_the_first_round_goes_on_with_the_platform_that_was_in_progress(tmp_path):
    import json

    server = FakeServer()
    (tmp_path / "loop.json").write_text(json.dumps({
        "running": True, "end_at": "", "order": ["liepin", "zhilian"], "modes": {"liepin": "auto", "zhilian": "auto"}, "targets": {"liepin": 5, "zhilian": 5},
        "delivery_cap": 150, "platform": "zhilian", "step": "collect", "round": 3, "monitor_minutes": 0}))
    loop = _loop(server, tmp_path)
    original_launch = loop._launch
    loop._launch = lambda: None
    assert loop.resume() is True
    assert loop._resume_from == "zhilian"
    loop._launch = original_launch
    loop._set(running=True)
    loop._launch()
    for _ in range(200):                                                  # let the first round run, then stop it
        if any(c[1] == "/api/workbench/task" for c in server.calls):
            break
        loop.join(0.02)
    loop.stop()
    loop.join(10)
    first = next(c for c in server.calls if c[1] == "/api/workbench/task" and c[0] == "POST")
    assert first[2]["options"]["platform_order"] == ["zhilian"]           # not liepin, the first one of the order


def test_a_state_file_saved_with_a_byte_order_mark_is_still_read(tmp_path):
    import json

    (tmp_path / "loop.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"running": True, "round": 7, "platform": "51job"}).encode("utf-8"))
    loop = _loop(FakeServer(), tmp_path)
    assert loop.state["round"] == 7 and loop.state["platform"] == "51job"      # not silently treated as "no loop was running"


def _sliced_loop(tmp_path, settings):
    server = FakeServer()
    loop = DailyLoop(tmp_path / "loop.json", server.api, now=server.now, sleep=server.sleep, poll_seconds=60, idle_seconds=600, settings=lambda: settings)
    loop._launch = lambda: None
    loop.start({"platform_order": ["liepin"], "time_window": False})
    return loop


def test_collecting_is_cut_into_slices_and_what_a_slice_found_is_delivered_before_the_next_one(tmp_path):
    loop = _sliced_loop(tmp_path, {"daily_loop_collect_minutes": 45, "daily_loop_slice_minutes": 15})
    calls = []

    def collect(platform, limit=None):
        calls.append(("collect", limit))
        loop._cut = len(calls) < 5                                   # the first slices end by their limit, the last one by itself
        return 2

    loop._collect = collect
    loop._deliver = lambda platform: calls.append(("deliver",))
    loop._platform("liepin")
    assert calls == [("collect", 15), ("deliver",), ("collect", 15), ("deliver",), ("collect", 15), ("deliver",)]    # 45 minutes in all


def test_without_a_slice_setting_the_collection_is_one_piece_followed_by_the_delivery(tmp_path):
    loop = _sliced_loop(tmp_path, {"daily_loop_collect_minutes": 45, "daily_loop_slice_minutes": 0})
    calls = []
    loop._collect = lambda platform, limit=None: (calls.append(("collect", limit)), setattr(loop, "_cut", True), 2)[-1]
    loop._deliver = lambda platform: calls.append(("deliver",))
    loop._platform("liepin")
    assert calls == [("collect", 45), ("deliver",)]


def test_a_collection_that_ends_by_itself_is_not_repeated(tmp_path):
    loop = _sliced_loop(tmp_path, {"daily_loop_collect_minutes": 45, "daily_loop_slice_minutes": 15})
    calls = []
    loop._collect = lambda platform, limit=None: (calls.append(("collect", limit)), 0)[-1]      # blocked, finished or out of postings
    loop._deliver = lambda platform: calls.append(("deliver",))
    loop._platform("liepin")
    assert calls == [("collect", 15), ("deliver",)]


def _planner(tmp_path, settings=None):
    server = FakeServer()
    loop = DailyLoop(tmp_path / "loop.json", server.api, now=server.now, sleep=server.sleep, poll_seconds=60, idle_seconds=600, settings=lambda: settings or {})
    loop._launch = lambda: None
    loop.start({"platform_order": ["zhilian", "boss", "51job"], "time_window": False})
    return server, loop


def test_the_collecting_time_goes_to_the_platforms_that_bring_in_new_postings(tmp_path):
    _server, loop = _planner(tmp_path)
    loop.state["perf"] = {"zhilian": {"minutes": 100, "new": 120}, "boss": {"minutes": 100, "new": 25}, "51job": {"minutes": 100, "new": 1}}
    plan = loop._round_plan(["51job", "boss", "zhilian"])
    assert [p for p, _ in plan] == ["zhilian", "boss", "51job"]                     # the best platform first
    minutes = dict(plan)
    assert minutes["zhilian"] > minutes["boss"] > minutes["51job"]
    assert minutes["51job"] >= 10 and minutes["zhilian"] <= 0.61 * sum(minutes.values())          # a dry platform is still looked at, nobody takes the round
    assert sum(minutes.values()) >= 270 - 3                                         # as much time as three fixed collections


def test_platforms_without_data_are_treated_like_the_average_and_the_switch_turns_the_plan_off(tmp_path):
    _server, loop = _planner(tmp_path)
    plan = dict(loop._round_plan(["zhilian", "boss", "51job"]))
    assert len({round(value) for value in plan.values()}) == 1                      # no data: equal shares
    _server, off = _planner(tmp_path / "off", {"daily_loop_dynamic": False}) if (tmp_path / "off").mkdir() is None else (None, None)
    assert [m for _, m in off._round_plan(["zhilian", "boss", "51job"])] == [None, None, None]


def test_a_platform_that_is_held_or_has_no_automatic_delivery_keeps_its_place_and_gets_no_minutes(tmp_path):
    _server, loop = _planner(tmp_path)
    loop.state["modes"]["boss"] = "manual"
    plan = loop._round_plan(["zhilian", "boss", "51job"])
    assert [p for p, _ in plan][1] == "boss" and dict(plan)["boss"] is None


def test_a_captcha_holds_the_platform_until_the_user_says_it_is_solved(tmp_path):
    server, loop = _planner(tmp_path)
    events = _events(loop)
    last = {"status": "completed", "progress": {"platforms": {"boss": {"status": "blocked", "reason_code": "captcha", "message": "BOSS 采集检测到验证码"}}}, "logs": []}
    loop._report_collect_problems("boss", last)
    assert "boss" in loop.snapshot()["held"] and any("验证码" in message for _kind, message, _kw in events)
    calls = len(server.calls)
    assert loop._platform("boss") == 0 and len(server.calls) == calls              # not collected, not delivered, no request at all
    assert loop.release("boss") is True and "boss" not in loop.snapshot()["held"]
    assert all(item["resolved"] for item in loop.snapshot()["incidents"] if item["code"] == "captcha")
    assert loop.release("boss") is False


def test_a_held_platform_stays_held_over_midnight_and_the_user_is_asked_again(tmp_path):
    server, loop = _planner(tmp_path)
    events = _events(loop)
    loop._hold("boss", "captcha", "验证码")
    assert loop.skip_today("boss") and loop.snapshot()["held"]["boss"]["skipped_day"] == loop.now().date().isoformat()
    server.t += timedelta(days=1)
    loop._roll_day()
    assert "boss" in loop.snapshot()["held"]                                       # nobody said it was solved
    assert any("新的一天" in message for _kind, message, _kw in events)


def test_the_loop_waits_for_tomorrow_when_every_platform_is_held_or_out(tmp_path):
    _server, loop = _planner(tmp_path)
    for platform in ("zhilian", "boss", "51job"):
        loop._hold(platform, "captcha", "x")
    assert loop._all_out()


def test_the_loop_says_why_it_stops_a_collection_so_that_it_is_not_taken_for_the_user(tmp_path):
    server = FakeServer()
    server.running = 10 ** 6
    loop = DailyLoop(tmp_path / "loop.json", server.api, now=server.now, sleep=lambda s: setattr(server, "t", server.t + timedelta(seconds=s)),
                     poll_seconds=60, idle_seconds=600, settings=lambda: {"daily_loop_collect_minutes": 30})
    loop._launch = lambda: None
    loop.start({"platform_order": ["liepin"], "time_window": False})
    bodies = []
    original = server.api

    def api(method, path, body=None):
        if path.endswith("/stop"):
            bodies.append(body)
            server.running = 0
            return {}
        return original(method, path, body)

    loop.api = api
    loop._set(platform="liepin", step="collect")
    loop._wait_idle("猎聘：采集中", routine=True)
    assert bodies and bodies[0]["reason"].startswith("每日循环分段")
