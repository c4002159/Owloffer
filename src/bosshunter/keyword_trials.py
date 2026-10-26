"""Trying out the role names that postings carry and the keywords do not search for, and keeping what works.

The keywords are written from the resume; the postings show what the market calls the same work. ``term_stats`` counts every posting by
its role name (``role_name``: "高级Java开发工程师（上海）" -> "java开发工程师"). This module turns those counts into small experiments:

1. a few free *slots* (default 3) are filled with role names no keyword covers; the choice is a draw weighted by how likely a name is
   to pass (Thompson sampling: what was scored so far, shrunk towards the average pass rate) and by how common the name is, so
   promising names are tried first and unknown ones still get their chance;
2. a tried name is a normal keyword in the *explore* tier (a small share of the reads, ``keyword_strategy``) and stays in its slot until
   it has brought in enough jobs (15) or ten days passed;
3. then it is judged by the jobs it brought in: a pass rate near the average or better (80 %) keeps the keyword (the tier rules go on
   promoting or pausing it); anything else removes it again and the name is not tried for 30 days.

Nothing here touches the user's own keywords: only words this module added are ever removed, and a word the user removed is
simply forgotten. What happened is in ``data/keyword_trials.json`` and is announced in the message center.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from bosshunter import keyword_strategy as ks
from bosshunter import term_stats
from bosshunter.keyword_advisor import _with_words, _words

STORE_NAME = "keyword_trials.json"
SLOTS = 3
TRIAL_MIN_JOBS = 15
TRIAL_MAX_DAYS = 10
KEEP_RATIO = 0.8
COOLDOWN_DAYS = 30
SHRINK = 6                  # how many "average" postings every name is credited with before its own numbers count
MAX_HISTORY = 60

_LOCK = threading.Lock()


def store_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / STORE_NAME


def _read(base_dir: Path) -> dict:
    try:
        data = json.loads(store_path(base_dir).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(base_dir: Path, state: dict) -> None:
    path = store_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["history"] = list(state.get("history") or [])[-MAX_HISTORY:]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def display(role: str) -> str:
    """A role name as it is searched: short English words are acronyms (ai -> AI), longer ones are capitalised (java -> Java)."""
    return re.sub(r"[a-z]+", lambda match: match.group(0).upper() if len(match.group(0)) <= 3 else match.group(0).capitalize(), role)


def state(base_dir: Path) -> dict:
    """``{"active": [...], "history": [...], "last_run": ""}`` for the settings page."""
    data = _read(base_dir)
    return {"active": list(data.get("active") or []), "history": list(data.get("history") or [])[-20:], "last_run": str(data.get("last_run") or "")}


def pick(candidates: list[dict], baseline: float, free: int, rng: random.Random, blocked: set[str]) -> list[dict]:
    """``free`` role names drawn from ``candidates`` (see the module text); names that were scored often and never passed are skipped."""
    prior = min(max(baseline, 0.05), 0.9) if baseline > 0 else 0.2
    drawn = []
    for item in candidates:
        if item["role"] in blocked or (item["scored"] >= 6 and item["passed"] == 0):
            continue
        theta = rng.betavariate(item["passed"] + SHRINK * prior + 0.01, item["scored"] - item["passed"] + SHRINK * (1 - prior) + 0.01)
        drawn.append((theta * item["seen"] ** 0.5, item))
    drawn.sort(key=lambda pair: -pair[0])
    return [item for _score, item in drawn[:max(free, 0)]]


def _remove(config: dict, word: str) -> dict:
    return _with_words(config, lambda current: [item for item in current if item != word])


def run(conn: sqlite3.Connection, config: dict, base_dir: Path, today: date | None = None, rng: random.Random | None = None,
        now: datetime | None = None) -> dict:
    """Judge the running trials, start new ones in the free slots. ``config`` of the result is the changed config, or None."""
    ks.ensure_tables(conn)
    today = today or date.today()
    now = now or datetime.now()
    rng = rng or random.Random()
    automation = config.get("automation") or {}
    slots = max(int(automation.get("keyword_trial_slots", SLOTS) or 0), 0)
    with _LOCK:
        data = _read(base_dir)
        active = [dict(item) for item in data.get("active") or [] if isinstance(item, dict) and item.get("keyword")]
        history = [dict(item) for item in data.get("history") or [] if isinstance(item, dict)]
        words = _words(config)
        baseline = term_stats.baseline(conn, today=today)
        scores = ks.keyword_scores(conn)
        started: list[str] = []
        kept: list[str] = []
        dropped: list[str] = []
        changed = False

        def close(item: dict, result: str, reason: str, n: int, rate: float) -> None:
            history.append({"keyword": item["keyword"], "role": item.get("role", ""), "result": result, "reason": reason, "jobs": n,
                            "pass_rate": round(rate, 3), "started": item.get("started", ""), "at": now.isoformat(timespec="seconds")})

        still: list[dict] = []
        for item in active:
            word = item["keyword"]
            score = scores.get(word) or {"n": 0, "pass_rate": 0.0}
            n, rate = int(score["n"]), float(score["pass_rate"])
            age = (today - date.fromisoformat(str(item.get("started") or today.isoformat())[:10])).days
            if word not in words:                                              # the user took it out: not a result
                close(item, "removed", "你手动移除了这个词", n, rate)
            elif n >= TRIAL_MIN_JOBS and rate >= baseline * KEEP_RATIO:
                kept.append(word)
                close(item, "kept", f"读到 {n} 个岗位，通过率 {rate:.0%}，不低于平均 {baseline:.0%} 的 80%", n, rate)
            elif n >= TRIAL_MIN_JOBS or age >= TRIAL_MAX_DAYS:
                dropped.append(word)
                reason = f"读到 {n} 个岗位，通过率 {rate:.0%}，低于平均 {baseline:.0%} 的 80%" if n >= TRIAL_MIN_JOBS else f"{age} 天只读到 {n} 个岗位，几乎没有产出"
                close(item, "dropped", reason, n, rate)
                config = _remove(config, word)
                conn.execute("DELETE FROM keyword_tiers WHERE keyword = ?", (word,))
                changed = True
            else:
                still.append(item)
        active = still

        free = slots - len(active)
        if free > 0:
            blocked = {str(item.get("role")) for item in active}
            cutoff = (today - timedelta(days=COOLDOWN_DAYS)).isoformat()
            blocked |= {str(item.get("role")) for item in history if item.get("result") in ("dropped", "kept") and str(item.get("at", ""))[:10] >= cutoff}
            exclude = [str(word) for word in (config.get("profile") or {}).get("deal_breakers") or []] + \
                      [str(word) for word in (config.get("filters") or {}).get("deal_breakers") or []]
            for item in pick(term_stats.mine_roles(conn, words, exclude, today=today), baseline, free, rng, blocked):
                word = display(item["role"])
                if word.lower() in (w.lower() for w in words):
                    continue
                words.append(word)
                config = _with_words(config, lambda current, word=word: current + [word])
                conn.execute(
                    "INSERT INTO keyword_tiers (keyword, tier, pinned, reason, updated_at) VALUES (?, 'explore', 0, '岗位名称试用', CURRENT_TIMESTAMP) "
                    "ON CONFLICT(keyword) DO NOTHING", (word,))
                active.append({"keyword": word, "role": item["role"], "started": today.isoformat(),
                               "seen": item["seen"], "scored": item["scored"], "passed": item["passed"]})
                started.append(word)
                changed = True
        conn.commit()
        _write(base_dir, {"active": active, "history": history, "last_run": now.isoformat(timespec="seconds")})
    return {"config": config if changed else None, "started": started, "kept": kept, "dropped": dropped}


def summary(result: dict) -> str:
    parts = []
    if result.get("started"):
        parts.append("开始试用岗位名称：" + "、".join(result["started"]))
    if result.get("kept"):
        parts.append("试用结果好，保留：" + "、".join(result["kept"]))
    if result.get("dropped"):
        parts.append("试用效果不好，已移除：" + "、".join(result["dropped"]))
    return "；".join(parts)
