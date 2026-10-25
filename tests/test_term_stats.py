import tempfile
from datetime import date, timedelta
from pathlib import Path

from bosshunter import term_stats as ts
from bosshunter.db import get_db, insert_job, update_job_status


def test_title_words_are_english_words_and_chinese_runs_without_jargon():
    terms = ts.title_terms("人工智能应用工程师（上海）| AIGC Prompt")
    assert {"人工智能", "aigc", "prompt", "人工智能应用"} <= terms                        # the part before the jargon "工程师" is kept whole
    assert "工程师" not in terms and "上海" not in terms and "实习" not in ts.title_terms("实习")
    assert not any("工程师" in term for term in ts.title_terms("人工智能工程师"))          # no gram runs into the jargon
    assert ts.title_terms("") == set() and "and" not in ts.title_terms("AI and ML")


def test_an_outcome_is_counted_once_per_posting_and_the_counts_move_with_the_day():
    with tempfile.TemporaryDirectory() as tmp:
        db = get_db(Path(tmp) / "t.db")
        try:
            today = date(2026, 10, 25)
            ts.note_seen(db, "AIGC应用开发", today)
            ts.note_outcome(db, "j1", "AIGC应用开发", "用 LangChain 和 MCP", True, today)
            ts.note_outcome(db, "j1", "AIGC应用开发", "用 LangChain 和 MCP", True, today)       # the same job again: ignored
            row = {r["term"]: (r["seen"], r["scored"], r["passed"]) for r in db.execute("SELECT * FROM term_daily WHERE day = '2026-10-25'")}
            assert row["aigc"] == (1, 1, 1) and row["langchain"] == (0, 1, 1) and row["*"] == (0, 1, 1)
            ts.prune(db, today + timedelta(days=60))
            assert db.execute("SELECT COUNT(*) FROM term_daily").fetchone()[0] == 0           # older than the window it is gone
        finally:
            db.close()


def _fill(db, title, passed_count, failed_count, day):
    for number in range(passed_count + failed_count):
        job_id = f"{title}-{number}"
        ts.note_seen(db, title, day)
        ts.note_outcome(db, job_id, title, "", number < passed_count, day)


def test_mining_finds_words_that_pass_more_often_and_no_keyword_covers_yet():
    with tempfile.TemporaryDirectory() as tmp:
        db = get_db(Path(tmp) / "t.db")
        try:
            day = date.today()
            _fill(db, "人工智能工程师", 9, 3, day)                  # 75 % : well above the average
            _fill(db, "销售运营专员", 0, 30, day)                    # drags the average down
            _fill(db, "Agent开发工程师", 8, 4, day)                 # also good, but a keyword covers it
            result = ts.mine(db, ["Agent开发"], today=day)
            words = [item["term"] for item in result["lift"]]
            assert "人工智能" in words
            assert not any("agent" in word.lower() for word in words)                        # covered by the keyword
            assert "工智能" not in words                                                       # explained by the longer word
            found = next(item for item in result["lift"] if item["term"] == "人工智能")
            assert found["scored"] == 12 and found["passed"] == 9 and found["lift"] > 1.3
            assert 0 < result["baseline"] < 0.5
        finally:
            db.close()


def test_a_word_with_too_few_postings_or_an_ordinary_pass_rate_is_not_suggested_and_frequent_unscored_words_are_listed():
    with tempfile.TemporaryDirectory() as tmp:
        db = get_db(Path(tmp) / "t.db")
        try:
            day = date.today()
            _fill(db, "数据治理专员", 3, 1, day)                      # too few scored
            _fill(db, "测试自动化", 5, 7, day)                        # about average
            _fill(db, "运营后台", 6, 20, day)
            for _ in range(25):
                ts.note_seen(db, "多模态算法", day)                    # common on the market, never scored
            result = ts.mine(db, [], today=day)
            assert not any(item["term"] == "数据治理" for item in result["lift"])
            assert [item["term"] for item in result["frequent"]][:1] == ["多模态算法"] or "多模态" in [item["term"] for item in result["frequent"]]
        finally:
            db.close()


def test_the_counters_start_from_the_postings_already_in_the_database():
    with tempfile.TemporaryDirectory() as tmp:
        db = get_db(Path(tmp) / "t.db")
        try:
            for number, status in enumerate(["ready", "ready", "filtered"]):
                insert_job(db, {"id": f"j{number}", "title": "AIGC工程", "company": f"c{number}", "salary": "", "city": "上海", "experience": "", "jd": "x" * 60,
                                "hr_name": "", "hr_title": "", "url": "", "hr_active": ""})
                update_job_status(db, f"j{number}", status)
            assert ts.backfill(db) == 3
            row = db.execute("SELECT seen, scored, passed FROM term_daily WHERE term = 'aigc'").fetchone()
            assert tuple(row) == (3, 3, 2)
            assert ts.backfill(db) == 0                                                      # only once
        finally:
            db.close()
