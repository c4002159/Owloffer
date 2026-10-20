"""Keyword budgets and tiers.

Every keyword has a tier (core > expand > explore). A run covers all keywords: the first pass gives each one
a small slice of its tier's budget, later passes spend what is left on the best performers. Tiers move with
the results the keywords actually bring in (promotion, demotion, a 7-day pause for dead explore words).
"""

import math
import sqlite3
from datetime import date, datetime, timedelta

from bosshunter.ai.prefilter import hr_active_level

TIERS = ("core", "expand", "explore")
TIER_SHARE = {"core": 0.5, "expand": 0.3, "explore": 0.1}      # the last 10% is kept for the best performers
MIN_SLICE = 4                # first-pass minimum per keyword
MIN_SAMPLES = 10             # jobs needed before a keyword is scored
WINDOW_DAYS = 14
PROMOTE_STREAK = 2
DEMOTE_STREAK = 3
MIN_CORE = 4
PAUSE_SAMPLES = 15
PAUSE_DAYS = 7
PASSED = ("ready", "approved", "sent", "replied", "resume_sent", "needs_resume", "follow_up_sent")
REPLIED = ("replied", "resume_sent", "needs_resume")
SENT = ("sent", "replied", "resume_sent", "needs_resume", "follow_up_sent")


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS keyword_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, keyword TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'fulltime', run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            seen INTEGER NOT NULL DEFAULT 0, new INTEGER NOT NULL DEFAULT 0, filtered INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS keyword_tiers (
            keyword TEXT PRIMARY KEY, tier TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
            paused_until TEXT, up_streak INTEGER NOT NULL DEFAULT 0, down_streak INTEGER NOT NULL DEFAULT 0,
            streak_day TEXT, reason TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS keyword_tier_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, from_tier TEXT, to_tier TEXT,
            reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def record_run(conn: sqlite3.Connection, platform: str, keyword: str, kind: str, *, seen: int, new: int, filtered: int) -> None:
    ensure_tables(conn)
    conn.execute(
        "INSERT INTO keyword_runs (platform, keyword, kind, seen, new, filtered) VALUES (?, ?, ?, ?, ?, ?)",
        (platform, keyword, kind, int(seen), int(new), int(filtered)),
    )
    conn.commit()


# ---------------------------------------------------------------- scoring
def keyword_scores(conn: sqlite3.Connection, days: int = WINDOW_DAYS) -> dict[str, dict]:
    """Per keyword over the last ``days`` days: jobs, pass rate, HR-active share, reply rate, novelty and a 0-1 score.

    ``score`` is None below MIN_SAMPLES jobs. Missing signals count as neutral (0.5), not as bad.
    """
    ensure_tables(conn)
    since = f"-{int(days)} day"
    rows = conn.execute(
        """
        SELECT source_keyword AS keyword, status, hr_active FROM jobs
        WHERE COALESCE(source_keyword, '') != '' AND created_at >= datetime('now', ?)
        """,
        (since,),
    ).fetchall()
    stats: dict[str, dict] = {}
    for row in rows:
        item = stats.setdefault(row["keyword"], {"n": 0, "passed": 0, "sent": 0, "replied": 0, "hr_known": 0, "hr_active": 0})
        item["n"] += 1
        item["passed"] += row["status"] in PASSED
        item["sent"] += row["status"] in SENT
        item["replied"] += row["status"] in REPLIED
        level = hr_active_level(row["hr_active"] or "")
        if level is not None:
            item["hr_known"] += 1
            item["hr_active"] += level >= 2
    novelty = {
        row["keyword"]: row["new"] / row["seen"]
        for row in conn.execute(
            "SELECT keyword, SUM(seen) AS seen, SUM(new) AS new FROM keyword_runs "
            "WHERE seen > 0 AND run_at >= datetime('now', ?) GROUP BY keyword",
            (since,),
        ).fetchall()
    }
    result = {}
    for keyword, item in stats.items():
        n = item["n"]
        pass_rate = item["passed"] / n
        active = item["hr_active"] / item["hr_known"] if item["hr_known"] >= 3 else 0.5
        reply = item["replied"] / item["sent"] if item["sent"] >= 5 else 0.5
        fresh = min(max(novelty.get(keyword, 0.5), 0.0), 1.0)
        score = round(0.4 * pass_rate + 0.25 * active + 0.2 * reply + 0.15 * fresh, 3) if n >= MIN_SAMPLES else None
        result[keyword] = {
            "n": n, "pass_rate": round(pass_rate, 3), "active_share": round(active, 3),
            "reply_rate": round(reply, 3), "novelty": round(fresh, 3), "score": score,
        }
    return result


# ---------------------------------------------------------------- tiers
def current_tiers(conn: sqlite3.Connection, words: list[str], plan: dict | None, today: date | None = None) -> dict:
    """``{"core": [...], "expand": [...], "explore": [...], "paused": [...]}`` for the given keywords.

    A keyword's tier comes from the stored override, else the AI plan, else ``expand``.
    """
    ensure_tables(conn)
    today = today or date.today()
    overrides = {row["keyword"]: row for row in conn.execute("SELECT * FROM keyword_tiers").fetchall()}
    plan_tier = {word: tier for tier in TIERS for word in (plan or {}).get(tier, [])}
    result: dict[str, list[str]] = {"core": [], "expand": [], "explore": [], "paused": []}
    for word in words:
        override = overrides.get(word)
        if override and override["paused_until"] and override["paused_until"] > today.isoformat():
            result["paused"].append(word)
            continue
        tier = (override["tier"] if override else None) or plan_tier.get(word) or "expand"
        result[tier if tier in TIERS else "expand"].append(word)
    return result


def adjust_tiers(conn: sqlite3.Connection, words: list[str], plan: dict | None, today: date | None = None) -> list[dict]:
    """Move keywords between tiers from their results; at most one step per keyword per day. Returns the moves."""
    ensure_tables(conn)
    today = today or date.today()
    day = today.isoformat()
    scores = keyword_scores(conn)
    tiers = current_tiers(conn, words, plan, today)
    tier_of = {word: tier for tier, group in tiers.items() if tier != "paused" for word in group}
    ranked = sorted((w for w in tier_of if scores.get(w, {}).get("score") is not None), key=lambda w: scores[w]["score"], reverse=True)
    top = set(ranked[: max(1, math.ceil(len(ranked) / 3))]) if ranked else set()
    bottom = set(ranked[-max(1, math.ceil(len(ranked) / 4)):]) if len(ranked) >= 4 else set()
    moves: list[dict] = []
    core_count = len(tiers["core"])

    def move(word: str, to_tier: str | None, reason: str, **fields) -> None:
        nonlocal core_count
        before = tier_of[word]
        row = conn.execute("SELECT * FROM keyword_tiers WHERE keyword = ?", (word,)).fetchone()
        values = {"tier": to_tier or before, "up_streak": 0, "down_streak": 0, "streak_day": day, "reason": reason,
                  "paused_until": row["paused_until"] if row else None, **fields}
        conn.execute(
            "INSERT INTO keyword_tiers (keyword, tier, pinned, paused_until, up_streak, down_streak, streak_day, reason, updated_at) "
            "VALUES (?, ?, COALESCE((SELECT pinned FROM keyword_tiers WHERE keyword = ?), 0), ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(keyword) DO UPDATE SET tier=excluded.tier, paused_until=excluded.paused_until, up_streak=excluded.up_streak, "
            "down_streak=excluded.down_streak, streak_day=excluded.streak_day, reason=excluded.reason, updated_at=CURRENT_TIMESTAMP",
            (word, values["tier"], word, values["paused_until"], values["up_streak"], values["down_streak"], day, reason),
        )
        shown = "paused" if fields.get("paused_until") else values["tier"]
        conn.execute("INSERT INTO keyword_tier_log (keyword, from_tier, to_tier, reason) VALUES (?, ?, ?, ?)",
                     (word, before, shown, reason))
        if to_tier == "core":
            core_count += 1
        if before == "core" and to_tier and to_tier != "core":
            core_count -= 1
        moves.append({"keyword": word, "from": before, "to": shown, "reason": reason})

    for word in tier_of:
        row = conn.execute("SELECT * FROM keyword_tiers WHERE keyword = ?", (word,)).fetchone()
        if row and (row["pinned"] or row["streak_day"] == day):
            continue
        info = scores.get(word)
        tier = tier_of[word]
        if info is None:
            continue
        if tier == "explore" and info["n"] >= PAUSE_SAMPLES and info["pass_rate"] == 0:
            move(word, None, f"{info['n']} 条里 0 通过，暂停 {PAUSE_DAYS} 天",
                 paused_until=(today + timedelta(days=PAUSE_DAYS)).isoformat())
            continue
        up = (row["up_streak"] if row else 0) + 1 if word in top else 0
        down = (row["down_streak"] if row else 0) + 1 if word in bottom else 0
        if up >= PROMOTE_STREAK and tier != "core":
            move(word, "core" if tier == "expand" else "expand", f"连续 {up} 次排在前三分之一（分数 {info['score']}）")
        elif down >= DEMOTE_STREAK and tier == "core" and core_count > MIN_CORE:
            move(word, "expand", f"连续 {down} 次排在后四分之一（分数 {info['score']}）")
        else:
            conn.execute(
                "INSERT INTO keyword_tiers (keyword, tier, up_streak, down_streak, streak_day) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(keyword) DO UPDATE SET up_streak=excluded.up_streak, down_streak=excluded.down_streak, streak_day=excluded.streak_day",
                (word, tier, up, down, day),
            )
    conn.commit()
    return moves


def set_pinned(conn: sqlite3.Connection, keyword: str, pinned: bool, tier: str | None = None) -> None:
    ensure_tables(conn)
    conn.execute(
        "INSERT INTO keyword_tiers (keyword, tier, pinned, reason) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(keyword) DO UPDATE SET pinned=excluded.pinned, tier=COALESCE(?, tier), reason=excluded.reason",
        (keyword, tier or "expand", int(pinned), "手动固定" if pinned else "取消固定", tier),
    )
    conn.commit()


# ---------------------------------------------------------------- budgets
def first_pass_caps(tiers: dict, raw_target: int) -> dict[str, int]:
    """Per-keyword cap for the coverage pass: the tier's budget split evenly, never below MIN_SLICE."""
    caps: dict[str, int] = {}
    ceiling: int | None = None          # a lower tier never gets more per keyword than the tier above it
    for tier in TIERS:
        words = tiers.get(tier) or []
        if not words:
            continue
        budget = TIER_SHARE[tier] * max(raw_target, 0)
        cap = max(MIN_SLICE, math.ceil(budget / len(words)))
        if ceiling is not None:
            cap = max(MIN_SLICE, min(cap, ceiling))
        ceiling = cap
        for word in words:
            caps[word] = cap
    return caps


def ordered_for_next_pass(tiers: dict, scores: dict[str, dict]) -> list[str]:
    """Later passes: best performers first (unscored keywords sit in the middle)."""
    words = [word for tier in TIERS for word in tiers.get(tier, [])]
    return sorted(words, key=lambda w: (scores.get(w, {}).get("score") if scores.get(w, {}).get("score") is not None else 0.5), reverse=True)


def platform_share(raw_target: int, platform_count: int) -> int:
    """Raw collection target per platform: everything for one platform, an even split for several."""
    return math.ceil(max(raw_target, 0) / max(platform_count, 1))


def strategy_report(conn: sqlite3.Connection, words: list[str], plan: dict | None, limit: int = 20) -> dict:
    """What the config page shows: tiers, per-keyword results and the latest moves."""
    tiers = current_tiers(conn, words, plan)
    scores = keyword_scores(conn)
    pinned = {row["keyword"] for row in conn.execute("SELECT keyword FROM keyword_tiers WHERE pinned = 1").fetchall()}
    paused = {row["keyword"]: row["paused_until"] for row in conn.execute("SELECT keyword, paused_until FROM keyword_tiers WHERE paused_until IS NOT NULL").fetchall()}
    log = [dict(row) for row in conn.execute("SELECT keyword, from_tier, to_tier, reason, created_at FROM keyword_tier_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    return {"tiers": tiers, "scores": scores, "pinned": sorted(pinned), "paused": paused, "log": log}
