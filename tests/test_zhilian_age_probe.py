import json
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import TestCase

from bosshunter import zhilian_age_probe as probe
from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.platforms.zhilian import ZhilianBrowser, ZhilianCollector
from bosshunter.db import get_db


class AgeProbeStatsTests(TestCase):
    def test_the_list_date_is_trusted_only_after_enough_agreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "t.db")
            try:
                for _ in range(19):
                    probe.record(db, True, True)
                self.assertFalse(probe.stats(db)["trusted"])                         # too few comparisons
                probe.record(db, True, True)
                self.assertTrue(probe.stats(db)["trusted"])
                for _ in range(3):
                    probe.record(db, True, False)                                     # refreshed postings the list would have dropped
                state = probe.stats(db)
                self.assertEqual((state["rejected"], state["wrong"]), (23, 3))
                self.assertFalse(state["trusted"])                                    # 13 % wrong: not trusted
                for _ in range(80):
                    probe.record(db, False, False)                                    # comparisons where the list said "fine" do not count
                self.assertFalse(probe.stats(db)["trusted"])
            finally:
                db.close()


class ListStageAgeTests(TestCase):
    OLD = (date.today() - timedelta(days=60)).isoformat() + " 10:00:00"
    NEW = date.today().isoformat() + " 10:00:00"

    @staticmethod
    def _item(number, publish):
        return {"number": number, "name": "Agent工程师", "companyName": f"公司{number}", "salary60": "1.5-2万", "workCity": "上海", "cityDistrict": "浦东",
                "streetName": "金桥", "education": "本科", "workingExp": "经验不限", "publishTime": publish, "positionURL": f"http://jobs.zhaopin.com/{number}.htm"}

    def _run(self, db, items, details):
        opened, saved, events = [], [], []

        def evaluate(_target, script):
            if "__zpHooked" in script:
                return "hooked"
            if "submitted_by" in script:
                return json.dumps({"ok": True, "value": "x", "submitted_by": "button"})
            if "window.__zpReq || null" in script:
                return json.dumps({"u": "https://fe-api.example/search/positions", "h": {}, "body": "{}"})
            if "Object.assign(JSON.parse(req.body)" in script:
                page = json.loads(re.search(r"Object\.assign\(JSON\.parse\(req\.body\), (\{.*?\})\);", script).group(1))["pageIndex"]
                return json.dumps({"status": 200, "body": json.dumps({"data": {"list": items if page == 1 else [], "count": 40, "isEndPage": 1}})})
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
            PlatformCollectionRequest("zhilian", ["Agent工程师"], ["上海"], {"上海": "538"}, max_pages=3), hooks)
        return [url for url in opened if "jobs.zhaopin.com" in url], saved, events

    @staticmethod
    def _detail(days_ago):
        return {"status": "ready", "title": "Agent工程师", "company": "公司", "jd": "负责 Agent 开发", "updated": (date.today() - timedelta(days=days_ago)).isoformat()}

    def test_while_untrusted_every_page_is_opened_and_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "t.db")
            try:
                opened, saved, _ = self._run(db, [self._item("J1", self.OLD), self._item("J2", self.NEW)], [self._detail(60), self._detail(0)])
                self.assertEqual(len(opened), 2)                                       # nothing is skipped before the list date has proved itself
                self.assertEqual([c.source_job_id for c in saved], ["J2"])             # the old one was rejected on its page, as before
                self.assertEqual(probe.stats(db)["rejected"], 1)                      # and the pair was kept
            finally:
                db.close()

    def test_once_trusted_an_old_list_date_is_rejected_without_opening_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "t.db")
            try:
                for _ in range(30):
                    probe.record(db, True, True)
                opened, saved, events = self._run(db, [self._item("J1", self.OLD), self._item("J2", self.NEW)], [self._detail(0)])
                self.assertEqual(opened, ["http://jobs.zhaopin.com/J2.htm"])           # J1 never got a page
                self.assertEqual([c.source_job_id for c in saved], ["J2"])
                self.assertTrue(any("列表预筛" in str(e.get("message")) and "天前更新" in str(e.get("message")) for e in events if e.get("increment_filtered")))
            finally:
                db.close()

    def test_a_refreshed_posting_proves_the_list_date_wrong_and_switches_the_shortcut_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "t.db")
            try:
                for _ in range(20):
                    probe.record(db, True, True)
                for _ in range(2):
                    probe.record(db, True, False)
                self.assertFalse(probe.stats(db)["trusted"])                          # 2 of 22 were wrong: 9 %
                opened, saved, _ = self._run(db, [self._item("J1", self.OLD)], [self._detail(1)])
                self.assertEqual(len(opened), 1)                                       # opened again, and it was fine: saved
                self.assertEqual([c.source_job_id for c in saved], ["J1"])
            finally:
                db.close()

    def test_one_in_ten_early_rejections_is_opened_to_keep_checking_the_list_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "t.db")
            try:
                for _ in range(30):
                    probe.record(db, True, True)
                items = [self._item(f"J{n}", self.OLD) for n in range(1, 21)]
                opened, saved, _ = self._run(db, items, [self._detail(60), self._detail(60)])
                self.assertEqual(len(opened), 2)                                       # 20 old-looking postings: 18 rejected on the list, 2 checked
                self.assertEqual(saved, [])
                self.assertEqual(probe.stats(db)["rejected"], 32)                      # the two checks were added to the comparisons
            finally:
                db.close()
