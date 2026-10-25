"""Advice on the search keywords, from what they actually brought in.

Three levels, from quiet to loud:
1. while collecting: a keyword that stops paying is dropped for the run and its share goes to the better ones (``orchestrator``);
2. after a run: every move between tiers is a message in the message center (``news_watcher``);
3. this module: when the picture is bad enough that the user should decide, the workbench shows a pop-up with suggestions that
   can be applied in one click (and undone).

The pop-up appears only when one of these holds:
* the core keywords' pass rate is below 10 % on each of the last two days with data (at least 30 jobs in all);
* half or more of the keywords are paused or dead (15+ jobs, none passed);
* each of the last two full days brought fewer than 30 new jobs although collection ran;
* postings the user's scoring passed share a title no keyword covers yet (3+ passed jobs);
* a platform read 500+ postings in 24 hours and found fewer than 30 new ones (its pool is used up): the pop-up then offers to ask
  the model for new directions (``ai/keyword_expansion.py``); the same button is always in the settings page.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from bosshunter import keyword_strategy as ks
from bosshunter import term_stats
from bosshunter.ai import keyword_expansion, keyword_lint

CORE_PASS_FLOOR = 0.10
CORE_MIN_SAMPLES = 30
DAY_MIN_SAMPLES = 5
DEAD_SHARE = 0.5
DEAD_MIN_WORDS = 4
LOW_NEW_PER_DAY = 30
DRY_NEW = 30                      # a platform that brought fewer new jobs than this in 24 hours ...
DRY_MIN_SEEN = 500                # ... although at least this many postings were read there: its pool is nearly used up
NOT_TARGETS = {"liepin", "zhilian_campus"}
CANDIDATE_MIN_PASSED = 3
MAX_ADDITIONS = 5
PAUSE_DAYS = ks.PAUSE_DAYS
SNOOZE_HOURS = 24

_LOCK = threading.Lock()
_BRACKETS = re.compile(r"[（(\[【].*?[）)\]】]")
_SEPARATORS = re.compile(r"[-—–·_/|,，、+＋]")
_NOISE = re.compile(r"^\d+|\d+(?:k|K|薪|万|元)?$")


def _lists(config: dict) -> list[list]:
    """Every keyword list of the config: the shared one and each platform's own (the settings page keeps them alike)."""
    found = [(config.get("search") or {}).get("keywords") or []]
    for platform in (config.get("platforms") or {}).values():
        words = ((platform or {}).get("search") or {}).get("keywords")
        if words:
            found.append(words)
    return found


def _words(config: dict) -> list[str]:
    raw = [word for words in _lists(config) for word in words]
    return list(dict.fromkeys(str(word).strip() for word in raw if str(word).strip()))


def _with_words(config: dict, change) -> dict:
    """A copy of the config in which ``change(list)`` was applied to every keyword list."""
    search = dict(config.get("search") or {})
    search["keywords"] = change(list(search.get("keywords") or []))
    platforms = {}
    for name, platform in (config.get("platforms") or {}).items():
        platform = dict(platform or {})
        own = dict(platform.get("search") or {})
        if own.get("keywords"):
            own["keywords"] = change(list(own["keywords"]))
            platform["search"] = own
        platforms[name] = platform
    return {**config, "search": search, **({"platforms": platforms} if platforms else {})}


def _days(conn: sqlite3.Connection, today: date, keywords: list[str]) -> dict[str, dict]:
    """Per local day, the jobs of ``keywords`` that arrived and how many of them passed."""
    if not keywords:
        return {}
    since = (today - timedelta(days=3)).isoformat()
    marks = ",".join("?" for _ in keywords)
    rows = conn.execute(
        f"SELECT date(created_at, 'localtime') AS day, status FROM jobs WHERE source_keyword IN ({marks}) "
        "AND date(created_at, 'localtime') >= ?",
        [*keywords, since],
    ).fetchall()
    days: dict[str, dict] = {}
    for row in rows:
        item = days.setdefault(row["day"], {"n": 0, "passed": 0})
        item["n"] += 1
        item["passed"] += row["status"] in ks.PASSED
    return days


def _new_per_day(conn: sqlite3.Connection, today: date) -> dict[str, int]:
    since = (today - timedelta(days=3)).isoformat()
    rows = conn.execute(
        "SELECT date(run_at, 'localtime') AS day, SUM(new) AS new FROM keyword_runs "
        "WHERE date(run_at, 'localtime') >= ? GROUP BY day",
        (since,),
    ).fetchall()
    return {row["day"]: int(row["new"] or 0) for row in rows}


def _dry_platforms(conn: sqlite3.Connection, config: dict, now: datetime | None = None) -> list[dict]:
    """Platforms whose search has stopped finding new jobs: the existing keywords no longer reach anything there."""
    automation = config.get("automation") or {}
    limit = int(automation.get("keyword_dry_new", DRY_NEW))
    least = int(automation.get("keyword_dry_seen", DRY_MIN_SEEN))
    since = ((now or datetime.now()) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = conn.execute("SELECT platform, SUM(seen) AS seen, SUM(new) AS new FROM keyword_runs WHERE run_at >= datetime(?, 'utc') GROUP BY platform", (since,)).fetchall()
    except sqlite3.Error:
        return []
    return [{"platform": row["platform"], "seen": int(row["seen"] or 0), "new": int(row["new"] or 0)}
            for row in rows if row["platform"] not in NOT_TARGETS and int(row["seen"] or 0) >= least and int(row["new"] or 0) < limit]


def _clean_title(title: str) -> str:
    cleaned = _BRACKETS.sub("", title or "")
    cleaned = _SEPARATORS.split(cleaned)[0].strip()
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def _candidate_words(conn: sqlite3.Connection, words: list[str]) -> list[tuple[str, int]]:
    """Titles that passed scoring several times and that no keyword covers (an existing keyword inside the title covers it)."""
    marks = ",".join("?" for _ in ks.PASSED)
    rows = conn.execute(
        f"SELECT title FROM jobs WHERE status IN ({marks}) AND created_at >= datetime('now', ?) AND COALESCE(title, '') != ''",
        [*ks.PASSED, f"-{ks.WINDOW_DAYS} day"],
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        title = _clean_title(row["title"])
        if 4 <= len(title) <= 14 and not _NOISE.search(title):
            counts[title] = counts.get(title, 0) + 1
    lowered = [word.lower() for word in words]
    result = []
    for title, count in counts.items():
        if count < CANDIDATE_MIN_PASSED or keyword_lint.check(title) is not None:     # vague titles make vague keywords
            continue
        low = title.lower()
        if any(word in low or low in word for word in lowered):
            continue
        result.append((title, count))
    return sorted(result, key=lambda item: (-item[1], item[0]))[:MAX_ADDITIONS]


def _display(term: str) -> str:
    """A mined word as it should be searched: short English words are acronyms (aigc -> AIGC)."""
    return term.upper() if term.isascii() and term.isalpha() and len(term) <= 5 else term


def advise(conn: sqlite3.Connection, config: dict, plan: dict | None = None, today: date | None = None, base_dir: Path | None = None) -> dict:
    """``{"level3": bool, "reasons": [...], "suggestions": [...]}``; every suggestion has an ``id`` the apply call takes."""
    ks.ensure_tables(conn)
    today = today or date.today()
    words = _words(config)
    tiers = ks.current_tiers(conn, words, plan, today)
    scores = ks.keyword_scores(conn)
    reasons: list[dict] = []
    suggestions: list[dict] = []

    def suggest(kind: str, keyword: str, reason: str) -> None:
        if not any(item["kind"] == kind and item["keyword"] == keyword for item in suggestions):
            suggestions.append({"id": f"{kind}:{keyword}", "kind": kind, "keyword": keyword, "reason": reason})

    dead = [w for w in words if scores.get(w, {}).get("n", 0) >= ks.PAUSE_SAMPLES and scores[w]["pass_rate"] == 0]

    # 1. the core keywords bring little that passes, two days running
    core = tiers["core"]
    days = _days(conn, today, core)
    with_data = {day: item for day, item in days.items() if item["n"] >= DAY_MIN_SAMPLES}
    recent = sorted(with_data)[-2:]
    total = sum(days[day]["n"] for day in recent)
    if len(recent) == 2 and total >= CORE_MIN_SAMPLES and all(with_data[day]["passed"] / with_data[day]["n"] < CORE_PASS_FLOOR for day in recent):
        rate = sum(with_data[day]["passed"] for day in recent) / total
        reasons.append({"code": "core_low_pass", "message": f"核心关键词最近两天通过率只有 {rate:.0%}（{total} 个岗位），低于 {CORE_PASS_FLOOR:.0%}"})
        for word in core:
            info = scores.get(word)
            if info and info["n"] >= 8 and info["pass_rate"] < CORE_PASS_FLOOR:
                suggest("pause", word, f"近 14 天 {info['n']} 个岗位只有 {info['pass_rate']:.0%} 通过")

    # 2. half of the keywords are paused or dead
    gone = set(tiers["paused"]) | set(dead)
    if len(words) >= DEAD_MIN_WORDS and len(gone) / len(words) >= DEAD_SHARE:
        reasons.append({"code": "mostly_dead", "message": f"{len(words)} 个关键词里有 {len(gone)} 个已暂停或没有产出"})
        for word in dead:
            if word not in tiers["paused"]:
                suggest("pause", word, f"{scores[word]['n']} 个岗位一个也没通过")

    # 3. two full days with few new jobs although collection ran
    per_day = _new_per_day(conn, today)
    last_two = [(today - timedelta(days=offset)).isoformat() for offset in (1, 2)]
    if all(day in per_day and per_day[day] < LOW_NEW_PER_DAY for day in last_two):
        reasons.append({"code": "few_new", "message": f"最近两天每天新增都不到 {LOW_NEW_PER_DAY} 个岗位（{per_day[last_two[1]]}、{per_day[last_two[0]]}）"})

    # 4. titles that passed scoring and no keyword covers
    additions = _candidate_words(conn, words)
    if additions:
        reasons.append({"code": "new_terms", "message": f"{len(additions)} 个通过评分的岗位名称还没有关键词覆盖"})
    for title, count in additions:
        suggest("add", title, f"{count} 个通过评分的岗位都叫这个名字，现有关键词没有覆盖")

    # 5. words that the postings themselves point to (term_stats.py), and what the model proposed with those numbers in front of it
    try:
        mined = term_stats.mine(conn, words, today=today)
    except Exception:
        mined = {"lift": [], "frequent": [], "baseline": 0.0}
    titles = [item for item in mined["lift"] if item.get("source") != "jd"]          # description words only inform the model, they are not searched
    for item in titles[:3]:
        suggest("add", _display(item["term"]), f"挖词：标题里有「{item['term']}」的岗位 {item['passed']}/{item['scored']} 通过（{item['rate']:.0%}，是平均的 {item['lift']} 倍），现有关键词没有覆盖")
    strong = [item for item in titles if item["scored"] >= 15 and item["lift"] >= 1.5]
    if strong:
        reasons.append({"code": "mined_terms", "message": f"{len(strong)} 个标题词的岗位通过率明显高于平均，现有关键词没有覆盖（如「{_display(strong[0]['term'])}」）"})
    dry = _dry_platforms(conn, config)
    names = {"boss": "BOSS 直聘", "zhilian": "智联招聘", "51job": "前程无忧"}
    fresh = base_dir is not None and keyword_expansion.fresh(base_dir)
    if dry:
        reasons.append({"code": "platform_dry", "message": "；".join(
            f"{names.get(item['platform'], item['platform'])}最近 24 小时读了 {item['seen']} 个岗位，新增只有 {item['new']} 个" for item in dry) + "：现有关键词在这里快挖完了，可以让模型扩展新方向"})
    if base_dir is not None:
        for item in keyword_expansion.load(base_dir)[:5]:
            if not any(word.lower() == str(item["keyword"]).lower() for word in words) and keyword_lint.check(str(item["keyword"])) is None:
                suggest("add", str(item["keyword"]), f"模型建议（{item.get('direction') or '新方向'}）：{item.get('reason') or '现有关键词没有覆盖这个说法'}")

    return {"level3": bool(reasons), "reasons": reasons, "suggestions": suggestions, "dry": dry, "expand_wanted": bool(dry) and not fresh, "mined": {"baseline": mined["baseline"], "lift": mined["lift"][:8], "frequent": mined["frequent"][:8]}}


# ---------------------------------------------------------------- state, apply and undo
def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)


def popup_wanted(state_path: Path, config: dict, advice: dict, now: datetime | None = None) -> bool:
    """Whether the workbench should show the pop-up now: advice exists, the setting allows it and it was not snoozed."""
    if not advice.get("level3") or not (advice.get("suggestions") or advice.get("expand_wanted")):
        return False
    if (config.get("automation") or {}).get("keyword_advice_popup") is False:
        return False
    until = _read(state_path).get("snoozed_until")
    return not (until and until > (now or datetime.now()).isoformat(timespec="seconds"))


def snooze(state_path: Path, hours: int = SNOOZE_HOURS, now: datetime | None = None) -> str:
    with _LOCK:
        state = _read(state_path)
        until = ((now or datetime.now()) + timedelta(hours=max(1, int(hours)))).isoformat(timespec="seconds")
        state["snoozed_until"] = until
        _write(state_path, state)
    return until


def apply(conn: sqlite3.Connection, config: dict, state_path: Path, picks: list[dict], today: date | None = None) -> dict:
    """Apply suggestions: pause keywords for a week, add keywords to the search list. Returns the changes and the new config.

    The caller saves ``config``. What was done is kept so that ``undo`` can take it back.
    """
    ks.ensure_tables(conn)
    today = today or date.today()
    paused_until = (today + timedelta(days=PAUSE_DAYS)).isoformat()
    words = _words(config)
    changes: list[dict] = []
    for pick in picks:
        kind, word = str(pick.get("kind") or ""), str(pick.get("keyword") or "").strip()
        if not word:
            continue
        if kind == "pause" and word in words:
            row = conn.execute("SELECT paused_until FROM keyword_tiers WHERE keyword = ?", (word,)).fetchone()
            conn.execute(
                "INSERT INTO keyword_tiers (keyword, tier, paused_until, reason, updated_at) VALUES (?, 'expand', ?, '按建议暂停', CURRENT_TIMESTAMP) "
                "ON CONFLICT(keyword) DO UPDATE SET paused_until=excluded.paused_until, reason=excluded.reason, updated_at=CURRENT_TIMESTAMP",
                (word, paused_until),
            )
            conn.execute("INSERT INTO keyword_tier_log (keyword, from_tier, to_tier, reason) VALUES (?, ?, 'paused', ?)",
                         (word, "expand", "按建议暂停 7 天"))
            changes.append({"kind": "pause", "keyword": word, "before": row["paused_until"] if row else None})
        elif kind == "add" and word.lower() not in [w.lower() for w in words]:
            words.append(word)
            changes.append({"kind": "add", "keyword": word})
    for change in changes:
        if change["kind"] == "add":                                      # a new word starts in the explore tier: a small share of the reads
            conn.execute(
                "INSERT INTO keyword_tiers (keyword, tier, pinned, reason, updated_at) VALUES (?, 'explore', 0, '按建议试用', CURRENT_TIMESTAMP) "
                "ON CONFLICT(keyword) DO NOTHING",
                (change["keyword"],),
            )
    conn.commit()
    added = [change["keyword"] for change in changes if change["kind"] == "add"]
    config = _with_words(config, lambda current: current + [word for word in added if word not in current])
    with _LOCK:
        state = _read(state_path)
        state["last_applied"] = {"time": datetime.now().isoformat(timespec="seconds"), "changes": changes}
        _write(state_path, state)
    return {"changes": changes, "config": config}


def undo(conn: sqlite3.Connection, config: dict, state_path: Path) -> dict:
    """Take back the last ``apply``. Returns the changes undone and the new config (the caller saves it)."""
    with _LOCK:
        state = _read(state_path)
        last = state.get("last_applied") or {}
        changes = list(last.get("changes") or [])
        state.pop("last_applied", None)
        _write(state_path, state)
    added = {change["keyword"] for change in changes if change["kind"] == "add"}
    for change in changes:
        if change["kind"] == "pause":
            conn.execute("UPDATE keyword_tiers SET paused_until = ?, reason = '撤销暂停' WHERE keyword = ?", (change.get("before"), change["keyword"]))
            conn.execute("INSERT INTO keyword_tier_log (keyword, from_tier, to_tier, reason) VALUES (?, 'paused', 'expand', '撤销暂停')", (change["keyword"],))
    conn.commit()
    return {"changes": changes, "config": _with_words(config, lambda current: [word for word in current if word not in added])}


def last_applied(state_path: Path) -> dict | None:
    return _read(state_path).get("last_applied") or None
