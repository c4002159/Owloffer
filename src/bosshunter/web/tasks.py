"""Workbench background task runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Event, Lock, Thread, Timer
from typing import Any, Callable
from uuid import uuid4

from bosshunter.throttle import SendWindowChecker


MODE_LABELS = {
    "full": "运行全流程",
    "collect": "单独采集",
    "score": "单独 AI 评分",
    "rescore": "重新评分",
    "greet": "生成招呼语",
    "monitor": "单独监测",
    "deliver": "确认投递",
    "fill": "补满审查列表",
}

TERMINAL_STATUSES = {"completed", "failed", "stopped"}
ACTIVE_STATUSES = {"running", "stopping"}
QUEUED = "queued"          # waiting for the running task to end; starts by itself afterwards
MAX_QUEUED = 5
DEADLINE_MODES = {"full", "monitor", "deliver"}
# Scoring only talks to the AI and writes its own scores, collection only talks to the platforms and adds postings: the two may
# run side by side. Everything else (delivery, greetings, monitoring, the full flow) still has the program to itself.
SCORING_MODES = {"score", "rescore"}
COLLECTION_MODES = {"collect", "fill"}


def modes_conflict(first: str, second: str) -> bool:
    """Whether two kinds of task must not run at the same time."""
    if (first in SCORING_MODES and second in COLLECTION_MODES) or (first in COLLECTION_MODES and second in SCORING_MODES):
        return False
    return True


class TaskAlreadyRunningError(RuntimeError):
    """Raised when a mutually exclusive workbench task is already active."""


@dataclass
class WorkbenchTask:
    id: str
    mode: str
    label: str
    status: str = "running"
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    deadline_at: str | None = None
    stop_reason: str | None = None
    stop_requested: Event = field(default_factory=Event, repr=False)
    metrics: dict[str, int] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict, repr=False)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "mode": self.mode,
            "label": self.label,
            "status": self.status,
            "logs": list(self.logs),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline_at": self.deadline_at,
            "stop_reason": self.stop_reason,
            "stop_requested": self.stop_requested.is_set(),
            "metrics": dict(self.metrics),
            "progress": dict(self.progress),
        }


Executor = Callable[[WorkbenchTask, dict], None]


def wait_for_initial_monitor_cooldown(
    task: WorkbenchTask,
    config: dict,
    log: Callable[[WorkbenchTask, str], None],
) -> bool:
    """Wait for the full-flow monitor cooldown; return true when cancellation wins."""
    raw_cooldown = config.get("monitor", {}).get("initial_cooldown_minutes", 10)
    try:
        cooldown_sec = max(float(raw_cooldown), 0) * 60
    except (TypeError, ValueError):
        cooldown_sec = 10 * 60
    if cooldown_sec <= 0:
        return False
    log(task, f"发送结束，首次监测将在 {cooldown_sec / 60:g} 分钟冷却后开始")
    if task.stop_requested.wait(cooldown_sec):
        log(task, "首次监测冷却已取消")
        return True
    return False


class WorkbenchTaskRunner:
    def __init__(self, executors: dict[str, Executor] | None = None):
        self._executors = executors or {}
        self._tasks: dict[str, WorkbenchTask] = {}
        self._threads: dict[str, Thread] = {}
        self._deadline_timers: dict[str, Timer] = {}
        self._lock = Lock()
        self._queue: list[tuple[WorkbenchTask, dict, Callable[[], None] | None]] = []

    def start(
        self,
        mode: str,
        config: dict,
        *,
        before_start: Callable[[], None] | None = None,
        queue_if_busy: bool = False,
    ) -> dict:
        if mode not in MODE_LABELS:
            raise ValueError(f"Unsupported workbench mode: {mode}")

        with self._lock:
            blocking = self._blocking_locked(mode)
            waiting = [item for item in self._queue if modes_conflict(item[0].mode, mode)]
            if blocking or waiting:
                if not queue_if_busy:
                    raise TaskAlreadyRunningError(
                        f"当前已有后台任务「{blocking.label if blocking else waiting[0][0].label}」正在运行或停止中，请等待其完全结束"
                    )
                if len(self._queue) >= MAX_QUEUED:
                    raise TaskAlreadyRunningError(f"排队的任务已有 {MAX_QUEUED} 个，请等前面的任务完成后再添加")
                ahead = blocking.label if blocking else waiting[-1][0].label
                task = WorkbenchTask(id=str(uuid4()), mode=mode, label=MODE_LABELS[mode], status=QUEUED)
                task.logs.append(f"等待「{ahead}」完成，之后自动开始")
                for key in ("_workbench_job_ids",):
                    if config.get(key):
                        task.context["queued_job_ids"] = [str(job_id) for job_id in config[key]]
                self._tasks[task.id] = task
                self._queue.append((task, config, before_start))
                return task.snapshot()

            task = WorkbenchTask(id=str(uuid4()), mode=mode, label=MODE_LABELS[mode])
            deadline = _deadline_from_config(mode, config)
            if deadline:
                task.deadline_at = deadline.isoformat(timespec="seconds")
            if deadline and deadline <= datetime.now():
                task.stop_requested.set()
                task.status = "stopped"
                task.stop_reason = "今日发送时间窗口已截止，后台未启动"
                task.logs.append(task.stop_reason)
                task.updated_at = datetime.now().isoformat(timespec="seconds")
                self._tasks[task.id] = task
                return task.snapshot()

            # Persist start settings under the same lock as admission. A failed
            # callback must leave no registered task or worker behind.
            if before_start is not None:
                before_start()
            self._tasks[task.id] = task
            thread = Thread(target=self._run, args=(task, config), daemon=True)
            self._threads[task.id] = thread
            if deadline:
                delay_seconds = max((deadline - datetime.now()).total_seconds(), 0)
                timer = Timer(delay_seconds, self._stop_at_deadline, args=(task.id,))
                timer.daemon = True
                self._deadline_timers[task.id] = timer
                timer.start()
            thread.start()
            return task.snapshot()

    def status(self) -> dict:
        with self._lock:
            active = self._active_task_locked()
            tasks = [task.snapshot() for task in self._tasks.values()]
            return {
                "active": active.snapshot() if active else None,
                "active_tasks": [task.snapshot() for task in self._active_tasks_locked()],
                "last_task": tasks[-1] if tasks else None,
                "tasks": tasks,
                "queued": [task.snapshot() for task, _config, _before in self._queue],
            }

    def stop(self, task_id: str, reason: str = "用户已请求停止") -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task.status in TERMINAL_STATUSES:
                return task.snapshot()
            if task.status == QUEUED:                      # cancelled before it ever started
                self._queue = [item for item in self._queue if item[0].id != task_id]
                task.stop_requested.set()
                task.status = "stopped"
                task.stop_reason = "已取消排队"
                task.logs.append(task.stop_reason)
                task.updated_at = datetime.now().isoformat(timespec="seconds")
                return task.snapshot()
            task.stop_requested.set()
            task.status = "stopping"
            task.stop_reason = reason
            if reason and (not task.logs or task.logs[-1] != reason):
                task.logs.append(reason)
            task.updated_at = datetime.now().isoformat(timespec="seconds")
            confirmation_event = task.context.get("confirmation_event")
            if isinstance(confirmation_event, Event):
                confirmation_event.set()
            monitor_wakeup_event = task.context.get("monitor_wakeup_event")
            if isinstance(monitor_wakeup_event, Event):
                monitor_wakeup_event.set()
            return task.snapshot()

    def wait(self, timeout: float | None = None) -> None:
        threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=timeout)

    def _run(self, task: WorkbenchTask, config: dict) -> None:
        try:
            executor = self._executors.get(task.mode)
            if executor:
                executor(task, config)
            with self._lock:
                if task.stop_requested.is_set():
                    task.status = "stopped"
                else:
                    task.status = "completed"
                task.updated_at = datetime.now().isoformat(timespec="seconds")
        except Exception as exc:
            with self._lock:
                if task.stop_requested.is_set():
                    task.status = "stopped"
                    task.error = None
                else:
                    task.status = "failed"
                    task.error = str(exc)
                task.updated_at = datetime.now().isoformat(timespec="seconds")
        finally:
            with self._lock:
                timer = self._deadline_timers.pop(task.id, None)
            if timer:
                timer.cancel()
            self._start_next()

    def _start_next(self) -> None:
        """Start the queued tasks that no longer conflict with a running one, in the order they were queued."""
        with self._lock:
            while True:
                index = next((i for i, item in enumerate(self._queue) if not self._blocking_locked(item[0].mode)), None)
                if index is None:
                    return
                task, config, before_start = self._queue.pop(index)
                if task.stop_requested.is_set():
                    continue
                deadline = _deadline_from_config(task.mode, config)
                if deadline and deadline <= datetime.now():
                    task.stop_requested.set()
                    task.status = "stopped"
                    task.stop_reason = "今日发送时间窗口已截止，排队的任务未启动"
                    task.logs.append(task.stop_reason)
                    task.updated_at = datetime.now().isoformat(timespec="seconds")
                    continue
                try:
                    if before_start is not None:
                        before_start()
                except Exception as exc:
                    task.status = "failed"
                    task.error = str(exc)
                    task.updated_at = datetime.now().isoformat(timespec="seconds")
                    continue
                task.status = "running"
                task.logs.append("前面的任务已结束，开始执行")
                task.updated_at = datetime.now().isoformat(timespec="seconds")
                if deadline:
                    task.deadline_at = deadline.isoformat(timespec="seconds")
                    timer = Timer(max((deadline - datetime.now()).total_seconds(), 0), self._stop_at_deadline, args=(task.id,))
                    timer.daemon = True
                    self._deadline_timers[task.id] = timer
                    timer.start()
                thread = Thread(target=self._run, args=(task, config), daemon=True)
                self._threads[task.id] = thread
                thread.start()

    def _stop_at_deadline(self, task_id: str) -> None:
        try:
            self.stop(task_id, "已到发送时间窗口截止时间，后台自动停止")
        except KeyError:
            return

    def _active_tasks_locked(self) -> list[WorkbenchTask]:
        return [task for task in self._tasks.values() if task.status in ACTIVE_STATUSES]

    def _active_task_locked(self) -> WorkbenchTask | None:
        """The task the workbench shows as the running one: a collecting/delivering one first, a scoring one otherwise."""
        active = self._active_tasks_locked()
        main = [task for task in active if task.mode not in SCORING_MODES]
        return (main or active or [None])[0]

    def _blocking_locked(self, mode: str) -> WorkbenchTask | None:
        for task in self._active_tasks_locked():
            if modes_conflict(task.mode, mode):
                return task
        return None


def _deadline_from_config(mode: str, config: dict) -> datetime | None:
    """Resolve the automatic stop deadline for long-running/send tasks."""
    if mode not in DEADLINE_MODES:
        return None
    throttle = config.get("throttle", {}) if isinstance(config, dict) else {}
    windows = throttle.get("send_windows", ["09:00-16:00"])
    return SendWindowChecker(windows).latest_end_datetime()
