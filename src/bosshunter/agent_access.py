"""What Claude and Codex may do with this program, and the news they can read.

Both talk to the program through its MCP server (``bosshunter mcp``) with a token. The token says who is asking; this module
says what they may do:

* ``auto``: the agent may start and stop the daily loop, clear incidents, approve and deliver postings (the default once a token
  exists, like a permission that is granted until the user objects);
* ``ask``: every control action becomes a request that waits for the user's yes in the settings page;
* ``readonly``: the agent may only read status, incidents and events.

The mode moves down by itself when the user keeps taking the permission back (turning it off, denying requests): three
objections within two weeks step it from auto to ask, and three more from ask to readonly. The user can set any mode by hand.
The state lives in ``data/agent_access.json``; only a hash of the token is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

MODES = ("auto", "ask", "readonly")
DEGRADE_AFTER = 3                      # objections within the window that move the mode one step down
DEGRADE_WINDOW_DAYS = 14
MAX_EVENTS = 300
MAX_REQUESTS = 100
READ_ACTIONS = {"status", "events", "incidents", "jobs"}
CONTROL_ACTIONS = {"daily_loop_start", "daily_loop_stop", "incident_resolve", "deliver"}
ACTION_LABELS = {
    "daily_loop_start": "启动每日循环", "daily_loop_stop": "停止每日循环", "incident_resolve": "把异常标记为已处理", "deliver": "批准并投递岗位",
}


class AccessDenied(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class AgentAccess:
    def __init__(self, path: Path, *, now: Callable[[], datetime] = datetime.now):
        self.path = Path(path)
        self.now = now
        self._lock = threading.RLock()
        self._ephemeral: dict[str, float] = {}                # tokens of the program's own duty runs: in memory only, they expire
        self.state = self._load()

    # ------------------------------------------------------------------ storage
    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("token_hash", "")
        data.setdefault("token_created", "")
        data.setdefault("enabled", True)
        data.setdefault("mode", "auto")
        data.setdefault("denials", [])
        data.setdefault("requests", [])
        data.setdefault("events", [])
        data.setdefault("next_event", 1)
        data.setdefault("ui_read", 0)
        return data

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _stamp(self) -> str:
        return self.now().isoformat(timespec="seconds")

    # ------------------------------------------------------------------ token
    def has_token(self) -> bool:
        return bool(self.state.get("token_hash"))

    def generate_token(self) -> str:
        """A new token (shown once; the old one stops working)."""
        token = "bh_" + secrets.token_urlsafe(32)
        with self._lock:
            self.state["token_hash"] = hashlib.sha256(token.encode()).hexdigest()
            self.state["token_created"] = self._stamp()
            self._save()
        self.emit("access", "智能体访问令牌已重新生成", level="info")
        return token

    def revoke_token(self) -> None:
        with self._lock:
            self.state["token_hash"] = ""
            self.state["token_created"] = ""
            self._save()
        self.emit("access", "智能体访问令牌已撤销", level="warning")

    def issue_ephemeral(self, minutes: int = 20) -> str:
        """A token for one run of the built-in duty: valid for ``minutes``, never written to disk, independent of the user's own token."""
        token = "bhd_" + secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._ephemeral = {digest: until for digest, until in self._ephemeral.items() if until > now}
            self._ephemeral[hashlib.sha256(token.encode()).hexdigest()] = now + minutes * 60
        return token

    def verify(self, token: str) -> bool:
        if not token:
            return False
        digest = hashlib.sha256(str(token).encode()).hexdigest()
        with self._lock:
            until = self._ephemeral.get(digest)
            if until is not None:
                if until > time.time():
                    return True
                del self._ephemeral[digest]
        stored = str(self.state.get("token_hash") or "")
        if not stored:
            return False
        return hmac.compare_digest(stored, digest)

    # ------------------------------------------------------------------ mode
    @property
    def mode(self) -> str:
        return "readonly" if not self.state.get("enabled", True) else str(self.state.get("mode") or "auto")

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError("模式必须是 auto、ask 或 readonly")
        with self._lock:
            self.state["mode"] = mode
            self.state["enabled"] = True
            self.state["denials"] = []                        # a choice made by hand starts afresh
            self._save()
        self.emit("access", f"智能体权限设为：{ {'auto': '自动', 'ask': '询问', 'readonly': '只读'}[mode] }", level="info")

    def set_enabled(self, enabled: bool) -> None:
        """Turning the permission off is an objection; doing it again and again moves the mode down."""
        with self._lock:
            was = bool(self.state.get("enabled", True))
            self.state["enabled"] = bool(enabled)
            self._save()
        if was and not enabled:
            self.record_objection("disabled")

    def record_objection(self, kind: str) -> str | None:
        """Count one objection; returns the new mode when it moved down."""
        with self._lock:
            now = self.now()
            window = now - timedelta(days=DEGRADE_WINDOW_DAYS)
            recent = [item for item in self.state["denials"] if datetime.fromisoformat(item["time"]) >= window]
            recent.append({"time": self._stamp(), "kind": kind})
            self.state["denials"] = recent
            moved = None
            if len(recent) >= DEGRADE_AFTER and self.state.get("mode") != "readonly":
                moved = "ask" if self.state.get("mode") == "auto" else "readonly"
                self.state["mode"] = moved
                self.state["denials"] = []
            self._save()
        if moved:
            self.emit("access", f"你多次收回了智能体的权限，权限已调整为：{'询问' if moved == 'ask' else '只读'}", level="warning")
        return moved

    # ------------------------------------------------------------------ decisions
    def authorize(self, action: str) -> str:
        """``allow`` (do it now), ``ask`` (needs the user's yes) or raises AccessDenied."""
        if action in READ_ACTIONS:
            return "allow"
        if action not in CONTROL_ACTIONS:
            raise AccessDenied("unknown_action", f"未知操作：{action}")
        mode = self.mode
        if mode == "auto":
            return "allow"
        if mode == "ask":
            return "ask"
        raise AccessDenied("read_only", "当前智能体权限是只读：只能查看状态、异常和消息；需要操作请告诉用户在设置里调整")

    # ------------------------------------------------------------------ requests
    def create_request(self, action: str, params: dict, source: str = "") -> dict[str, Any]:
        with self._lock:
            item = {
                "id": uuid.uuid4().hex[:8], "time": self._stamp(), "action": action, "label": ACTION_LABELS.get(action, action),
                "params": params, "source": source, "status": "pending",
            }
            self.state["requests"].append(item)
            del self.state["requests"][:-MAX_REQUESTS]
            self._save()
        self.emit("request", f"智能体请求：{item['label']}，等待你确认", level="warning", data={"request_id": item["id"]})
        return item

    def pending_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self.state["requests"] if item["status"] == "pending"]

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self.state["requests"]:
                if item["id"] == request_id:
                    return dict(item)
        return None

    def decide(self, request_id: str, approve: bool) -> dict[str, Any]:
        with self._lock:
            for item in self.state["requests"]:
                if item["id"] == request_id and item["status"] == "pending":
                    item["status"] = "approved" if approve else "denied"
                    item["decided"] = self._stamp()
                    self._save()
                    chosen = dict(item)
                    break
            else:
                raise KeyError(request_id)
        if not approve:
            self.record_objection("denied_request")
        return chosen

    # ------------------------------------------------------------------ events
    def emit(self, kind: str, message: str, *, level: str = "info", platform: str = "", data: dict | None = None) -> int:
        with self._lock:
            event_id = int(self.state["next_event"])
            self.state["next_event"] = event_id + 1
            self.state["events"].append({"id": event_id, "time": self._stamp(), "kind": kind, "level": level, "platform": platform,
                                         "message": message[:300], "data": data or {}})
            del self.state["events"][:-MAX_EVENTS]
            self._save()
        return event_id

    def events_since(self, since: int = 0, limit: int = 50) -> dict[str, Any]:
        with self._lock:
            items = [dict(e) for e in self.state["events"] if int(e["id"]) > int(since)][:max(1, min(limit, 200))]
            return {"events": items, "latest": int(self.state["next_event"]) - 1}

    # ------------------------------------------------------------------ the message center (what the user has seen)
    def messages(self, limit: int = 100) -> dict[str, Any]:
        """Newest first, with how many the user has not looked at yet."""
        with self._lock:
            items = [dict(e) for e in reversed(self.state["events"])][:max(1, min(limit, 300))]
            unread = sum(1 for e in self.state["events"] if int(e["id"]) > int(self.state.get("ui_read", 0)))
            return {"messages": items, "unread": unread, "latest": int(self.state["next_event"]) - 1}

    def mark_read(self, upto: int | None = None) -> None:
        with self._lock:
            latest = int(self.state["next_event"]) - 1
            self.state["ui_read"] = latest if upto is None else max(int(self.state.get("ui_read", 0)), min(int(upto), latest))
            self._save()

    # ------------------------------------------------------------------ for the settings page
    def public_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "has_token": self.has_token(), "token_created": self.state.get("token_created", ""), "enabled": bool(self.state.get("enabled", True)),
                "mode": self.mode, "saved_mode": self.state.get("mode", "auto"), "objections": len(self.state.get("denials", [])),
                "degrade_after": DEGRADE_AFTER, "window_days": DEGRADE_WINDOW_DAYS, "requests": [dict(r) for r in self.state["requests"][-20:]],
                "recent_events": [dict(e) for e in self.state["events"][-15:]],
            }
