"""Pick among several experience tags when a platform's filter takes only one (Liepin).

Every search picks one tag at random. At the start all tags are equally likely; afterwards the odds follow how good the
searches under each tag were: many new jobs among the ones listed, and many of those with an active HR. A tag never drops
below ``FLOOR`` of the odds, so a tag that had a bad stretch is still tried now and then.
"""

import random
import sqlite3

FLOOR = 0.10


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS experience_variant_stats ("
        "platform TEXT NOT NULL, variant TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0, new INTEGER NOT NULL DEFAULT 0, "
        "active INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (platform, variant))"
    )


def stats(conn: sqlite3.Connection, platform: str) -> dict[str, dict[str, int]]:
    _ensure(conn)
    rows = conn.execute("SELECT variant, seen, new, active FROM experience_variant_stats WHERE platform = ?", (platform,)).fetchall()
    return {str(row[0]): {"seen": int(row[1]), "new": int(row[2]), "active": int(row[3])} for row in rows}


def record(conn: sqlite3.Connection, platform: str, variant: str, *, seen: int, new: int, active: int) -> None:
    """Add one search's result: jobs listed, new jobs among them, and how many of the new ones had an active HR."""
    _ensure(conn)
    conn.execute(
        "INSERT INTO experience_variant_stats (platform, variant, seen, new, active) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(platform, variant) DO UPDATE SET seen = seen + excluded.seen, new = new + excluded.new, "
        "active = active + excluded.active",
        (platform, variant, max(seen, 0), max(new, 0), max(min(active, new), 0)),
    )
    conn.commit()


def quality(entry: dict[str, int] | None) -> float:
    """Share of new jobs among the listed ones times the share of active ones among the new; 0.25 prior (= no data)."""
    entry = entry or {"seen": 0, "new": 0, "active": 0}
    return (entry["new"] + 1) / (entry["seen"] + 2) * (entry["active"] + 1) / (entry["new"] + 2) * 2


def weights(conn: sqlite3.Connection, platform: str, variants: list[str]) -> dict[str, float]:
    """Odds per variant (they add up to 1): equal without data, then by quality, never below FLOOR."""
    known = stats(conn, platform)
    raw = {variant: quality(known.get(variant)) for variant in variants}
    total = sum(raw.values()) or 1.0
    share = {variant: value / total for variant, value in raw.items()}
    floor = min(FLOOR, 1.0 / len(variants))
    # Every variant gets the floor; the rest is shared out by quality (the odds still add up to 1).
    return {variant: floor + (1.0 - floor * len(variants)) * value for variant, value in share.items()}


def choose(conn: sqlite3.Connection, platform: str, variants: list[str], rng: random.Random | None = None) -> str:
    if len(variants) == 1:
        return variants[0]
    odds = weights(conn, platform, variants)
    return (rng or random).choices(list(odds), weights=list(odds.values()), k=1)[0]
