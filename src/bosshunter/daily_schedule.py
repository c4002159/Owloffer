"""The daily loop's own timer: start it every day at a chosen time, without Claude, Codex or a browser tab being open.

The user turns it on in the app (the daily loop's settings) and picks a time on the 24-hour clock. The options the loop is to be
started with (platforms and how they take part, targets, delivery limit, time window, reply check) are saved with it, so the
start at 07:30 is the same start the user would have made by hand.

Rules:
* one start a day: a day on which the loop was started (by the timer, or by anybody) is not started again;
* a start that is late (the program was off at the time) still happens, within ``GRACE_HOURS`` of the chosen time; after that
  today is skipped and the next start is tomorrow;
* a loop that is already running is left alone;
* nothing is started that the loop itself refuses (e.g. an end time that has passed): the reason is kept in ``last_error``.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

GRACE_HOURS = 4
POLL_SECONDS = 30
DEFAULT_TIME = "07:30"


def parse_time(value: object) -> tuple[int, int]:
    text = str(value or DEFAULT_TIME)
    hour, _, minute = text.partition(":")
    try:
        hour_value, minute_value = int(hour), int(minute or 0)
    except ValueError as exc:
        raise ValueError("时间要写成 24 小时制的 时:分，例如 07:30") from exc
    if not (0 <= hour_value <= 23 and 0 <= minute_value <= 59):
        raise ValueError("时间要写成 24 小时制的 时:分，例如 07:30")
    return hour_value, minute_value


class DailySchedule:
    def __init__(
        self,
        path: Path,
        start_loop: Callable[[dict], Any],
        loop_running: Callable[[], bool],
        *,
        now: Callable[[], datetime] = datetime.now,
        on_event: Callable[..., Any] | None = None,
    ):
        self.path = Path(path)
        self.start_loop, self.loop_running, self.now, self.on_event = start_loop, loop_running, now, on_event
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.state = self._load()

    # ------------------------------------------------------------------ state
    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = {"enabled": False, "time": DEFAULT_TIME, "options": {}, "last_start_day": "", "last_start_at": "", "last_error": "", **self.state}
            state["override"] = self._override_today()                    # only today's change counts; older ones are gone
            state["next_run_at"] = self.next_run()
            return state

    def _override_today(self) -> dict | None:
        override = self.state.get("override")
        if isinstance(override, dict) and override.get("day") == self.now().date().isoformat():
            return override
        return None

    def override(self, action: str, time_of_day: object = None) -> dict[str, Any]:
        """Change only today: ``skip`` (no automatic start today), ``time`` (start today at another time) or ``clear``."""
        if action not in {"skip", "time", "clear"}:
            raise ValueError("只能是 skip（今天不启动）、time（今天改到另一个时间）或 clear（取消临时更改）")
        with self._lock:
            if action == "clear":
                self.state.pop("override", None)
            elif action == "skip":
                self.state["override"] = {"day": self.now().date().isoformat(), "action": "skip"}
            else:
                hour, minute = parse_time(time_of_day)
                self.state["override"] = {"day": self.now().date().isoformat(), "action": "time", "time": f"{hour:02d}:{minute:02d}"}
            self._save()
        return self.snapshot()

    def update(self, enabled: bool, time_of_day: object, options: dict | None) -> dict[str, Any]:
        parse_time(time_of_day)                                   # refuse nonsense before anything is saved
        with self._lock:
            self.state["enabled"] = bool(enabled)
            self.state["time"] = f"{parse_time(time_of_day)[0]:02d}:{parse_time(time_of_day)[1]:02d}"
            if isinstance(options, dict):
                self.state["options"] = options
            self.state["last_error"] = ""
            self.state.pop("override", None)                              # a new regular time replaces a one-off change
            self._save()
        return self.snapshot()

    # ------------------------------------------------------------------ when
    def _planned(self, day: datetime) -> datetime:
        override = self._override_today()
        hour, minute = parse_time(override["time"] if override and override.get("action") == "time" else self.state.get("time"))
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def next_run(self) -> str:
        """When the timer will start the loop next (ISO), or an empty string when it is off."""
        if not self.state.get("enabled"):
            return ""
        now = self.now()
        today = self._planned(now)
        override = self._override_today()
        started_today = self.state.get("last_start_day") == now.date().isoformat() or bool(override and override.get("action") == "skip")
        if not started_today and now <= today + timedelta(hours=GRACE_HOURS):
            return (today if now <= today else now).isoformat(timespec="seconds")      # late but within the grace: at the next tick
        return (today + timedelta(days=1)).isoformat(timespec="seconds")

    def tick(self) -> bool:
        """Start the loop when it is time. Returns whether it was started."""
        with self._lock:
            if not self.state.get("enabled"):
                return False
            now = self.now()
            day = now.date().isoformat()
            planned = self._planned(now)
            override = self._override_today()
            if self.state.get("last_start_day") == day or now < planned or (override and override.get("action") == "skip"):
                return False
            if now > planned + timedelta(hours=GRACE_HOURS):          # too late: today is skipped
                self.state["last_start_day"] = day
                self.state["last_error"] = f"{planned:%H:%M} 的定时启动没有赶上（程序当时没有运行），今天不再补启动"
                self._save()
                return False
            if self.loop_running():
                self.state["last_start_day"] = day                     # somebody started it already today
                self._save()
                return False
            options = dict(self.state.get("options") or {})
            try:
                self.start_loop(options)
            except Exception as exc:                                    # the loop refused (time already past, no platform, ...)
                self.state["last_start_day"] = day
                self.state["last_error"] = f"定时启动被拒绝：{exc}"
                self._save()
                self._news(f"{planned:%H:%M} 的定时启动没有成功：{exc}", "warning")
                return False
            self.state["last_start_day"] = day
            self.state["last_start_at"] = now.isoformat(timespec="seconds")
            self.state["last_error"] = ""
            self._save()
        self._news(f"已按定时（每天 {planned:%H:%M}）启动每日循环", "info")
        return True

    def _news(self, message: str, level: str) -> None:
        if self.on_event is not None:
            try:
                self.on_event("loop", message, level=level)
            except Exception:
                pass

    # ------------------------------------------------------------------ thread
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass                                                      # the next tick tries again
            self._stop.wait(POLL_SECONDS)

    def start(self) -> None:
        threading.Thread(target=self.run, name="daily-schedule", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
