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
    collected = [body["options"]["platform_order"][0] for method, path, body in server.calls if path == "/api/workbench/task" and method == "POST"]
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


def test_without_a_time_window_the_loop_has_no_end_time_and_stops_only_when_nothing_is_left():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        server = LimitServer(deliver_stop="daily_platform_page_limit")
        server.confirm = [{"id": "boss:1", "source_platform": "boss"}]
        loop = _loop(server, Path(tmp))
        loop.start({"platform_order": ["boss"], "time_window": False})
        loop.join(10)
        snapshot = loop.snapshot()
        assert snapshot["end_at"] == "" and snapshot["running"] is False                # it ended because the platform was out, not by the clock
        assert "额度" in snapshot["message"]


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
