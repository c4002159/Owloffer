"""The fixed daily job: collect platform after platform, approve and deliver what was collected, start over until the end time.

One round goes through the platforms in order (campus first). Per platform: collect until its new-job target is reached, approve
the postings waiting for confirmation (campus postings stay manual), generate the missing greetings and deliver. Everything
that goes wrong, or needs a person, is written to an incident list instead of stopping the loop; the list is shown on the
workbench and returned by the API so a watcher can fix the cause.

The loop talks to the program's own HTTP API, exactly like a person using the workbench, so every check the API makes (preflight,
platform limits, delivery windows, per-job locks) applies unchanged. State is kept in ``data/daily_loop.json`` so a restarted
server continues where it was.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, time as day_time, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

DEFAULT_ORDER = ("zhilian_campus", "zhilian", "boss", "51job", "liepin")
DEFAULT_TARGETS = {"zhilian_campus": 50, "zhilian": 80, "boss": 80, "51job": 80, "liepin": 80}
MANUAL_ONLY = {"zhilian_campus"}                 # collected, scored and greeted, but delivered by the user
POLL_SECONDS = 60
IDLE_SECONDS = 600                              # nothing to collect (window closed, all paused): look again later
SLICE_MINUTES = 15                              # collecting is cut into slices this long, what was found is delivered between them (automation.daily_loop_slice_minutes)
PERF_DECAY = 0.7                                # how much of the earlier collections still counts when a platform's yield is judged
MIN_SHARE, MAX_SHARE = 0.15, 0.6                # no platform gets less than 15 % or more than 60 % of a round's collecting time
MIN_PLAN_MINUTES = 10                           # the least a platform is given in a round (it must be possible to notice that it recovered)
DEFAULT_CONVERSION = 0.3                        # deliveries per new posting until a platform has shown its own rate
MAX_COLLECT_MINUTES = 90                        # one collection that runs longer is stopped (the setting automation.daily_loop_collect_minutes)
MAX_DELIVER_MINUTES = 240                       # the same for one delivery (automation.daily_loop_deliver_minutes)
MODES = ("auto", "manual", "off")      # collect + approve + deliver / collect, then you confirm by hand / not in the loop
DEFAULT_END = "20:00"
DEFAULT_CAP = 150                               # deliveries per platform and day; 0 = no limit
MAX_INCIDENTS = 300
MAX_HISTORY = 30
MAX_FAILED_ROUNDS = 5                          # rounds in a row that raise before the loop gives up
# Why a run ended, how it is told, and whether it deserves a decision from the user (restart, change today's start, ...).
STOP_REASONS = {
    "user": ("你已手动停止每日循环", False, ""),
    "end_time": ("已到结束时间", False, ""),
    "cap": ("各平台都已达到投递上限", False, ""),
    "limit": ("各平台今天都用完了额度", True, "额度明天恢复；也可以到设置里调高上限后现在重新启动"),
    "crash": ("每日循环出错退出", True, "建议现在重新启动；出错原因见异常列表"),
    "unknown": ("每日循环结束，原因不明", True, "先看异常列表，再决定要不要现在重新启动"),
}
GREETING_STATUSES = {"ready", "approved", "error"}      # the statuses the greeting API accepts (not the unscored ones)
PLATFORM_NAMES = {"zhilian_campus": "智联校招", "zhilian": "智联招聘", "boss": "BOSS 直聘", "51job": "前程无忧", "liepin": "猎聘"}
SETTING_NAMES = {"zhilian_campus": "智联校招", "zhilian": "智联招聘", "boss": "BOSS", "51job": "前程无忧", "liepin": "猎聘"}
# Limits that last until tomorrow (or until the user raises them): once a platform hits one, the loop has nothing more to do there.
DAY_LONG_LIMITS = ("daily_platform_page_limit", "daily_search_page_limit", "daily_detail_page_limit", "daily_limit")
# The page budget of a platform is shared by collecting and delivering, so reaching it ends both.
SHARED_LIMITS = ("daily_platform_page_limit", "daily_limit")


def limit_label(code: str, platform: str) -> str:
    """The limit under the same name the settings page gives it (the frontend's settingsIndex.ts is the source of these words)."""
    if code == "daily_platform_page_limit":
        return "BOSS 单日页面访问总上限"
    if code == "daily_detail_page_limit":
        return "BOSS 单日详情页尝试上限"
    if code == "daily_search_page_limit":
        return f"{SETTING_NAMES.get(platform, platform)} 单日搜索页上限"
    return "每日发送上限"


def limit_href(code: str, platform: str) -> str:
    section = "delivery" if code == "daily_limit" else "safety"
    return f"/config?section={section}&focus={quote(limit_label(code, platform))}"


def day_long_limit(text: object) -> str | None:
    """The day-long limit a task's stop reason or a platform's reason code names, if any."""
    text = str(text or "")
    return next((code for code in DAY_LONG_LIMITS if code in text), None)


class ApiError(RuntimeError):
    def __init__(self, status: int, payload: dict):
        super().__init__(str(payload.get("error") or payload)[:300])
        self.status, self.payload = status, payload


def parse_end_time(value: str, now: datetime) -> datetime:
    hour, _, minute = str(value or "14:00").partition(":")
    end = datetime.combine(now.date(), day_time(int(hour or 14), int(minute or 0)))
    return end


class DailyLoop:
    def __init__(
        self,
        state_path: Path,
        api: Callable[[str, str, dict | None], dict],
        *,
        now: Callable[[], datetime] = datetime.now,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = POLL_SECONDS,
        idle_seconds: float = IDLE_SECONDS,
        on_event: Callable[..., Any] | None = None,
        settings: Callable[[], dict] | None = None,
    ):
        self.path = Path(state_path)
        self.api, self.now, self.sleep = api, now, sleep
        self.poll_seconds, self.idle_seconds = poll_seconds, idle_seconds
        self.on_event = on_event
        self.settings = settings or (lambda: {})        # the automation section of the config: collect / deliver time limits
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._collect_closed: set[str] = set()      # platforms whose collecting is over for today (delivering may go on)
        self._resume_from = ""                      # after a restart: the platform the first round starts with
        self._cut = False                           # the last collection was stopped by its time limit (it had more to read)
        self.history_path = self.path.with_name("daily_loop_history.json")
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

    def _news(self, kind: str, message: str, **kwargs: Any) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, message, **kwargs)
            except Exception:
                pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.state, ensure_ascii=False))

    def _set(self, **values: Any) -> None:
        with self._lock:
            self.state.update(values, updated_at=self.now().isoformat(timespec="seconds"))
            self._save()

    def incident(self, platform: str, step: str, code: str, message: str, detail: str = "") -> None:
        """Record something that went wrong or needs a person. The loop goes on."""
        with self._lock:
            items = self.state.setdefault("incidents", [])
            for item in items:                                   # the same trouble again: count it, do not repeat it
                if not item.get("resolved") and (item["platform"], item["step"], item["code"]) == (platform, step, code):
                    item["count"] = int(item.get("count", 1)) + 1
                    item["last_seen"] = self.now().isoformat(timespec="seconds")
                    self._save()
                    return
            self._news("incident", f"{PLATFORM_NAMES.get(platform, '全局')}：{message}", level="warning", platform=platform, data={"code": code, "step": step})
            items.append({
                "id": uuid.uuid4().hex[:8], "time": self.now().isoformat(timespec="seconds"), "last_seen": self.now().isoformat(timespec="seconds"),
                "platform": platform, "platform_name": PLATFORM_NAMES.get(platform, platform), "step": step, "code": code,
                "message": message[:300], "detail": detail[:1500], "count": 1, "resolved": False,
            })
            del items[:-MAX_INCIDENTS]
            self._save()

    def resolve_incident(self, incident_id: str) -> bool:
        with self._lock:
            for item in self.state.get("incidents", []):
                if item.get("id") == incident_id:
                    item["resolved"] = True
                    self._save()
                    return True
        return False

    def _bump(self, platform: str, key: str, amount: int) -> None:
        with self._lock:
            totals = self.state.setdefault("totals", {}).setdefault(platform, {})
            totals[key] = int(totals.get(key, 0)) + int(amount)
            self._save()

    # ------------------------------------------------------------------ control
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, options: dict | None = None) -> dict[str, Any]:
        options = options or {}
        with self._lock:
            if self.running():
                return self.snapshot()
            now = self.now()
            modes = {p: str((options.get("modes") or {}).get(p) or "auto") for p in DEFAULT_TARGETS}
            if any(mode not in MODES for mode in modes.values()):
                raise ValueError("平台模式只能是 auto（全自动）、manual（采集后手动确认）或 off（不参加）")
            modes.update({p: "manual" for p in MANUAL_ONLY if modes[p] != "off"})            # campus postings can only be delivered by hand
            order = [p for p in (options.get("platform_order") or DEFAULT_ORDER) if p in DEFAULT_TARGETS and modes[p] != "off"]
            if not order:
                raise ValueError("至少要有一个平台参加每日循环")
            targets = {**DEFAULT_TARGETS, **{k: int(v) for k, v in (options.get("targets") or {}).items() if k in DEFAULT_TARGETS}}
            # The time window is optional: without it the loop runs around the clock until it is stopped, or until every platform
            # has used up its day (see _all_out). Callers that do not say (the API, Claude/Codex) get the window as before.
            if options.get("time_window", True) is False:
                end, begin = None, None
            else:
                end = parse_end_time(options.get("end_time") or DEFAULT_END, now)
                begin = parse_end_time(options["start_time"], now) if options.get("start_time") else None
                if begin is not None and end <= begin:
                    end += timedelta(days=1)                      # a window across midnight, e.g. 22:00 - 06:00
                if end <= now:
                    raise ValueError(f"结束时间 {end:%H:%M} 已经过了，请把结束时间调到现在之后")
            cap = max(int(options.get("delivery_cap", DEFAULT_CAP) or 0), 0)
            try:
                configured = self.settings() or {}
            except Exception:
                configured = {}
            monitor_minutes = max(self._int(options.get("monitor_minutes", configured.get("daily_loop_monitor_minutes")), 60), 0)
            collect_minutes = self._minutes(options.get("collect_minutes", configured.get("daily_loop_collect_minutes")), MAX_COLLECT_MINUTES)
            deliver_minutes = self._minutes(options.get("deliver_minutes", configured.get("daily_loop_deliver_minutes")), MAX_DELIVER_MINUTES)
            pinned = "collect_minutes" in options or "deliver_minutes" in options     # asked for in the start itself: not changed by the settings later
            self._collect_closed = set()
            started_by = str(options.get("started_by") or "manual")
            self.state = {
                "running": True, "started_at": now.isoformat(timespec="seconds"), "end_at": end.isoformat(timespec="seconds") if end else "", "day": now.date().isoformat(),
                "started_by": started_by, "ended": None, "delivered_base": {}, "minutes_pinned": pinned, "monitor_minutes": monitor_minutes, "last_monitor": "", "collect_minutes": collect_minutes, "deliver_minutes": deliver_minutes,
                "start_at": begin.isoformat(timespec="seconds") if begin and begin > now else "", "delivery_cap": cap,
                "order": order, "modes": modes, "targets": targets, "round": 0, "platform": "", "step": "starting", "message": "每日循环启动",
                "totals": {}, "incidents": [], "options": {k: options[k] for k in ("start_time", "end_time", "time_window", "auto_approve", "delivery_cap") if k in options},
                "stopped_reason": "", "out_today": {},
            }
            self._save()
        self._launch()
        return self.snapshot()

    @staticmethod
    def _int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _minutes(value: Any, default: int) -> int:
        try:
            return min(max(int(value), 10), 720)
        except (TypeError, ValueError):
            return default

    def resume(self) -> bool:
        """Continue after a server restart when the loop was running and its end time has not come."""
        state = self.state
        if not state.get("running") or self.running():
            return False
        end = datetime.fromisoformat(state["end_at"]) if state.get("end_at") else None
        if end is not None and self.now() >= end:
            self._set(running=False, step="finished", message="已到结束时间", stopped_reason="end_time")
            self._record_run("end_time", "程序重启时已过了结束时间")
            return False
        self._news("loop", "程序重启过，每日循环已接着运行", level="info")
        self._resume_from = state.get("platform") or ""        # carry on with the platform that was in progress, not the first one
        self._launch()
        return True

    def _launch(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="daily-loop", daemon=True)
        self._thread.start()

    def stop(self, reason: str = "用户已停止") -> dict[str, Any]:
        self._stop.set()
        self._set(message=reason, stopped_reason="user")
        return self.snapshot()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # ------------------------------------------------------------------ the loop
    def _over(self) -> bool:
        end = datetime.fromisoformat(self.state["end_at"]) if self.state.get("end_at") else None
        if end is None:
            # No time window: the loop does not end because the day's quotas are used up, it waits for tomorrow (_wait_for_tomorrow).
            return self._stop.is_set()
        return self._stop.is_set() or self.now() >= end or self._all_capped() or self._all_out()

    def _delivered(self, platform: str) -> int:
        """Deliveries of the current day (a loop without a time window runs through midnight: the day's count starts again)."""
        total = int((self.state.get("totals") or {}).get(platform, {}).get("delivered", 0))
        return total - int((self.state.get("delivered_base") or {}).get(platform, 0))

    def _roll_day(self) -> None:
        """A new calendar day: what was used up yesterday is available again."""
        today = self.now().date().isoformat()
        if not self.state.get("day"):
            self.state["day"] = today
            return
        if self.state["day"] == today:
            return
        with self._lock:
            self.state["day"] = today
            self.state["delivered_base"] = {p: int(v.get("delivered", 0)) for p, v in (self.state.get("totals") or {}).items()}
            self.state["out_today"] = {}
            self._collect_closed = set()
            held = dict(self.state.get("held") or {})
            self._save()
        for platform in held:                                            # a platform held back by yesterday's trouble waits for the user's answer
            self._news("loop", f"新的一天：{PLATFORM_NAMES.get(platform, platform)}昨天遇到验证码，还没有加入循环。问题解决了吗？请在弹窗里确认", level="warning",
                       platform=platform, data={"held": True})

    def _mode(self, platform: str) -> str:
        """auto: the loop collects, approves and delivers; manual: it only collects (and scores) and the postings wait for you."""
        if platform in MANUAL_ONLY:
            return "manual"
        return str((self.state.get("modes") or {}).get(platform) or "auto")

    def _capped(self, platform: str) -> bool:
        cap = int(self.state.get("delivery_cap") or 0)
        return cap > 0 and self._mode(platform) == "auto" and self._delivered(platform) >= cap

    def _all_capped(self) -> bool:
        """Every platform that delivers has reached the daily delivery limit: nothing more to do today."""
        cap = int(self.state.get("delivery_cap") or 0)
        platforms = [p for p in self.state.get("order", []) if self._mode(p) == "auto"]
        return cap > 0 and bool(platforms) and all(self._capped(p) for p in platforms)

    def _out(self, platform: str) -> bool:
        return platform in (self.state.get("out_today") or {})

    def _all_out(self) -> bool:
        """Every platform that delivers is capped or has run into a day-long limit: waiting longer would change nothing."""
        platforms = [p for p in self.state.get("order", []) if self._mode(p) == "auto"]
        return bool(platforms) and all(self._capped(p) or self._out(p) or self._held(p) for p in platforms) and not self._all_capped()

    # ------------------------------------------------------------------ platforms held back until the user says the trouble is over
    def _held(self, platform: str) -> bool:
        return platform in (self.state.get("held") or {})

    def _hold(self, platform: str, code: str, message: str) -> None:
        """A verification (captcha) only the user can pass: the platform sits out until the user confirms that it is solved."""
        if self._held(platform):
            return
        with self._lock:
            self.state.setdefault("held", {})[platform] = {"code": code, "message": message[:200], "since": self.now().isoformat(timespec="seconds"),
                                                           "day": self.now().date().isoformat(), "skipped_day": ""}
            self._save()
        self._news("loop", f"{PLATFORM_NAMES.get(platform, platform)}遇到验证码，已退出循环。请在浏览器里完成验证，再在弹窗里确认加入循环", level="warning",
                   platform=platform, data={"held": True})

    def release(self, platform: str) -> bool:
        """The user says the trouble is solved: the platform is part of the loop again (its open captcha / pause entries are closed)."""
        with self._lock:
            was_out = platform in (self.state.get("out_today") or {})         # "reached today's limit" is lifted too (the limit was raised)
            if platform not in (self.state.get("held") or {}) and not was_out:
                return False
            (self.state.get("held") or {}).pop(platform, None)
            (self.state.get("out_today") or {}).pop(platform, None)
            self._collect_closed.discard(platform)
            for item in self.state.get("incidents", []):
                if item.get("platform") == platform and item.get("code") in ("captcha", "platform_paused") and not item.get("resolved"):
                    item["resolved"] = True
            self._save()
        self._news("loop", f"{PLATFORM_NAMES.get(platform, platform)}已加入循环", level="info", platform=platform)
        return True

    def is_out_or_held(self, platform: str) -> bool:
        return self._held(platform) or self._out(platform)

    def skip_today(self, platform: str) -> bool:
        """The user does not want the platform today: it stays out and the question is not asked again until tomorrow."""
        with self._lock:
            item = (self.state.get("held") or {}).get(platform)
            if not item:
                return False
            item["skipped_day"] = self.now().date().isoformat()
            self._save()
        return True

    # ------------------------------------------------------------------ how the collecting time is shared
    def _dynamic(self) -> bool:
        try:
            return bool((self.settings() or {}).get("daily_loop_dynamic", True))
        except Exception:
            return True

    def _note_perf(self, platform: str, minutes: float, new: int) -> None:
        """What a stretch of collecting brought in; earlier stretches count less and less."""
        with self._lock:
            item = self.state.setdefault("perf", {}).setdefault(platform, {"minutes": 0.0, "new": 0.0})
            item["minutes"] = round(float(item.get("minutes", 0.0)) * PERF_DECAY + max(minutes, 0.0), 3)
            item["new"] = round(float(item.get("new", 0.0)) * PERF_DECAY + max(int(new), 0), 3)
            self._save()

    def _expected(self, platform: str) -> float | None:
        """Deliveries a minute of collecting is expected to lead to: new postings a minute times the share that gets delivered. None = no data."""
        item = (self.state.get("perf") or {}).get(platform) or {}
        minutes = float(item.get("minutes", 0.0))
        if minutes < 5:
            return None
        totals = (self.state.get("totals") or {}).get(platform) or {}
        collected, delivered = int(totals.get("collected", 0)), int(totals.get("delivered", 0))
        conversion = min(max(delivered / collected, 0.05), 1.0) if collected >= 20 else DEFAULT_CONVERSION
        return float(item.get("new", 0.0)) / minutes * conversion

    def _round_plan(self, order: list[str]) -> list[tuple[str, float | None]]:
        """The platforms of the next round in the order of what they are worth, with the collecting minutes each gets.

        A round has as much collecting time as the platforms in it would get at the fixed limit; it is shared by the expected
        deliveries a minute (with a floor, so that a dry platform is still looked at, and a ceiling, so that one platform does not
        take the round). Platforms without data count as the average of the others."""
        active = [p for p in order if self._mode(p) == "auto" and not self._capped(p) and not self._out(p) and not self._held(p)]
        if not self._dynamic() or len(active) < 2:
            return [(p, None) for p in order]
        known = {p: self._expected(p) for p in active}
        seen = [value for value in known.values() if value is not None]
        average = sum(seen) / len(seen) if seen else 1.0
        raw = {p: (known[p] if known[p] is not None else average) for p in active}
        share = {p: MIN_SHARE for p in active}                          # everyone gets the floor, the rest is shared by worth up to the ceiling
        open_ = set(active)
        while open_:
            left = 1.0 - sum(share.values())
            worth = {p: raw[p] for p in open_}
            if sum(worth.values()) <= 0:                                # nothing is expected anywhere: share equally
                worth = {p: 1.0 for p in open_}
            added = {p: left * worth[p] / sum(worth.values()) for p in open_}
            capped = {p for p in open_ if share[p] + added[p] > MAX_SHARE}
            if not capped:
                share.update({p: share[p] + added[p] for p in open_})
                break
            for p in capped:
                share[p] = MAX_SHARE
            open_ -= capped
        budget = self._limit_minutes("collect") * len(active)
        minutes = {p: max(round(budget * share[p]), MIN_PLAN_MINUTES) for p in active}
        with self._lock:
            self.state["plan"] = {p: {"minutes": minutes[p], "share": round(share[p], 2), "expected": None if known[p] is None else round(known[p], 3)} for p in active}
            self._save()
        best = iter(sorted(active, key=lambda p: -share[p]))
        return [((lambda p: (p, float(minutes[p])))(next(best)) if platform in active else (platform, None)) for platform in order]      # the others keep their places

    def _mark_out(self, platform: str, code: str) -> None:
        """The platform has nothing more to do today: say so once, with a link to the setting that decided it."""
        if self._out(platform):
            return
        label = limit_label(code, platform)
        with self._lock:
            self.state.setdefault("out_today", {})[platform] = {"code": code, "label": label}
            self._save()
        name = PLATFORM_NAMES.get(platform, platform)
        self._news("loop", f"{name}今天已达到「{label}」，每日循环不再处理这个平台；明天恢复，或到设置里调整", level="warning",
                   platform=platform, data={"href": limit_href(code, platform), "limit": label})

    def _wait(self, seconds: float) -> None:
        waited = 0.0
        while waited < seconds and not self._over():
            self.sleep(min(self.poll_seconds, seconds - waited))
            waited += self.poll_seconds

    def _wait_for_start(self) -> None:
        begin = self.state.get("start_at")
        if not begin:
            return
        start = datetime.fromisoformat(begin)
        while self.now() < start and not self._over():
            self._set(step="waiting", message=f"等待开始时间 {start:%H:%M}")
            self.sleep(self.poll_seconds)
        self._set(start_at="")

    def _wait_for_tomorrow(self) -> bool:
        """A loop without a time window whose platforms have all used up their day waits for the next day instead of ending."""
        if self.state.get("end_at") or not (self._all_capped() or self._all_out()):
            return False
        reason = "达到了投递上限" if self._all_capped() else "用完了今天的额度"
        self._set(step="idle", platform="", message=f"各平台今天都{reason}，等到明天自动继续（每日循环没有停）")
        self._wait(self.idle_seconds)
        return True

    def _run(self) -> None:
        try:
            self._wait_for_start()
            idle_rounds = 0
            failed_rounds = 0
            while not self._over():
                self._roll_day()
                if self._over():
                    break
                with self._lock:
                    self.state["round"] = int(self.state.get("round", 0)) + 1
                    self._save()
                new_in_round = 0
                order = list(self.state["order"])
                if self._resume_from in order:                    # the first round after a restart goes on where the loop was
                    order = order[order.index(self._resume_from):]
                self._resume_from = ""
                try:
                    plan = self._round_plan(order) if len(order) == len(self.state["order"]) else [(p, None) for p in order]
                    for platform, minutes in plan:
                        if self._over():
                            break
                        new_in_round += self._platform(platform, minutes)
                    if self._over():
                        break
                    self._check_replies()
                    failed_rounds = 0
                except Exception as exc:                          # one bad round must not end the loop: note it, wait, go on
                    failed_rounds += 1
                    self.incident("", "loop", "round_failed", f"第 {self.state.get('round')} 轮出错（连续第 {failed_rounds} 次）：{type(exc).__name__}: {exc}")
                    if failed_rounds >= MAX_FAILED_ROUNDS:
                        raise
                    self._wait(self.poll_seconds)
                    continue
                if self._wait_for_tomorrow():
                    idle_rounds = 0
                    continue
                idle_rounds = idle_rounds + 1 if new_in_round == 0 else 0
                if idle_rounds >= 2:                              # nothing new to collect: keep delivering and look again later
                    self._set(step="idle", message="连续两轮没有新岗位，等待后再试")
                    self._wait(self.idle_seconds)
        except Exception as exc:                                  # the loop must not die silently
            self.state["stopped_reason"] = "crash"
            self.incident("", "loop", "loop_crashed", f"每日循环异常退出：{type(exc).__name__}: {exc}")
        finally:
            self._finish()

    def _finish(self) -> None:
        end = datetime.fromisoformat(self.state["end_at"]) if self.state.get("end_at") else None
        by_clock = end is not None and self.now() >= end
        stopped = self.state.get("stopped_reason") or ("cap" if self._all_capped() else "limit" if self._all_out() else "end_time" if by_clock else "")
        self.state["stopped_reason"] = stopped
        if self._over() or stopped:
            try:
                status = self.api("GET", "/api/workbench", None)
                active = status.get("task") or {}
                if active.get("id") and active.get("status") in {"running", "queued"}:
                    self.api("POST", f"/api/workbench/task/{active['id']}/stop", {})
            except Exception:
                pass
        message = (
            "各平台都已达到投递上限，每日循环结束" if stopped == "cap"
            else self._limits_message() if stopped == "limit"
            else f"已到结束时间 {end:%H:%M}，每日循环结束" if stopped == "end_time" and end is not None
            else "你已手动停止每日循环" if stopped == "user"
            else "每日循环结束"
        )
        self._set(running=False, step="finished", message=message, platform="", summary=self.summary())
        record = self._record_run(stopped or "unknown", message)
        level = "warning" if record["needs_attention"] else "info"
        suffix = f"。{record['suggestion']}" if record["suggestion"] else ""
        self._news("loop", f"{message}：{json.dumps(self.summary(), ensure_ascii=False)}{suffix}", level=level,
                   data={"reason": record["reason"], "needs_attention": record["needs_attention"]})

    def _record_run(self, reason: str, message: str) -> dict[str, Any]:
        """Keep what a run did and why it ended, so the app can show it and ask the user what to do next."""
        label, attention, suggestion = STOP_REASONS.get(reason, STOP_REASONS["unknown"])
        summary = self.summary()
        record = {
            "started_at": self.state.get("started_at", ""), "ended_at": self.now().isoformat(timespec="seconds"),
            "started_by": self.state.get("started_by") or "manual", "reason": reason if reason in STOP_REASONS else "unknown",
            "reason_label": label, "message": message, "needs_attention": attention, "suggestion": suggestion,
            "rounds": summary["rounds"], "totals": summary["totals"], "open_incidents": summary["open_incidents"],
        }
        with self._lock:
            self.state["ended"] = record
            self._save()
        try:
            history = json.loads(self.history_path.read_text(encoding="utf-8"))
            history = history if isinstance(history, list) else []
        except (OSError, ValueError):
            history = []
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text(json.dumps((history + [record])[-MAX_HISTORY:], ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
        return record

    def history(self, limit: int = 10) -> list[dict[str, Any]]:
        """The last runs, newest first."""
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return list(reversed(data if isinstance(data, list) else []))[:max(1, limit)]

    def _limits_message(self) -> str:
        parts = [f"{PLATFORM_NAMES.get(p, p)}「{info['label']}」" for p, info in (self.state.get("out_today") or {}).items()]
        return "各平台今天都用完了额度（" + "、".join(parts) + "），每日循环结束；明天恢复，或调整上限后重新开始"

    def summary(self) -> dict[str, Any]:
        with self._lock:
            open_incidents = [i for i in self.state.get("incidents", []) if not i.get("resolved")]
            return {"rounds": self.state.get("round", 0), "totals": self.state.get("totals", {}), "open_incidents": len(open_incidents)}

    # ------------------------------------------------------------------ one platform
    def _check_replies(self) -> None:
        """Every ``monitor_minutes`` minutes: one read-only pass over BOSS for HR replies (BOSS is the only platform with a monitor).

        The pass only reads. The new replies show up in the message center (news watcher) and for Claude / Codex in the status.
        """
        minutes = int(self.state.get("monitor_minutes") or 0)
        if minutes <= 0 or "boss" not in self.state.get("order", []) or self._mode("boss") == "off" or self._out("boss"):
            return
        last = self.state.get("last_monitor")
        if last and self.now() < datetime.fromisoformat(last) + timedelta(minutes=minutes):
            return
        if "boss" in (self._get("/api/workbench").get("platform_limit_details") or {}):
            return                                              # paused (verification, limit): no page of BOSS is opened for this
        self._set(platform="boss", step="monitor", message="BOSS 直聘：检查 HR 回复")
        try:
            self.api("POST", "/api/workbench/task", {"mode": "monitor", "options": {"once": True}})
        except ApiError:
            return                                              # busy or refused: it is tried again next round
        with self._lock:
            self.state["last_monitor"] = self.now().isoformat(timespec="seconds")
            self._save()
        self._wait_idle("BOSS 直聘：检查 HR 回复", limit_minutes=20)

    def _platform(self, platform: str, minutes: float | None = None) -> int:
        self._set(platform=platform, step="check", message=f"{PLATFORM_NAMES.get(platform, platform)}：检查状态")
        if self._held(platform):
            self._set(message=f"{PLATFORM_NAMES.get(platform, platform)}：遇到验证码，等你确认后再加入循环，跳过")
            return 0
        if self._capped(platform):
            self._set(message=f"{PLATFORM_NAMES.get(platform, platform)}：已达到投递上限 {self.state.get('delivery_cap')}，跳过")
            return 0
        if self._out(platform):
            self._set(message=f"{PLATFORM_NAMES.get(platform, platform)}：今天已达到「{self.state['out_today'][platform]['label']}」，跳过")
            return 0
        status = self._get("/api/workbench")
        if platform in (status.get("platform_limit_details") or {}):
            detail = status["platform_limit_details"][platform]
            self.incident(platform, "check", "platform_paused", f"{PLATFORM_NAMES[platform]}当前被暂停：{detail.get('reason', '')}")
            if "captcha" in str(detail.get("reason", "")).lower() or "验证码" in str(detail.get("reason", "")):
                self._hold(platform, "captcha", str(detail.get("reason", "")))
            return 0
        # Collecting is done in slices and what a slice found is delivered before the next one starts: a posting does not wait
        # for the rest of the collection (one account does one thing at a time, as before). Without a slice setting (0) or when the
        # whole limit fits in one slice this is a single collection followed by the delivery.
        total, piece, spent, added = (minutes or self._limit_minutes("collect")), self._slice_minutes(), 0.0, 0
        delivers = platform not in MANUAL_ONLY and self._mode(platform) == "auto" and self.state.get("options", {}).get("auto_approve", True)
        while True:
            limit = min(piece, total - spent) if 0 < piece < total else total
            self._cut = False
            began = self.now()
            found = self._collect(platform, limit)
            added += found
            self._note_perf(platform, (self.now() - began).total_seconds() / 60, found)
            if self._held(platform) or self._over():
                return added
            if delivers:
                self._deliver(platform)
            spent += limit
            if not self._cut or spent >= total or platform in self._collect_closed or self._out(platform) or self._capped(platform):
                break
        if platform in MANUAL_ONLY:
            self._greet_for_manual(platform)
        return added

    def _get(self, path: str) -> dict:
        try:
            return self.api("GET", path, None)
        except ApiError as exc:
            self.incident("", "api", "api_error", f"{path}：{exc}")
            return {}

    def _limit_minutes(self, which: str) -> int:
        """How long one collection / delivery may run. The settings page is read every time, so a change there counts at once
        (unless the loop was started with an explicit value)."""
        default = MAX_COLLECT_MINUTES if which == "collect" else MAX_DELIVER_MINUTES
        stored = self._minutes(self.state.get(f"{which}_minutes"), default)
        if self.state.get("minutes_pinned"):
            return stored
        try:
            live = (self.settings() or {}).get(f"daily_loop_{which}_minutes")
        except Exception:
            live = None
        minutes = self._minutes(live, stored)
        if minutes != stored:
            self._set(**{f"{which}_minutes": minutes})
        return minutes

    def _slice_minutes(self) -> float:
        """How long one slice of collecting lasts before the loop delivers what it found (0 = no slicing)."""
        try:
            live = (self.settings() or {}).get("daily_loop_slice_minutes")
            return max(float(live), 0.0) if live is not None else float(SLICE_MINUTES)
        except (TypeError, ValueError):
            return float(SLICE_MINUTES)

    def _wait_idle(self, label: str, limit_minutes: float | None = None, *, routine: bool = False) -> dict:
        """Wait until no task is running or queued; stop a task that runs longer than the limit. Returns the last task."""
        if limit_minutes is None:
            limit_minutes = self._limit_minutes("collect")
        waited = 0.0
        while not self._over():
            status = self._get("/api/workbench")
            active, queued = status.get("task") or {}, status.get("queued_tasks") or []
            if not (active.get("status") in {"running", "queued", "stopping"} or queued):
                return status.get("last_task") or {}
            self._set(message=f"{label}（{(active.get('logs') or [''])[-1][:60]}）")
            if waited >= limit_minutes * 60 and active.get("id"):
                if routine:                                       # the time limit of a collection is how the loop alternates, not a fault
                    self._cut = True
                    if limit_minutes >= self._limit_minutes("collect"):       # a slice that ends is not worth a message
                        self._news("loop", f"{label}已到单次上限 {limit_minutes:g} 分钟，先去投递，之后继续采集", level="info", platform=self.state.get("platform", ""))
                else:
                    self.incident(self.state.get("platform", ""), self.state.get("step", ""), "task_too_long", f"{label}超过 {limit_minutes:g} 分钟，已请求停止")
                reason = "每日循环分段：这一段采集时间到，先去投递，之后继续采集" if routine else f"每日循环：{label}超过 {limit_minutes:g} 分钟，已请求停止"
                self.api("POST", f"/api/workbench/task/{active['id']}/stop", {"reason": reason})
                limit_minutes = 1e9
            self.sleep(self.poll_seconds)
            waited += self.poll_seconds
        return {}

    def _collect(self, platform: str, limit_minutes: float | None = None) -> int:
        name = PLATFORM_NAMES.get(platform, platform)
        self._set(step="collect", message=f"{name}：采集")
        body = {"mode": "collect", "options": {"platform_order": [platform], "auto_score": True, "target_new": self.state["targets"][platform],
                                               "quotas": {"fulltime": max(self.state["targets"][platform] // 2, 1), "internship": 0}}}
        while not self._over():
            try:
                self.api("POST", "/api/workbench/task", body)
                break
            except ApiError as exc:
                code = str(exc.payload.get("code") or "")
                if code == "platform_limit_today":
                    self.incident(platform, "collect", "platform_paused", f"{name}今天已达上限或被暂停：{exc}")
                    return 0
                if exc.status == 409:                              # another task is running: wait for it
                    self._wait_idle("等待当前任务结束")
                    continue
                messages = exc.payload.get("messages")
                self.incident(platform, "collect", "start_refused", f"{name}采集没有启动：{exc}", "；".join(map(str, messages or []))[:1000])
                self._wait(self.idle_seconds)                      # window closed, preflight failing ...: try again later
                return 0
        last = self._wait_idle(f"{name}：采集中", limit_minutes, routine=True)
        metrics = last.get("metrics") or {}
        new = int(metrics.get("collect_new") or 0)
        self._bump(platform, "collected", new)
        self._report_collect_problems(platform, last)
        limit = day_long_limit(((last.get("progress") or {}).get("platforms") or {}).get(platform, {}).get("reason_code"))
        if limit in SHARED_LIMITS:
            self._mark_out(platform, limit)
        elif limit:                                                # collecting is over for today, delivering what is left is not
            self._collect_closed.add(platform)
            self._news("loop", f"{PLATFORM_NAMES.get(platform, platform)}今天已达到「{limit_label(limit, platform)}」，不再采集，只投递已有的岗位", level="warning",
                       platform=platform, data={"href": limit_href(limit, platform), "limit": limit_label(limit, platform)})
        return new

    def _report_collect_problems(self, platform: str, last: dict) -> None:
        name = PLATFORM_NAMES.get(platform, platform)
        progress = (last.get("progress") or {}).get("platforms") or {}
        item = progress.get(platform) or {}
        state = str(item.get("status") or "")
        if last.get("status") == "failed" or last.get("error"):
            self.incident(platform, "collect", "task_failed", f"{name}采集任务失败：{last.get('error') or ''}", "\n".join((last.get("logs") or [])[-8:]))
        elif state in {"blocked", "failed", "completed_with_errors"}:
            self.incident(platform, "collect", str(item.get("reason_code") or state), f"{name}采集异常（{state}）：{item.get('message') or ''}",
                          "\n".join((last.get("logs") or [])[-8:]))
            if str(item.get("reason_code") or "") == "captcha":
                self._hold(platform, "captcha", str(item.get("message") or ""))

    def _ids(self, platform: str, view: str, *, missing_greeting: bool = False, statuses: set[str] | None = None) -> list[str]:
        ids: list[str] = []
        offset = 0
        while True:
            page = self._get(f"/api/jobs/search?queue_view={view}&limit=100&offset={offset}")
            items = page.get("items") or []
            ids += [str(i["id"]) for i in items if str(i.get("source_platform") or "boss") == platform
                    and (not missing_greeting or not str(i.get("greeting") or "").strip())
                    and (statuses is None or str(i.get("status") or "") in statuses)]
            if len(items) < 100:
                break
            offset += 100
        return ids

    def _greet_for_manual(self, platform: str) -> None:
        name = PLATFORM_NAMES.get(platform, platform)
        self._set(step="greet", message=f"{name}：生成招呼语（留给你手动投递）")
        ids = self._ids(platform, "manual", missing_greeting=True, statuses=GREETING_STATUSES)
        if not ids:
            return
        try:
            self.api("POST", "/api/workbench/greetings", {"job_ids": ids})
            self._wait_idle(f"{name}：生成招呼语")
        except ApiError as exc:
            self.incident(platform, "greet", "greeting_failed", f"生成招呼语失败：{exc}")

    def _deliver(self, platform: str) -> None:
        """Approve and deliver in one step: the delivery task takes the postings waiting for confirmation (and the ones
        already approved), writes their greetings and sends them."""
        name = PLATFORM_NAMES.get(platform, platform)
        self._set(step="deliver", message=f"{name}：批准并投递")
        ids = list(dict.fromkeys(self._ids(platform, "confirm") + self._ids(platform, "waiting")))
        cap = int(self.state.get("delivery_cap") or 0)
        if cap > 0:
            ids = ids[:max(cap - self._delivered(platform), 0)]            # never deliver past the day's limit
        if not ids or self._over():
            if not ids and platform in self._collect_closed:       # nothing left to deliver and nothing more to collect
                self._mark_out(platform, "daily_search_page_limit")
            return
        try:
            result = self.api("POST", "/api/workbench/deliver", {"job_ids": ids, "skip_invalid": True})
        except ApiError as exc:
            if exc.status != 409 or "没有可投递" not in str(exc):      # nothing deliverable is not a problem
                self.incident(platform, "deliver", str(exc.payload.get("code") or "deliver_refused"), f"{name}投递没有启动：{exc}")
            return
        self._bump(platform, "approved", len(ids))
        last = self._wait_idle(f"{name}：投递中", limit_minutes=self._limit_minutes("deliver"))
        self._report_delivery(platform, last, result)
        limit = day_long_limit(last.get("stop_reason"))
        if limit in SHARED_LIMITS:
            self._mark_out(platform, limit)

    def _report_delivery(self, platform: str, last: dict, started: dict) -> None:
        name = PLATFORM_NAMES.get(platform, platform)
        metrics = last.get("metrics") or {}
        sent = int(metrics.get("send_success") or 0)
        failed = int(metrics.get("send_failed") or 0)
        self._bump(platform, "delivered", sent)
        self._bump(platform, "failed", failed)
        if last.get("status") == "failed" or last.get("error"):
            self.incident(platform, "deliver", "task_failed", f"{name}投递任务失败：{last.get('error') or ''}", "\n".join((last.get("logs") or [])[-8:]))
        elif failed:
            self.incident(platform, "deliver", "send_failures", f"{name}有 {failed} 个岗位投递失败，见工作台「发送失败待处理」", "\n".join((last.get("logs") or [])[-6:]))
