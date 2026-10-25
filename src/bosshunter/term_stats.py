"""Which words in job titles go with postings that fit the user? Counted as the postings come in, never recomputed from scratch.

The search keywords can only ever search for what they contain. To find directions that are missing ("人工智能" where the keywords
say "AI", "AIGC", "架构师") the program has to learn from the postings themselves. Two counters are kept per word and day:

* ``seen``: postings whose title had the word, counted when a posting is listed for the first time (cheap, at collection time; it
  tells how common the word is on the market, also among postings that were turned away before scoring);
* ``scored`` / ``passed``: postings whose title (or, for English skill words, whose JD) had the word, counted when the AI scoring
  decided about the posting (the only moment the outcome is known).

Counting is incremental (one small update per posting), the day buckets give a moving window (old days simply drop out) and a mining
query over a couple of thousand rows is instant, so there is no batch job to wait for and nothing to refresh by hand.
"""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import date, timedelta

WINDOW_DAYS = 14
KEEP_DAYS = 45
TOTAL = "*"                                           # per-day totals of scored / passed postings (the baseline)
MIN_SCORED = 8
MIN_LIFT = 1.3
MIN_SEEN_FREQUENT = 20

# Words that say nothing about the direction of a job: levels, kinds of work, places, hiring jargon.
STOP_WORDS = {
    "工程师", "开发", "经理", "专员", "主管", "助理", "实习", "实习生", "校招", "应届", "应届生", "初级", "中级", "高级", "资深", "专家", "负责人",
    "上海", "浦东", "徐汇", "闵行", "杨浦", "松江", "嘉定", "宝山", "青浦", "普陀", "静安", "黄浦", "长宁", "虹口", "奉贤", "金山", "崇明", "临港",
    "招聘", "岗位", "公司", "方向", "团队", "部门", "项目", "人员", "技术", "研发", "管理", "助理", "岗", "届", "全职", "兼职", "急聘", "直招",
    "校园", "毕业", "毕业生", "社招", "五险一金", "双休", "可实习", "海量", "名额", "诚聘", "热招",
}
STOP_ASCII = {"and", "the", "for", "with", "pm", "hr", "it", "jd", "hc", "ok", "or", "of", "to", "in", "on", "at", "co", "ltd", "inc",
              "api", "http", "https", "json", "xml", "web", "app", "sdk"}
JD = "jd:"                                            # prefix of a word counted from a job description (a different thing from a title word)
_STOP_LONGEST_FIRST = sorted(STOP_WORDS, key=len, reverse=True)
_ASCII = re.compile(r"[A-Za-z][A-Za-z0-9+#.]{1,}")
_CJK = re.compile(r"[一-鿿]+")
_NOISE = re.compile(r"[（(\[【].*?[）)\]】]")


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS term_daily ("
        "term TEXT NOT NULL, day TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0, scored INTEGER NOT NULL DEFAULT 0, "
        "passed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (term, day))"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS term_scored_jobs (job_id TEXT PRIMARY KEY)")


def title_terms(title: str) -> set[str]:
    """The words of a title worth counting: English words and Chinese runs of 2-6 characters that are not jargon."""
    cleaned = _NOISE.sub(" ", title or "")
    for word in _STOP_LONGEST_FIRST:                      # jargon splits a title into the parts that carry the direction
        cleaned = cleaned.replace(word, " ")
    terms: set[str] = set()
    for word in _ASCII.findall(cleaned):
        lowered = word.lower().strip(".")
        if len(lowered) >= 2 and lowered not in STOP_ASCII and not lowered.isdigit():
            terms.add(lowered)
    for run in _CJK.findall(cleaned):
        for size in range(2, 7):
            for start in range(0, len(run) - size + 1):
                gram = run[start:start + size]
                if gram not in STOP_WORDS and not _is_jargon(gram):
                    terms.add(gram)
    return terms


def _is_jargon(gram: str) -> bool:
    """A gram made only of stop words (or one that starts or ends with a generic character) means nothing alone."""
    remainder = gram
    for word in sorted(STOP_WORDS, key=len, reverse=True):
        remainder = remainder.replace(word, "")
    return not remainder or gram[0] in "的和与及或为" or gram[-1] in "的和与及或为"


def jd_terms(jd: str, limit: int = 60) -> set[str]:
    """English skill words of a job description (MCP, LangChain, FastAPI ...): Chinese JD text is too free-form to count."""
    found = {word.lower().strip(".") for word in _ASCII.findall(jd or "") if len(word) >= 3}
    return {word for word in found if word not in STOP_ASCII and not word.isdigit()} if len(found) <= limit * 4 else set(sorted(found)[:limit])


def _bump(conn: sqlite3.Connection, terms, day: str, seen: int = 0, scored: int = 0, passed: int = 0) -> None:
    conn.executemany(
        "INSERT INTO term_daily (term, day, seen, scored, passed) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(term, day) DO UPDATE SET seen = seen + excluded.seen, scored = scored + excluded.scored, passed = passed + excluded.passed",
        [(term, day, seen, scored, passed) for term in terms],
    )


def note_seen(conn: sqlite3.Connection, title: str, today: date | None = None) -> None:
    """A posting was listed for the first time."""
    _ensure(conn)
    _bump(conn, title_terms(title), (today or date.today()).isoformat(), seen=1)
    conn.commit()


def note_outcome(conn: sqlite3.Connection, job_id: str, title: str, jd: str, passed: bool, today: date | None = None) -> None:
    """The scoring decided about a posting (once per posting: a second call for the same job is ignored)."""
    _ensure(conn)
    if conn.execute("INSERT OR IGNORE INTO term_scored_jobs (job_id) VALUES (?)", (str(job_id),)).rowcount == 0:
        return
    day = (today or date.today()).isoformat()
    terms = title_terms(title) | {JD + word for word in jd_terms(jd)}
    _bump(conn, terms, day, scored=1, passed=int(passed))
    _bump(conn, [TOTAL], day, scored=1, passed=int(passed))
    conn.commit()


def prune(conn: sqlite3.Connection, today: date | None = None) -> None:
    _ensure(conn)
    conn.execute("DELETE FROM term_daily WHERE day < ?", (((today or date.today()) - timedelta(days=KEEP_DAYS)).isoformat(),))
    conn.commit()


def backfill(conn: sqlite3.Connection) -> int:
    """Count the postings that are already in the database (once, when there are no counters yet)."""
    _ensure(conn)
    if conn.execute("SELECT 1 FROM term_daily LIMIT 1").fetchone():
        return 0
    passed = ("ready", "approved", "sent", "replied", "resume_sent", "needs_resume", "follow_up_sent", "error")
    scored_status = passed + ("filtered",)
    count = 0
    for row in conn.execute("SELECT id, title, jd, status, date(created_at, 'localtime') AS day, score_reason FROM jobs WHERE deleted_at IS NULL").fetchall():
        day = row["day"] or date.today().isoformat()
        _bump(conn, title_terms(row["title"]), day, seen=1)
        prefiltered = str(row["score_reason"] or "").startswith(("预筛不通过", "AI评分失败", "AI 评分失败", "评分失败"))
        if row["status"] in scored_status and not prefiltered:
            ok = row["status"] in passed
            conn.execute("INSERT OR IGNORE INTO term_scored_jobs (job_id) VALUES (?)", (str(row["id"]),))
            _bump(conn, title_terms(row["title"]) | {JD + word for word in jd_terms(row["jd"])}, day, scored=1, passed=int(ok))
            _bump(conn, [TOTAL], day, scored=1, passed=int(ok))
        count += 1
    conn.commit()
    return count


def _wilson_lower(passed: int, total: int, z: float = 1.64) -> float:
    if total <= 0:
        return 0.0
    p = passed / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (centre - margin) / denominator


def _covered(term: str, keywords: list[str]) -> bool:
    low = term.lower()
    return any(low in word or word in low for word in (k.lower() for k in keywords if k))


def mine(conn: sqlite3.Connection, keywords: list[str], days: int = WINDOW_DAYS, today: date | None = None) -> dict:
    """``{"baseline", "lift": [...], "frequent": [...]}`` over the last ``days`` days.

    ``lift``: words of scored postings that pass clearly more often than the average, which no keyword covers yet.
    ``frequent``: words that are common among the postings listed but were hardly ever scored (the relevance pre-filter turns those
    postings away), which no keyword covers: the model is asked about them, the numbers cannot say.
    """
    from bosshunter.ai import keyword_lint

    _ensure(conn)
    backfill(conn)
    since = ((today or date.today()) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT term, SUM(seen) AS seen, SUM(scored) AS scored, SUM(passed) AS passed FROM term_daily WHERE day >= ? GROUP BY term", (since,)
    ).fetchall()
    stats = {row["term"]: (int(row["seen"] or 0), int(row["scored"] or 0), int(row["passed"] or 0)) for row in rows}
    total_scored, total_passed = stats.get(TOTAL, (0, 0, 0))[1:]
    baseline = total_passed / total_scored if total_scored else 0.0
    candidates = [
        (term, seen, scored, passed) for term, (seen, scored, passed) in stats.items()
        if term != TOTAL and not _covered(term.removeprefix(JD), keywords) and keyword_lint.check(term.removeprefix(JD)) is None
    ]
    # A longer word that explains a shorter one's count makes the shorter one redundant ("工智能" inside "人工智能").
    by_text = {term: (seen, scored, passed) for term, seen, scored, passed in candidates}

    def explained(term: str, value: tuple[int, int, int]) -> bool:
        return any(other != term and term in other and other.startswith(JD) == term.startswith(JD)
                   and other_value[1] >= value[1] * 0.85 and other_value[0] >= value[0] * 0.85
                   for other, other_value in by_text.items() if len(other) > len(term))

    lift = []
    for term, seen, scored, passed in candidates:
        if scored < MIN_SCORED or baseline <= 0:
            continue
        rate = passed / scored
        if rate >= baseline * MIN_LIFT and _wilson_lower(passed, scored) >= baseline * 0.9 and not explained(term, (seen, scored, passed)):
            lift.append({"term": term.removeprefix(JD), "source": "jd" if term.startswith(JD) else "title", "seen": seen, "scored": scored,
                         "passed": passed, "rate": round(rate, 3), "lift": round(rate / baseline, 2)})
    lift.sort(key=lambda item: (-item["passed"] * item["lift"], item["term"]))
    frequent = [
        {"term": term, "source": "title", "seen": seen, "scored": scored, "passed": passed}
        for term, seen, scored, passed in candidates
        if not term.startswith(JD) and seen >= MIN_SEEN_FREQUENT and scored <= 3 and not explained(term, (seen, scored, passed))
    ]
    frequent.sort(key=lambda item: (-item["seen"], item["term"]))
    return {"baseline": round(baseline, 3), "lift": lift[:15], "frequent": frequent[:15]}
