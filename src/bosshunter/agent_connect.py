"""Connect the user's Claude to this program with one click, without showing or typing a token or a path.

The settings page used to print ``claude mcp add ... -e BOSSHUNTER_TOKEN=<token> -- "<path to python>" ...`` for the user to copy
into a terminal. That showed a path of the user's own machine, and the token was only visible once. Here the program does it itself:
it finds the Claude command line (``agent_duty.find_cli``), makes a token, and registers its own MCP server in the user's Claude
configuration; the token goes straight into that configuration and is never shown.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

SERVER_NAME = "bosshunter"


def display_path(path: str) -> str:
    """A path as it is shown on screen: only the last parts, never the folders of the user's machine (``...\\venv\\Scripts\\python.exe``)."""
    parts = [part for part in str(path or "").replace("\\", "/").split("/") if part]
    return ".../" + "/".join(parts[-3:]) if len(parts) > 3 else "/".join(parts)


def registered(cli: str, runner: Callable[..., Any] = subprocess.run) -> bool | None:
    """Whether Claude already knows an MCP server of this name (None when the command line could not be asked)."""
    try:
        done = runner([cli, "mcp", "get", SERVER_NAME], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode == 0


def connect(cli: str, python: str, base_url: str, token: str, runner: Callable[..., Any] = subprocess.run) -> tuple[bool, str]:
    """Register this program's MCP server in the user's Claude (user scope: every project sees it), replacing an older entry."""
    try:
        runner([cli, "mcp", "remove", SERVER_NAME, "--scope", "user"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        done = runner([cli, "mcp", "add", "--scope", "user", SERVER_NAME, "-e", f"BOSSHUNTER_TOKEN={token}", "-e", f"BOSSHUNTER_URL={base_url}",
                       "--", python, "-m", "bosshunter.main", "mcp"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"没能运行 Claude 命令行：{type(exc).__name__}"
    if done.returncode != 0:
        return False, ((done.stderr or done.stdout or "").strip()[-300:] or f"Claude 命令行返回 {done.returncode}")
    return True, "已连接：重启 Claude（或新开一个会话）后就能看到 bosshunter_* 工具"

