"""Tiny client for the local dashboard server, so the review window can start tasks there.

A task started through the dashboard server shows up on the dashboard ("running", logs, stop
button); a task run inside the review window would be invisible to it.
"""

import json
import urllib.error
import urllib.request

DEFAULT_PORT = 8686


def _call(method: str, path: str, body: dict | None = None, *, port: int = DEFAULT_PORT, timeout: float = 8.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route localhost via a proxy
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "null")
        except ValueError:
            payload = None
        return exc.code, payload
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def server_reachable(port: int = DEFAULT_PORT) -> bool:
    status, _ = _call("GET", "/api/workbench", port=port, timeout=4)
    return status == 200


def start_task(mode: str, port: int = DEFAULT_PORT) -> tuple[bool, dict | str]:
    """Start a dashboard task. Returns (True, task snapshot) or (False, human-readable reason)."""
    status, payload = _call("POST", "/api/workbench/task", {"mode": mode}, port=port, timeout=20)
    if status == 0:
        return False, "面板服务未运行"
    if status == 200 and isinstance(payload, dict):
        task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
        if task.get("id"):
            return True, task
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("error") or "")
        if payload.get("messages"):
            detail += "：" + "；".join(str(m) for m in payload["messages"])
    return False, detail or f"启动失败（HTTP {status}）"


def workbench_tasks(port: int = DEFAULT_PORT) -> tuple[dict | None, dict | None]:
    """(active task, last task) from the dashboard, or (None, None) when unreachable."""
    status, payload = _call("GET", "/api/workbench", port=port, timeout=6)
    if status != 200 or not isinstance(payload, dict):
        return None, None
    return payload.get("task"), payload.get("last_task")


def stop_task(task_id: str, port: int = DEFAULT_PORT) -> bool:
    status, _ = _call("POST", f"/api/workbench/task/{task_id}/stop", {}, port=port, timeout=8)
    return status == 200
