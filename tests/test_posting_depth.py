import tempfile
from pathlib import Path

from bosshunter import posting_depth as pd
from bosshunter.db import get_db, insert_job, update_job_status


def _job(db, number, platform, status):
    insert_job(db, {"id": f"{platform}:{number}", "source_job_id": number, "source_platform": platform, "title": "岗位", "company": f"公司{number}", "salary": "",
                    "city": "上海", "experience": "", "jd": "x" * 60, "hr_name": "", "hr_title": "", "url": "", "hr_active": ""})
    update_job_status(db, f"{platform}:{number}", status)


def test_the_page_of_the_first_sighting_is_kept_and_the_report_sets_it_against_the_outcome():
    with tempfile.TemporaryDirectory() as tmp:
        db = get_db(Path(tmp) / "t.db")
        try:
            for number, page, status in (("a", 1, "ready"), ("b", 2, "filtered"), ("c", 2, "ready"), ("d", 15, "filtered"), ("e", 15, "filtered"), ("f", 25, "filtered")):
                _job(db, number, "boss", status)
                pd.record(db, "boss", number, "AI", page)
            pd.record(db, "boss", "a", "AI", 19)                                      # seen again later: the first page stays
            pd.record(db, "boss", "g", "AI", 5)                                       # seen but never saved (turned away): counts as seen only
            pd.record(db, "boss", "card:x:y", "AI", 3)                                # a list-card stub has no identity
            pd.record(db, "boss", "h", "AI", None)                                    # no page known: not recorded
            bands = {item["pages"]: item for item in pd.report(db, "boss")}
            assert bands["1-3"] == {"platform": "boss", "pages": "1-3", "seen": 3, "saved": 3, "passed": 2, "rate": 0.667}
            assert bands["4-10"]["seen"] == 1 and bands["4-10"]["saved"] == 0 and bands["4-10"]["rate"] is None
            assert bands["11-20"]["rate"] == 0.0 and bands["21+"]["seen"] == 1
            assert pd.report(db, "zhilian") == []
        finally:
            db.close()
