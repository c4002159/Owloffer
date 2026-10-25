import json
import re
import tempfile
from datetime import date
from pathlib import Path
from unittest import TestCase

from bosshunter import zhilian_contacted
from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.platforms.zhilian import JS_EXTRACT_DETAIL, ZhilianBrowser, ZhilianCollector
from bosshunter.db import get_db


class ContactedTableTests(TestCase):
    def test_a_posting_is_remembered_and_the_label_is_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "t.db")
            try:
                self.assertFalse(zhilian_contacted.known(db, "J1"))
                zhilian_contacted.remember(db, "J1")
                zhilian_contacted.remember(db, "J1")                                     # twice is fine
                self.assertTrue(zhilian_contacted.known(db, "J1") and not zhilian_contacted.known(db, "J2"))
            finally:
                db.close()
        self.assertTrue(zhilian_contacted.is_contacted_label("继续沟通"))
        self.assertTrue(zhilian_contacted.is_contacted_label("  继续沟通 "))
        self.assertFalse(zhilian_contacted.is_contacted_label("立即投递") or zhilian_contacted.is_contacted_label("") or zhilian_contacted.is_contacted_label(None))

    def test_the_detail_script_reads_the_same_button_the_delivery_looks_at(self):
        self.assertIn(".summary-planes__action button, button.job-detail-summary__apply", JS_EXTRACT_DETAIL)
        self.assertIn("applyLabel", JS_EXTRACT_DETAIL)


class ContactedPostingsAreNotQueuedTests(TestCase):
    @staticmethod
    def _item(number):
        return {"number": number, "name": "Agent工程师", "companyName": f"公司{number}", "salary60": "1.5-2万", "workCity": "上海", "cityDistrict": "浦东",
                "streetName": "金桥", "education": "本科", "workingExp": "经验不限", "publishTime": date.today().isoformat() + " 10:00:00",
                "positionURL": f"http://jobs.zhaopin.com/{number}.htm"}

    def _run(self, db, numbers, labels):
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():      # a keyword searched to its end is skipped
            if "combo" in name or "page_progress" in name or name == "collect_progress":
                db.execute(f"DELETE FROM {name}")
        db.commit()
        opened, saved, events = [], [], []
        details = [{"status": "ready", "title": "Agent工程师", "company": "公司", "jd": "负责 Agent 开发", "updated": date.today().isoformat(), "applyLabel": label} for label in labels]

        def evaluate(_target, script):
            if "__zpHooked" in script:
                return "hooked"
            if "submitted_by" in script:
                return json.dumps({"ok": True, "value": "x", "submitted_by": "button"})
            if "window.__zpReq || null" in script:
                return json.dumps({"u": "https://fe-api.example/search/positions", "h": {}, "body": "{}"})
            if "Object.assign(JSON.parse(req.body)" in script:
                page = json.loads(re.search(r"Object\.assign\(JSON\.parse\(req\.body\), (\{.*?\})\);", script).group(1))["pageIndex"]
                return json.dumps({"status": 200, "body": json.dumps({"data": {"list": [self._item(n) for n in numbers] if page == 1 else [], "count": 40, "isEndPage": 1}})})
            if "descriptionCard" in script:
                return json.dumps(details.pop(0))
            return "null"

        browser = ZhilianBrowser(
            new_tab=lambda url, **_k: opened.append(url) or f"tab{len(opened)}", close_tab=lambda _t: True, evaluate=evaluate,
            scroll=lambda *_a, **_k: True, wait_for_load=lambda *_a, **_k: True, navigate_action=lambda *_a: True)
        config = {"search": {"keywords": ["AI Agent开发"]}, "throttle": {"collect_windows": ["00:00-23:59"], "day_off_probability": 0}, "_experience_level": "fresh"}
        hooks = CollectorHooks(stop_event=None, on_list_candidate=lambda c: True, on_candidate=lambda c: saved.append(c) or True,
                               on_parse_failed=lambda r: events.append(r), on_event=lambda **kw: events.append(kw))
        ZhilianCollector(browser=browser, config=config, safety_conn=db, sleep=lambda _s: None).collect(
            PlatformCollectionRequest("zhilian", ["Agent工程师"], ["上海"], {"上海": "538"}, max_pages=2), hooks)
        return [u for u in opened if "jobs.zhaopin.com" in u], saved, events

    def test_a_posting_whose_button_says_continue_chat_is_not_saved_and_not_opened_again_next_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "t.db")
            try:
                opened, saved, events = self._run(db, ["J1", "J2"], ["继续沟通", "立即投递"])
                self.assertEqual([c.source_job_id for c in saved], ["J2"])             # J1 was never queued for delivery
                self.assertEqual(len(opened), 2)
                self.assertTrue(any("继续沟通" in str(e.get("message")) for e in events if e.get("increment_filtered")))
                opened, saved, events = self._run(db, ["J1", "J2"], ["立即投递"])
                self.assertEqual(opened, ["http://jobs.zhaopin.com/J2.htm"])           # J1 is remembered: no page is opened for it
                self.assertTrue(any("列表预筛：这个岗位之前已经投递或沟通过" in str(e.get("message")) for e in events))
            finally:
                db.close()

    def test_a_posting_that_can_still_be_applied_to_is_saved_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "t.db")
            try:
                _opened, saved, _events = self._run(db, ["J3"], ["立即投递"])
                self.assertEqual([c.source_job_id for c in saved], ["J3"])
                _opened, saved, _events = self._run(db, ["J4"], [""])                  # no button found (page changed): not treated as contacted
                self.assertEqual([c.source_job_id for c in saved], ["J4"])
            finally:
                db.close()
