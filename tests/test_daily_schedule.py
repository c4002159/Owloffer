from datetime import datetime, timedelta

import pytest

from bosshunter.daily_schedule import DailySchedule, parse_time


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def _schedule(tmp_path, clock, started=None, running=False, refuse=None, events=None):
    started = [] if started is None else started

    def start(options):
        if refuse:
            raise ValueError(refuse)
        started.append(options)

    return DailySchedule(tmp_path / "s.json", start, lambda: running, now=clock,
                         on_event=(lambda kind, message, **kw: events.append((message, kw.get("level")))) if events is not None else None), started


def test_it_starts_the_loop_once_a_day_at_the_chosen_time_with_the_saved_options(tmp_path):
    clock = Clock(datetime(2026, 10, 24, 7, 29))
    events = []
    schedule, started = _schedule(tmp_path, clock, events=events)
    schedule.update(True, "07:30", {"modes": {"boss": "auto"}, "monitor_minutes": 60})
    assert schedule.tick() is False and not started                       # not yet
    clock.at = datetime(2026, 10, 24, 7, 30, 5)
    assert schedule.tick() is True
    assert started == [{"modes": {"boss": "auto"}, "monitor_minutes": 60}]
    assert schedule.tick() is False and len(started) == 1                  # one start a day
    assert events == [("已按定时（每天 07:30）启动每日循环", "info")]
    clock.at = datetime(2026, 10, 25, 7, 31)
    assert schedule.tick() is True and len(started) == 2                   # and again the next day


def test_it_does_nothing_while_switched_off_and_the_setting_survives_a_restart(tmp_path):
    clock = Clock(datetime(2026, 10, 24, 8, 0))
    schedule, started = _schedule(tmp_path, clock)
    schedule.update(False, "07:30", {"x": 1})
    assert schedule.tick() is False and not started and schedule.snapshot()["next_run_at"] == ""
    schedule.update(True, "09:15", {"x": 1})
    again, _ = _schedule(tmp_path, clock)                                  # the program was restarted
    assert again.snapshot()["enabled"] is True and again.snapshot()["time"] == "09:15" and again.snapshot()["options"] == {"x": 1}


def test_a_late_start_still_happens_within_the_grace_and_a_missed_day_is_skipped(tmp_path):
    clock = Clock(datetime(2026, 10, 24, 9, 0))                            # the program came up 1.5 hours late
    schedule, started = _schedule(tmp_path, clock)
    schedule.update(True, "07:30", {})
    assert schedule.snapshot()["next_run_at"].startswith("2026-10-24T09:00")
    assert schedule.tick() is True and len(started) == 1
    clock.at = datetime(2026, 10, 25, 13, 0)                               # 5.5 hours late: too late
    assert schedule.tick() is False and len(started) == 1
    assert "没有赶上" in schedule.snapshot()["last_error"]
    assert schedule.snapshot()["next_run_at"].startswith("2026-10-26T07:30")


def test_a_loop_that_is_already_running_is_left_alone_and_a_refusal_is_reported(tmp_path):
    clock = Clock(datetime(2026, 10, 24, 7, 31))
    schedule, started = _schedule(tmp_path, clock, running=True)
    schedule.update(True, "07:30", {})
    assert schedule.tick() is False and not started                        # started by hand already
    events = []
    refusing, _ = _schedule(tmp_path / "r", clock, refuse="结束时间 06:00 已经过了", events=events)
    refusing.update(True, "07:30", {})
    assert refusing.tick() is False
    assert "定时启动被拒绝：结束时间 06:00 已经过了" in refusing.snapshot()["last_error"]
    assert events and events[0][1] == "warning"


def test_the_time_is_a_24_hour_clock_time(tmp_path):
    assert parse_time("07:30") == (7, 30) and parse_time("19:05") == (19, 5)
    for bad in ("25:00", "7:75", "abc", "-1:00"):
        with pytest.raises(ValueError):
            parse_time(bad)
    schedule, _ = _schedule(tmp_path, Clock(datetime(2026, 10, 24, 7, 0)))
    with pytest.raises(ValueError):
        schedule.update(True, "24:30", {})
    assert schedule.snapshot()["enabled"] is False                          # nothing was saved
