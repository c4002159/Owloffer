import json
from datetime import date, timedelta

import pytest

from bosshunter import zombie
from bosshunter.company_suggestions import company_suggestions
from bosshunter.db import (
    get_db, insert_job_if_new, record_sighting, update_job_dual_score, update_job_score, update_job_status,
)

CONFIG = {"automation": {"zombie_days": 7}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr("bosshunter.db.DB_PATH", path)
    conn = get_db(path)
    yield conn
    conn.close()


def _job(db, job_id, hr="半年前活跃", days=7, status="ready", company=None):
    insert_job_if_new(db, {
        "id": job_id, "title": f"岗位{job_id}", "company": company or f"公司{job_id}", "jd": "x" * 200,
        "hr_active": hr, "url": f"https://www.zhipin.com/job_detail/{job_id}.html",
    })
    update_job_score(db, job_id, 80, "ok")
    update_job_dual_score(db, job_id, 80, 100, "d")
    update_job_status(db, job_id, status)
    for offset in range(days):
        record_sighting(db, job_id, date.today() - timedelta(days=offset))


def test_zombie_needs_long_listing_and_known_inactive_hr():
    job_inactive = {"hr_active": "本月活跃"}
    assert zombie.is_zombie(job_inactive, 7, 7)
    assert not zombie.is_zombie(job_inactive, 6, 7)
    assert not zombie.is_zombie({"hr_active": "在线"}, 30, 7)
    assert not zombie.is_zombie({"hr_active": "3日内活跃"}, 30, 7)
    assert not zombie.is_zombie({"hr_active": ""}, 30, 7)  # unknown activity is not proof


def test_zombie_days_setting_is_clamped():
    assert zombie.zombie_days({}) == 7
    assert zombie.zombie_days({"automation": {"zombie_days": 3}}) == 3
    assert zombie.zombie_days({"automation": {"zombie_days": 1}}) == 2
    assert zombie.zombie_days({"automation": {"zombie_days": "x"}}) == 7


def test_apply_zombie_filter_filters_ready_jobs_and_stamps_them(db):
    _job(db, "zombie")
    _job(db, "active", hr="在线")
    _job(db, "short", days=3)
    _job(db, "approved", status="approved")
    assert zombie.apply_zombie_filter(db, CONFIG) == 1
    rows = {r["id"]: (r["status"], r["zombie_at"] is not None) for r in db.execute("SELECT id, status, zombie_at FROM jobs")}
    assert rows == {
        "zombie": ("filtered", True),
        "active": ("ready", False),
        "short": ("ready", False),
        "approved": ("approved", True),  # stamped for the blacklist suggestion, status left to the user
    }
    assert zombie.apply_zombie_filter(db, CONFIG) == 0


def test_zombie_company_is_recommended_even_after_filtering(db):
    _job(db, "z1", company="僵尸科技")
    _job(db, "z2", company="僵尸科技")
    _job(db, "ok", hr="在线", company="好公司")
    zombie.apply_zombie_filter(db, CONFIG)
    suggestions = {s["company"]: s["reasons"] for s in company_suggestions(db)}
    assert suggestions["僵尸科技"][0] == "僵尸岗位×2"
    # An online HR with a long listing is flagged as long-running, never as a zombie.
    assert not any(reason.startswith("僵尸") for reason in suggestions.get("好公司", []))
    assert "僵尸科技" not in {s["company"] for s in company_suggestions(db, ["僵尸"])}


def test_refresh_updates_hr_activity_and_respects_risk(db, monkeypatch):
    _job(db, "a", hr="刚刚活跃", days=6)
    _job(db, "b", hr="刚刚活跃", days=6)
    pages = {"a": {"hr_active": "半年前活跃"}, "b": {"hr_active": ""}}
    opened, closed = [], []

    monkeypatch.setattr("bosshunter.browser.new_tab", lambda url, background=False: opened.append(url) or url)
    monkeypatch.setattr("bosshunter.browser.close_tab", lambda target: closed.append(target))
    monkeypatch.setattr("bosshunter.browser.wait_for_load", lambda target, timeout=10.0: True)

    def fake_eval(target, js):
        if "risk" in js and "captcha" in js:
            return json.dumps({})
        key = "a" if target.endswith("a.html") else "b"
        return json.dumps(pages[key])

    monkeypatch.setattr("bosshunter.browser.evaluate", fake_eval)
    monkeypatch.setattr(zombie.time, "sleep", lambda s: None)
    result = zombie.refresh_long_listed(CONFIG, lambda _m: None)
    assert result == {"checked": 2, "updated": 1, "stopped": ""}
    assert sorted(closed) == sorted(opened)
    row = db.execute("SELECT hr_active, hr_checked_at FROM jobs WHERE id = 'a'").fetchone()
    assert row["hr_active"] == "半年前活跃" and row["hr_checked_at"]
    assert db.execute("SELECT hr_active FROM jobs WHERE id = 'b'").fetchone()["hr_active"] == "刚刚活跃"
    # Checked recently: not opened again.
    assert zombie.refresh_long_listed(CONFIG, lambda _m: None)["checked"] == 0
    # After the refresh job "a" is a long-listed, inactive-HR zombie.
    assert zombie.apply_zombie_filter(db, {"automation": {"zombie_days": 6}}) == 1


def test_refresh_stops_on_risk_signal(db, monkeypatch):
    _job(db, "a", hr="刚刚活跃", days=6)
    monkeypatch.setattr("bosshunter.browser.new_tab", lambda url, background=False: url)
    monkeypatch.setattr("bosshunter.browser.close_tab", lambda target: None)
    monkeypatch.setattr("bosshunter.browser.wait_for_load", lambda target, timeout=10.0: True)
    monkeypatch.setattr("bosshunter.browser.evaluate", lambda target, js: json.dumps({"risk": "captcha"}))
    monkeypatch.setattr(zombie.time, "sleep", lambda s: None)
    result = zombie.refresh_long_listed(CONFIG, lambda _m: None)
    assert "captcha" in result["stopped"] and result["checked"] == 0
