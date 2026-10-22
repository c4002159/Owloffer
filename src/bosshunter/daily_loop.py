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
from datetime import datetime, time as day_time
from pathlib import Path
from typing import Any, Callable

DEFAULT_ORDER = ("zhilian_campus", "zhilian", "boss", "51job", "liepin")
DEFAULT_TARGETS = {"zhilian_campus": 50, "zhilian": 80, "boss": 80, "51job": 80, "liepin": 80}
MANUAL_ONLY = {"zhilian_campus"}                 # collected, scored and greeted, but delivered by the user
POLL_SECONDS = 60
IDLE_SECONDS = 600                              # nothing to collect (window closed, all paused): look again later
MAX_COLLECT_MINUTES = 90
DEFAULT_END = "20:00"
DEFAULT_CAP = 150                               # deliveries per platform and day; 0 = no limit
MAX_INCIDENTS = 300
GREETING_STATUSES = {"ready", "approved", "error"}      # the statuses the greeting API accepts (not the unscored ones)
PLATFORM_NAMES = {"zhilian_campus": "智联校招", "zhilian": "智联招聘", "boss": "BOSS 直聘", "51job": "前程无忧", "liepin": "猎聘"}


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
    ):
        self.path = Path(state_path)
        self.api, self.now, self.sleep = api, now, sleep
        self.poll_seconds, self.idle_seconds = poll_seconds, idle_seconds
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.state = self._load()

    # ------------------------------------------------------------------ state
    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
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
            order = [p for p in (options.get("platform_order") or DEFAULT_ORDER) if p in DEFAULT_TARGETS]
            targets = {**DEFAULT_TARGETS, **{k: int(v) for k, v in (options.get("targets") or {}).items() if k in DEFAULT_TARGETS}}
            end = parse_end_time(options.get("end_time") or DEFAULT_END, now)
            if end <= now:
                raise ValueError(f"结束时间 {end:%H:%M} 已经过了，请把结束时间调到现在之后")
            begin = parse_end_time(options["start_time"], now) if options.get("start_time") else None
            if begin is not None and begin >= end:
                raise ValueError("开始时间必须早于结束时间")
            cap = max(int(options.get("delivery_cap", DEFAULT_CAP) or 0), 0)
            self.state = {
                "running": True, "started_at": now.isoformat(timespec="seconds"), "end_at": end.isoformat(timespec="seconds"),
                "start_at": begin.isoformat(timespec="seconds") if begin and begin > now else "", "delivery_cap": cap,
                "order": order, "targets": targets, "round": 0, "platform": "", "step": "starting", "message": "每日循环启动",
                "totals": {}, "incidents": [], "options": {k: options[k] for k in ("start_time", "end_time", "auto_approve", "delivery_cap") if k in options},
                "stopped_reason": "",
            }
            self._save()
        self._launch()
        return self.snapshot()

    def resume(self) -> bool:
        """Continue after a server restart when the loop was running and its end time has not come."""
        state = self.state
        if not state.get("running") or self.running():
            return False
        end = datetime.fromisoformat(state["end_at"]) if state.get("end_at") else None
        if end is None or self.now() >= end:
            self._set(running=False, step="finished", message="已到结束时间", stopped_reason="end_time")
            return False
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
        end = datetime.fromisoformat(self.state["end_at"])
        return self._stop.is_set() or self.now() >= end or self._all_capped()

    def _delivered(self, platform: str) -> int:
        return int((self.state.get("totals") or {}).get(platform, {}).get("delivered", 0))

    def _capped(self, platform: str) -> bool:
        cap = int(self.state.get("delivery_cap") or 0)
        return cap > 0 and platform not in MANUAL_ONLY and self._delivered(platform) >= cap

    def _all_capped(self) -> bool:
        """Every platform that delivers has reached the daily delivery limit: nothing more to do today."""
        cap = int(self.state.get("delivery_cap") or 0)
        platforms = [p for p in self.state.get("order", []) if p not in MANUAL_ONLY]
        return cap > 0 and bool(platforms) and all(self._capped(p) for p in platforms)

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

    def _run(self) -> None:
        try:
            self._wait_for_start()
            idle_rounds = 0
            while not self._over():
                with self._lock:
                    self.state["round"] = int(self.state.get("round", 0)) + 1
                    self._save()
                new_in_round = 0
                for platform in self.state["order"]:
                    if self._over():
                        break
                    new_in_round += self._platform(platform)
                if self._over():
                    break
                idle_rounds = idle_rounds + 1 if new_in_round == 0 else 0
                if idle_rounds >= 2:                              # nothing new to collect: keep delivering and look again later
                    self._set(step="idle", message="连续两轮没有新岗位，等待后再试")
                    self._wait(self.idle_seconds)
        except Exception as exc:                                  # the loop must not die silently
            self.incident("", "loop", "loop_crashed", f"每日循环异常退出：{type(exc).__name__}: {exc}")
        finally:
            self._finish()

    def _finish(self) -> None:
        stopped = self.state.get("stopped_reason") or ("cap" if self._all_capped() else "end_time")
        if self._over() or stopped:
            try:
                status = self.api("GET", "/api/workbench", None)
                active = status.get("task") or {}
                if active.get("id") and active.get("status") in {"running", "queued"}:
                    self.api("POST", f"/api/workbench/task/{active['id']}/stop", {})
            except Exception:
                pass
        message = "各平台都已达到投递上限，每日循环结束" if self._all_capped() else "每日循环结束"
        self._set(running=False, step="finished", message=message, platform="", summary=self.summary())
        self._news("loop", f"{message}：{json.dumps(self.summary(), ensure_ascii=False)}", level="info")

    def summary(self) -> dict[str, Any]:
        with self._lock:
            open_incidents = [i for i in self.state.get("incidents", []) if not i.get("resolved")]
            return {"rounds": self.state.get("round", 0), "totals": self.state.get("totals", {}), "open_incidents": len(open_incidents)}

    # ------------------------------------------------------------------ one platform
    def _platform(self, platform: str) -> int:
        self._set(platform=platform, step="check", message=f"{PLATFORM_NAMES.get(platform, platform)}：检查状态")
        if self._capped(platform):
            self._set(message=f"{PLATFORM_NAMES.get(platform, platform)}：已达到投递上限 {self.state.get('delivery_cap')}，跳过")
            return 0
        status = self._get("/api/workbench")
        if platform in (status.get("platform_limit_details") or {}):
            detail = status["platform_limit_details"][platform]
            self.incident(platform, "check", "platform_paused", f"{PLATFORM_NAMES[platform]}当前被暂停：{detail.get('reason', '')}")
            return 0
        added = self._collect(platform)
        if self._over():
            return added
        if platform in MANUAL_ONLY:
            self._greet_for_manual(platform)
        elif self.state.get("options", {}).get("auto_approve", True):
            self._deliver(platform)
        return added

    def _get(self, path: str) -> dict:
        try:
            return self.api("GET", path, None)
        except ApiError as exc:
            self.incident("", "api", "api_error", f"{path}：{exc}")
            return {}

    def _wait_idle(self, label: str, limit_minutes: float = MAX_COLLECT_MINUTES) -> dict:
        """Wait until no task is running or queued; stop a task that runs longer than the limit. Returns the last task."""
        waited = 0.0
        while not self._over():
            status = self._get("/api/workbench")
            active, queued = status.get("task") or {}, status.get("queued_tasks") or []
            if not (active.get("status") in {"running", "queued", "stopping"} or queued):
                return status.get("last_task") or {}
            self._set(message=f"{label}（{(active.get('logs') or [''])[-1][:60]}）")
            if waited >= limit_minutes * 60 and active.get("id"):
                self.incident(self.state.get("platform", ""), self.state.get("step", ""), "task_too_long", f"{label}超过 {limit_minutes:g} 分钟，已请求停止")
                self.api("POST", f"/api/workbench/task/{active['id']}/stop", {})
                limit_minutes = 1e9
            self.sleep(self.poll_seconds)
            waited += self.poll_seconds
        return {}

    def _collect(self, platform: str) -> int:
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
        last = self._wait_idle(f"{name}：采集中")
        metrics = last.get("metrics") or {}
        new = int(metrics.get("collect_new") or 0)
        self._bump(platform, "collected", new)
        self._report_collect_problems(platform, last)
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
            return
        try:
            result = self.api("POST", "/api/workbench/deliver", {"job_ids": ids, "skip_invalid": True})
        except ApiError as exc:
            if exc.status != 409 or "没有可投递" not in str(exc):      # nothing deliverable is not a problem
                self.incident(platform, "deliver", str(exc.payload.get("code") or "deliver_refused"), f"{name}投递没有启动：{exc}")
            return
        self._bump(platform, "approved", len(ids))
        last = self._wait_idle(f"{name}：投递中", limit_minutes=240)
        self._report_delivery(platform, last, result)

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
