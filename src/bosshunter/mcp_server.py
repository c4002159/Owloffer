"""A small MCP server (stdio, JSON-RPC) that lets Claude and Codex watch and steer BossHunter.

It only forwards to the program's token-protected ``/api/agent/control`` routes, so what an agent may do is decided there
(the permission mode in the settings page), never here. Configure it with two environment variables:
``BOSSHUNTER_TOKEN`` (made in the settings page) and ``BOSSHUNTER_URL`` (default http://127.0.0.1:8686).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, TextIO

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "bosshunter", "version": "1.0"}

Http = Callable[[str, str, dict | None], tuple[int, Any]]


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


TOOLS: list[dict[str, Any]] = [
    {"name": "bosshunter_status", "description": "BossHunter 当前状态：每日循环进度、未处理异常、正在运行的任务、被暂停的平台、今天的投递数量、等待用户确认的请求。先调用它了解情况。",
     "inputSchema": _obj({})},
    {"name": "bosshunter_events", "description": "读取新消息（事件流）：异常、平台暂停、每日循环结束、你自己做过的操作。传上次返回的 latest 作为 since，只拿新的。",
     "inputSchema": _obj({"since": {"type": "integer", "description": "只返回 id 大于它的事件，默认 0"}, "limit": {"type": "integer"}})},
    {"name": "bosshunter_daily_loop_start", "description": "启动每日循环（采集→批准→投递→再采集）。时间是 HH:MM 本机时间。",
     "inputSchema": _obj({"time_window": {"type": "boolean", "description": "false = 不限时间，全天运行；省略或 true = 用 start_time 到 end_time"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}, "delivery_cap": {"type": "integer", "description": "每个平台当天投递上限，0 不限"}})},
    {"name": "bosshunter_daily_loop_stop", "description": "停止每日循环。", "inputSchema": _obj({})},
    {"name": "bosshunter_resolve_incident", "description": "把一条异常标记为已处理（修复之后再调用）。",
     "inputSchema": _obj({"incident_id": {"type": "string"}}, ["incident_id"])},
    {"name": "bosshunter_deliver", "description": "批准并投递岗位：给 job_ids，或给 platform（该平台所有待确认和已通过的岗位，最多 limit 个）。是否直接执行取决于用户设置的权限；需要确认时会返回 request_id。",
     "inputSchema": _obj({"job_ids": {"type": "array", "items": {"type": "string"}}, "platform": {"type": "string"}, "limit": {"type": "integer"}})},
    {"name": "bosshunter_request_status", "description": "查看一个待用户确认的请求现在是 pending、approved 还是 denied。",
     "inputSchema": _obj({"request_id": {"type": "string"}}, ["request_id"])},
]


def _route(name: str, args: dict) -> tuple[str, str, dict | None]:
    if name == "bosshunter_status":
        return "GET", "/api/agent/control/status", None
    if name == "bosshunter_events":
        query = urllib.parse.urlencode({"since": int(args.get("since") or 0), "limit": int(args.get("limit") or 50)})
        return "GET", f"/api/agent/control/events?{query}", None
    if name == "bosshunter_daily_loop_start":
        return "POST", "/api/agent/control/daily-loop/start", {k: args[k] for k in ("time_window", "start_time", "end_time", "delivery_cap") if k in args}
    if name == "bosshunter_daily_loop_stop":
        return "POST", "/api/agent/control/daily-loop/stop", {}
    if name == "bosshunter_resolve_incident":
        return "POST", f"/api/agent/control/incidents/{urllib.parse.quote(str(args['incident_id']))}/resolve", {}
    if name == "bosshunter_deliver":
        return "POST", "/api/agent/control/deliver", {k: args[k] for k in ("job_ids", "platform", "limit") if k in args}
    if name == "bosshunter_request_status":
        return "GET", f"/api/agent/control/requests/{urllib.parse.quote(str(args['request_id']))}", None
    raise KeyError(name)


def handle(message: dict, http: Http) -> dict | None:
    """The reply to one JSON-RPC message (None for notifications)."""
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        return None                                                        # notifications get no answer
    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        return {"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": asked, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": ident, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": ident, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            verb, path, body = _route(str(params.get("name")), params.get("arguments") or {})
        except KeyError:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": f"未知工具：{params.get('name')}"}}
        status, payload = http(verb, path, body)
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        return {"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text": text}], "isError": status >= 400}}
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"不支持的方法：{method}"}}


def make_http(base_url: str, token: str, timeout: float = 120.0) -> Http:
    def call(verb: str, path: str, body: dict | None) -> tuple[int, Any]:
        data = json.dumps(body or {}).encode("utf-8") if verb != "GET" else None
        req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=verb,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
            except ValueError:
                return exc.code, {"error": str(exc)}
        except (urllib.error.URLError, OSError) as exc:
            return 503, {"error": f"连不上 BossHunter（{base_url}）：{exc}。请确认程序在运行"}

    return call


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None, http: Http | None = None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    http = http or make_http(os.environ.get("BOSSHUNTER_URL", "http://127.0.0.1:8686"), os.environ.get("BOSSHUNTER_TOKEN", ""))
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line), http)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "不是合法的 JSON"}}
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
