from bosshunter.db import add_history, get_db, get_sent_by_platform, insert_job, update_job_status


def _job(db, job_id, platform, status, action=None, days_ago=0):
    insert_job(db, {"id": job_id, "title": "岗位", "company": f"公司{job_id}", "salary": "", "city": "上海", "experience": "", "jd": "x" * 60,
                    "hr_name": "", "hr_title": "", "url": "", "hr_active": "", "source_platform": platform})
    update_job_status(db, job_id, status)
    if action:
        add_history(db, job_id, action, "")
        db.execute("UPDATE history SET created_at = datetime('now', ?) WHERE job_id = ?", (f"-{days_ago} day", job_id))
        db.commit()


def test_delivered_jobs_are_counted_per_platform_for_today_and_for_all_time(tmp_path):
    db = get_db(tmp_path / "t.db")
    try:
        _job(db, "b1", "boss", "sent", "sent")
        _job(db, "b2", "boss", "sent", "sent")
        _job(db, "b3", "boss", "sent", "sent", days_ago=3)             # sent earlier: all-time only
        _job(db, "l1", "liepin", "sent", "manual_sent")                 # marked as sent by hand today
        _job(db, "z1", "zhilian", "ready")                              # not delivered
        _job(db, "j1", "51job", "approved", "approved")                 # approved is not delivered
        result = get_sent_by_platform(db)
        assert result["today"] == {"boss": 2, "liepin": 1}
        assert result["total"] == {"boss": 3, "liepin": 1}
    finally:
        db.close()
