"""Turns what happens in the program into messages for the message center (and for Claude / Codex, which read the same feed).

It polls three things the rest of the program does not announce by itself: tasks that finished, platforms that were paused,
and new HR replies waiting for the user. The first poll only remembers the current state, so starting the program does not
replay the past.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

TERMINAL = {"completed": ("完成", "info"), "failed": ("失败", "warning"), "stopped": ("已停止", "info")}
PLATFORM_NAMES = {"zhilian_campus": "智联校招", "zhilian": "智联招聘", "boss": "BOSS 直聘", "51job": "前程无忧", "liepin": "猎聘"}
POLL_SECONDS = 15
KEYWORD_TIERS = {"core": "核心", "expand": "扩展", "explore": "探索", "paused": "暂停"}


class NewsWatcher:
    def __init__(
        self,
        emit: Callable[..., Any],
        tasks: Callable[[], list[dict]],
        limits: Callable[[], dict[str, dict]],
        replies: Callable[[], int],
        paused_scoring: Callable[[], list[dict]] | None = None,
        keyword_moves: Callable[[], list[dict]] | None = None,
        keyword_alerts: Callable[[], list[str]] | None = None,
    ):
        self.emit, self.tasks, self.limits, self.replies = emit, tasks, limits, replies
        self.paused_scoring = paused_scoring or (lambda: [])
        self.keyword_moves = keyword_moves or (lambda: [])
        self.keyword_alerts = keyword_alerts or (lambda: [])
        self._last_move = None                                 # id of the newest tier move already known (None: not looked yet)
        self._told_alerts: set[str] = set()
        self._told_paused: set[str] = set()
        self._seen_tasks: dict[str, str] = {}
        self._seen_limits: set[str] = set()
        self._replies = 0
        self._primed = False
        self._stop = threading.Event()

    def poll(self) -> None:
        tasks, limits, replies = self.tasks(), self.limits(), self.replies()
        announce = self._primed
        for task in tasks:
            status = str(task.get("status") or "")
            if status in TERMINAL and self._seen_tasks.get(str(task["id"])) != status:
                self._seen_tasks[str(task["id"])] = status
                if announce:
                    word, level = TERMINAL[status]
                    detail = str(task.get("error") or (task.get("logs") or [""])[-1] or "")[:120]
                    self.emit("task", f"{task.get('label') or task.get('mode')}{word}" + (f"：{detail}" if detail else ""), level=level)
        for platform, detail in limits.items():
            if platform not in self._seen_limits:
                self._seen_limits.add(platform)
                if announce:
                    self.emit("platform", f"{PLATFORM_NAMES.get(platform, platform)}被暂停：{str(detail.get('reason') or '')[:120]}", level="warning", platform=platform)
        self._seen_limits &= set(limits)                       # a platform that is free again can be announced again
        if announce and replies > self._replies:
            self.emit("reply", f"有 {replies - self._replies} 条新的 HR 回复待处理（共 {replies} 条）", level="info")
        self._replies = replies
        # A scoring run that stopped half way waits for the user: tell them once, even if it stopped before the program started.
        paused = {str(run["id"]): run for run in self.paused_scoring()}
        for run_id, run in paused.items():
            if run_id not in self._told_paused:
                self._told_paused.add(run_id)
                progress = run.get("progress") or {}
                done = f"已评 {progress['ai_completed']}/{progress['ai_total']}，" if progress.get("ai_completed") is not None and progress.get("ai_total") else ""
                left = len(run.get("remaining_job_ids") or [])
                self.emit("task", f"评分任务停在半路（{done}还剩 {left} 个没评）：到「今日活动 → 等待队列」继续或结束",
                          level="warning", data={"scoring_run": run_id})
        self._told_paused &= set(paused)
        # Level 2 of the keyword advice: every move between tiers. The first look only remembers where the log stands.
        moves = sorted(self.keyword_moves(), key=lambda move: int(move["id"]))
        if self._last_move is None:
            self._last_move = int(moves[-1]["id"]) if moves else 0
        for move in moves:
            if int(move["id"]) > self._last_move:
                self._last_move = int(move["id"])
                if announce:
                    before, after = KEYWORD_TIERS.get(move["from_tier"], move["from_tier"]), KEYWORD_TIERS.get(move["to_tier"], move["to_tier"])
                    self.emit("keyword", f"关键词「{move['keyword']}」从{before}调到{after}：{str(move.get('reason') or '')[:100]}",
                              level="warning" if move["to_tier"] == "paused" else "info")
        # Level 3: the pop-up reasons, once each (they come back the next day if still true).
        alerts = set(self.keyword_alerts())
        for alert in sorted(alerts - self._told_alerts):
            if announce:
                self.emit("keyword", f"关键词需要调整：{alert}，到工作台或「配置 → 搜索与关键词」查看建议", level="warning")
        self._told_alerts = alerts if announce else alerts | self._told_alerts
        self._replies = replies
        self._primed = True

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:
                pass                                           # the next poll tries again
            self._stop.wait(POLL_SECONDS)

    def start(self) -> None:
        threading.Thread(target=self.run, name="news-watcher", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
