"""Things to tell the user at a later start of the program, kept in ``data/reminders.json``.

A reminder has a day. At the first start of the server on or after that day it is delivered once: a message in the message
center (the same feed Claude and Codex read) and a line on the console. Some reminders carry a report that is computed at that
moment, so that it shows the data of the day and not of the day the reminder was written.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable


def _load(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(path: Path, items: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def add(path: Path, reminder_id: str, due_day: str, title: str, body: str, report: str = "") -> None:
    """Add (or replace) a reminder. ``report`` names a report to compute at delivery (``"depth"``)."""
    items = [item for item in _load(path) if item.get("id") != reminder_id]
    items.append({"id": reminder_id, "due": due_day, "title": title, "body": body, "report": report, "delivered_at": ""})
    _save(path, items)


def due(path: Path, today: date | None = None) -> list[dict]:
    day = (today or date.today()).isoformat()
    return [item for item in _load(path) if item.get("due", "") <= day and not item.get("delivered_at")]


def deliver_due(path: Path, emit: Callable[..., Any], reports: dict[str, Callable[[], str]] | None = None, today: date | None = None) -> list[str]:
    """Deliver every due reminder once. Returns the texts delivered."""
    items = _load(path)
    delivered: list[str] = []
    day = (today or date.today()).isoformat()
    for item in items:
        if item.get("delivered_at") or item.get("due", "") > day:
            continue
        text = f"待办提醒：{item['title']}\n{item['body']}"
        compute = (reports or {}).get(str(item.get("report") or ""))
        if compute is not None:
            try:
                text += "\n" + compute()
            except Exception as exc:                                  # a failing report must not stop the reminder itself
                text += f"\n（报告生成失败：{type(exc).__name__}）"
        try:
            emit("todo", text[:1500], level="info")
        except Exception:
            continue                                                  # not delivered: tried again at the next start
        item["delivered_at"] = f"{day}"
        delivered.append(text)
    if delivered:
        _save(path, items)
    return delivered
