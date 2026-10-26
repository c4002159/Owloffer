"""Built-in duty: while the program runs and the switch is on, it calls the user's Claude on a schedule to watch and steer it.

The MCP tools (``mcp_server.py``) let an agent look at and steer the program, but somebody has to call the agent. Before this module that
was a scheduled task the user had to create by hand in the agent's own app. Here the program does it itself: every ``interval_minutes`` it
starts the Claude command line in print mode (``claude -p``) with the duty prompt, gives it the program's own MCP server through a
token that lives only in memory for the length of that run, and writes what the agent reported to the message center.

What the agent may do is still decided by the permission mode of the settings page (auto / ask / read only): this module only
starts it. The agent is given the MCP tools of this program and nothing else (no shell, no file edits, no web), so a duty run can
look, start or stop the loop, close incidents and deliver within the permission mode, and report. It cannot change code.

It needs a Claude command line that is logged in (``claude`` then ``/login`` once, or an ``ANTHROPIC_API_KEY``); every run uses the
user's own Claude quota or API credit, which is why the switch is off by default and the interval is an hour.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

STORE_NAME = "agent_duty.json"
DEFAULT_INTERVAL = 60
MIN_INTERVAL = 10
DEFAULT_TIMEOUT_MINUTES = 15
DEFAULT_MAX_TURNS = 14
MAX_RUNS_KEPT = 20
TOKEN_MINUTES = DEFAULT_TIMEOUT_MINUTES + 5

DEFAULT_PROMPT = """你是本机求职程序 OwlOffer（BossHunter）的值班员，每次只做一轮值班，做完就结束。只用 bosshunter_* 工具。
1. 先调用 bosshunter_status 看整体状态；再用 bosshunter_events 读新消息（把上次值班得到的 latest 当作 since；没有就用 0）。
2. 每日循环没在运行、而且不是用户停的（stopped_reason 不是 user）：用 bosshunter_daily_loop_start 启动，时间窗口关闭（time_window=false）。
3. 看未处理的异常：
   - 遇到验证码、需要登录、被风控的平台：不要尝试绕过，也不要反复试，只汇报，让用户亲自处理。
   - 原因已经消失的旧异常（例如平台已恢复、任务已结束），用 bosshunter_resolve_incident 标记。
   - 采集或投递长时间没有进展、反复失败：汇报现象和你的判断，不要乱改设置。
4. 不要替用户回复 HR、发送简历，不要做用户没授权的事；权限被拒绝或是只读时只汇报，不要重复尝试。
5. 最后用不超过 8 行中文汇报：循环状态、今天各平台投递数、需要用户处理的事（没有就写「无」）。"""

_RUN_LOCK = threading.Lock()


def store_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / STORE_NAME


def _read(base_dir: Path) -> dict:
    try:
        data = json.loads(store_path(base_dir).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(base_dir: Path, data: dict) -> None:
    path = store_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["runs"] = list(data.get("runs") or [])[-MAX_RUNS_KEPT:]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def settings(base_dir: Path) -> dict:
    data = _read(base_dir)
    try:
        interval = max(int(data.get("interval_minutes", DEFAULT_INTERVAL)), MIN_INTERVAL)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL
    return {"enabled": bool(data.get("enabled", False)), "interval_minutes": interval,
            "prompt": str(data.get("prompt") or DEFAULT_PROMPT), "cli_path": str(data.get("cli_path") or "")}


def state(base_dir: Path, now: datetime | None = None) -> dict:
    """What the settings page shows: the settings, whether a Claude command line was found, the last reports and when the next run is due."""
    data = _read(base_dir)
    current = settings(base_dir)
    runs = list(data.get("runs") or [])
    last = runs[-1] if runs else None
    due = ""
    if current["enabled"]:
        try:
            base = datetime.fromisoformat(last["at"]) if last else (now or datetime.now())
            due = (base.timestamp() + current["interval_minutes"] * 60 if last else base.timestamp())
            due = datetime.fromtimestamp(due).isoformat(timespec="seconds")
        except (KeyError, ValueError, TypeError):
            due = ""
    cli = find_cli(current["cli_path"])
    return {**current, "prompt_is_default": current["prompt"] == DEFAULT_PROMPT, "default_prompt": DEFAULT_PROMPT, "cli_found": bool(cli), "cli": cli or "",
            "runs": runs[-8:][::-1], "next_run": due, "running": _RUN_LOCK.locked()}


def update(base_dir: Path, changes: dict) -> dict:
    data = _read(base_dir)
    if "enabled" in changes:
        data["enabled"] = bool(changes["enabled"])
    if "interval_minutes" in changes:
        data["interval_minutes"] = max(int(changes["interval_minutes"]), MIN_INTERVAL)
    if "prompt" in changes:
        text = str(changes["prompt"] or "").strip()
        data["prompt"] = text if text and text != DEFAULT_PROMPT else ""
    if "cli_path" in changes:
        data["cli_path"] = str(changes["cli_path"] or "").strip()
    _write(base_dir, data)
    return state(base_dir)


def find_cli(configured: str = "") -> str | None:
    """The Claude command line: the configured path, ``claude`` on the PATH, then where the desktop app and the installer put it."""
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    found = shutil.which("claude")
    if found:
        candidates.append(found)
    home = Path.home()
    candidates += [str(home / ".claude" / "local" / "claude"), str(home / ".local" / "bin" / "claude"), str(home / ".claude" / "local" / "claude.exe")]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        patterns = glob.glob(str(Path(local) / "Claude-Data" / "*" / "claude-code" / "*" / "*" / "claude.exe")) + \
                   glob.glob(str(Path(local) / "AnthropicClaude" / "**" / "claude.exe"), recursive=True)
        candidates += sorted(patterns, key=lambda path: os.path.getmtime(path), reverse=True)
    return next((path for path in candidates if path and os.path.isfile(path)), None)


def mcp_config(python: str, base_url: str, token: str) -> str:
    """The MCP configuration given to the agent for one run (a JSON string, nothing is written to disk)."""
    return json.dumps({"mcpServers": {"bosshunter": {"command": python, "args": ["-m", "bosshunter.main", "mcp"],
                                                     "env": {"BOSSHUNTER_TOKEN": token, "BOSSHUNTER_URL": base_url}}}}, ensure_ascii=False)


def build_command(cli: str, prompt: str, config_json: str, max_turns: int = DEFAULT_MAX_TURNS) -> list[str]:
    return [cli, "-p", prompt, "--mcp-config", config_json, "--strict-mcp-config", "--allowedTools", "mcp__bosshunter",
            "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch",
            "--max-turns", str(max_turns), "--output-format", "text", "--no-session-persistence"]


def classify(returncode: int, output: str) -> tuple[str, str]:
    """``(status, summary)``: ok / login_required / failed, and what to show."""
    text = (output or "").strip()
    low = text.lower()
    if "not logged in" in low or "/login" in low or "invalid api key" in low or ("authentication" in low and "error" in low):
        return "login_required", "Claude 命令行还没有登录：在终端运行 claude，输入 /login 登录一次（或设置 ANTHROPIC_API_KEY）后再试"
    if returncode != 0 and not text:
        return "failed", f"Claude 命令行异常退出（代码 {returncode}）"
    if returncode != 0:
        return "failed", text[-600:]
    return "ok", text[-1200:] if text else "（没有输出）"


def run_once(base_dir: Path, *, python: str, base_url: str, issue_token: Callable[[int], str], emit: Callable[..., Any],
             prompt: str | None = None, timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES, check_only: bool = False,
             runner: Callable[..., Any] = subprocess.run, now: Callable[[], datetime] = datetime.now) -> dict:
    """One duty run (or, with ``check_only``, a tiny call that only tests that the command line is logged in). Never raises."""
    if not _RUN_LOCK.acquire(blocking=False):
        return {"status": "busy", "summary": "上一次值班还没有结束"}
    began = time.monotonic()
    try:
        current = settings(base_dir)
        cli = find_cli(current["cli_path"])
        if not cli:
            result = {"status": "no_cli", "summary": "没有找到 Claude 命令行。安装 Claude Code，或在这里填它的路径"}
        else:
            if check_only:
                command = [cli, "-p", "只回复两个字：好的", "--max-turns", "1", "--output-format", "text", "--no-session-persistence"]
            else:
                command = build_command(cli, prompt or current["prompt"], mcp_config(python, base_url, issue_token(TOKEN_MINUTES)))
            try:
                done = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_minutes * 60,
                              cwd=str(base_dir), stdin=subprocess.DEVNULL)
                status, summary = classify(done.returncode, (done.stdout or "") + ("\n" + done.stderr if done.returncode and done.stderr else ""))
                result = {"status": status, "summary": summary}
            except subprocess.TimeoutExpired:
                result = {"status": "failed", "summary": f"值班超过 {timeout_minutes} 分钟，已结束"}
            except OSError as exc:
                result = {"status": "failed", "summary": f"没能启动 Claude 命令行：{exc}"}
        result["seconds"] = round(time.monotonic() - began, 1)
        result["at"] = now().isoformat(timespec="seconds")
        result["check"] = check_only
        if not check_only:
            data = _read(base_dir)
            data.setdefault("runs", []).append(result)
            _write(base_dir, data)
            emit("agent", f"智能体值班：{result['summary'][:300]}", level="info" if result["status"] == "ok" else "warning")
        return result
    finally:
        _RUN_LOCK.release()


def due(base_dir: Path, now: datetime | None = None) -> bool:
    """Whether a run is due: the switch is on and the last run is older than the interval (or there was none)."""
    current = settings(base_dir)
    if not current["enabled"]:
        return False
    runs = list(_read(base_dir).get("runs") or [])
    if not runs:
        return True
    try:
        last = datetime.fromisoformat(runs[-1]["at"])
    except (KeyError, ValueError, TypeError):
        return True
    return ((now or datetime.now()) - last).total_seconds() >= current["interval_minutes"] * 60


def python_executable() -> str:
    return sys.executable
