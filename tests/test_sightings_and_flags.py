from datetime import date, timedelta

import pytest

from bosshunter import review_queue as rq
from bosshunter.ai.recruit_score import compute_recruit_score, streak_factor
from bosshunter.db import (
    find_job_id, get_db, insert_job_if_new, record_sighting, sighting_streak,
    update_job_dual_score, update_job_score, update_job_status,
)

TODAY = date(2026, 10, 20)


@pytest.fixture
def db(tmp_path):
    conn = get_db(tmp_path / "jobs.db")
    yield conn
    conn.close()


def _job(db, job_id, company="公司", title="Agent工程师", *, match=80, hr="在线", status="ready"):
    insert_job_if_new(db, {
        "id": job_id, "title": title, "company": company, "jd": "x" * 200,
        "hr_active": hr, "source_platform": "boss", "source_job_id": f"src-{job_id}",
    })
    update_job_status(db, job_id, status)
    update_job_score(db, job_id, match, "原因")
    update_job_dual_score(db, job_id, match, 100, "d")


def test_streak_counts_consecutive_days_ending_recently(db):
    _job(db, "a")
    for offset in (0, 1, 2):
        record_sighting(db, "a", TODAY - timedelta(days=offset))
    assert sighting_streak(db, "a", TODAY) == 3
    assert sighting_streak(db, "a", TODAY + timedelta(days=1)) == 3
    assert sighting_streak(db, "a", TODAY + timedelta(days=2)) == 0


def test_streak_breaks_on_a_gap(db):
    _job(db, "a")
    for offset in (0, 1, 3, 4):
        record_sighting(db, "a", TODAY - timedelta(days=offset))
    assert sighting_streak(db, "a", TODAY) == 2


def test_record_sighting_is_idempotent_per_day(db):
    _job(db, "a")
    record_sighting(db, "a", TODAY)
    record_sighting(db, "a", TODAY)
    assert db.execute("SELECT COUNT(*) FROM job_sightings").fetchone()[0] == 1


def test_find_job_id_uses_platform_identity(db):
    _job(db, "a")
    assert find_job_id(db, "boss", "src-a") == "a"
    assert find_job_id(db, "boss", "nope") is None


@pytest.mark.parametrize("streak,expected", [(0, 1.0), (2, 1.0), (3, 0.8), (4, 0.6), (5, 0.4), (9, 0.4)])
def test_streak_factor_drops_gradually(streak, expected):
    assert streak_factor(streak) == pytest.approx(expected)


def test_long_listing_lowers_recruit_score_only_through_hr_points():
    job = {"hr_active": "在线", "jd": "x" * 200, "outsourcing_level": "clean"}
    fresh, _ = compute_recruit_score(job, 0)
    old, detail = compute_recruit_score(job, 5)
    assert old == fresh - 24 and "连续在招5天" in detail


def test_long_listing_lowers_only_the_activity_score_never_the_match_score(db):
    _job(db, "a", match=80)
    assert rq.refresh_recruit_scores(db, {}) == 1  # first pass replaces the placeholder detail
    before = db.execute("SELECT score, recruit_score FROM jobs WHERE id='a'").fetchone()
    assert before["score"] == 80
    record_sighting(db, "a", date.today())
    record_sighting(db, "a", date.today() - timedelta(days=1))
    record_sighting(db, "a", date.today() - timedelta(days=2))
    assert rq.refresh_recruit_scores(db, {}) == 1
    after = db.execute("SELECT score, recruit_score, recruit_detail FROM jobs WHERE id='a'").fetchone()
    assert after["score"] == 80                      # match with the user's experience is untouched
    assert after["recruit_score"] < before["recruit_score"]
    assert "连续在招3天" in after["recruit_detail"]
    assert rq.refresh_recruit_scores(db, {}) == 0    # nothing changed since


def test_activity_gate_filters_low_activity_jobs_but_not_by_match(db):
    _job(db, "alive", match=60)
    _job(db, "dead", match=95)
    update_job_dual_score(db, "dead", 95, 30, "d")
    update_job_dual_score(db, "alive", 60, 90, "d")
    assert rq.apply_activity_gate(db, {}) == 0
    assert rq.apply_activity_gate(db, {"scoring": {"min_activity_score": 70}}) == 1
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"alive": "ready", "dead": "filtered"}


def test_match_gate_also_drops_jobs_below_the_review_line(db):
    _job(db, "low", match=55)
    _job(db, "ok", match=65)
    assert rq.apply_match_gate(db, {"automation": {"review_min_score": 60}}) == 1
    statuses = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs")}
    assert statuses == {"low": "filtered", "ok": "ready"}


def test_migration_restores_pure_match_scores_once(db):
    from bosshunter.db import _migrate_pure_match_scores

    _job(db, "blended", match=80)
    update_job_score(db, "blended", 90, "综合90=匹配80×50%+招聘真实度100×50%（HR活跃40/40）；原始理由")
    db.execute("DELETE FROM schema_flags WHERE name = 'pure_match_scores'")
    db.commit()
    _migrate_pure_match_scores(db)
    row = db.execute("SELECT score, score_reason FROM jobs WHERE id='blended'").fetchone()
    assert row["score"] == 80 and row["score_reason"] == "原始理由"
    # Runs only once: later manual score changes are left alone.
    update_job_score(db, "blended", 77, "手动调整")
    _migrate_pure_match_scores(db)
    assert db.execute("SELECT score FROM jobs WHERE id='blended'").fetchone()["score"] == 77


def test_repeat_flags_mark_busy_companies_and_duplicate_postings(db):
    for i in range(3):
        _job(db, f"c{i}", company="多岗公司", title=f"岗位{i}")
    _job(db, "d1", company="重复公司", title="Agent 工程师")
    _job(db, "d2", company="重复公司", title="agent工程师")
    _job(db, "solo", company="单岗公司")
    for offset in range(3):
        record_sighting(db, "solo", date.today() - timedelta(days=offset))
    jobs = [dict(r) for r in db.execute("SELECT * FROM jobs")]
    flags = rq.repeat_flags(db, jobs)
    assert "同公司3岗" in flags["c0"]
    assert flags["d1"] == ["重复发布×2"] and flags["d2"] == ["重复发布×2"]
    assert flags["solo"] == ["连续在招3天"]
