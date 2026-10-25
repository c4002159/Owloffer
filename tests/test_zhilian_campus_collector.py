import json
import re
from datetime import date, timedelta
from unittest import TestCase

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.platforms.zhilian import ZhilianBrowser
from bosshunter.collection.platforms.zhilian_campus import ZhilianCampusCollector, parse_item, template_from_capture


def _item(number, name="AI应用开发工程师", company="某公司", **extra):
    return {"number": number, "name": name, "companyName": company, "salary60": "7000-10000元", "workCity": "上海",
            "cityDistrict": "浦东", "streetName": "金桥", "education": "本科", "workingExp": "在校/应届",
            "publishTime": "2026-10-01 10:00:00", "companySize": "100-299人", "industryName": "软件", **extra}


def _config(keywords):
    # the test must not depend on the time of day or on the random day off
    return {"search": {"keywords": keywords}, "throttle": {"collect_windows": ["00:00-23:59"], "day_off_probability": 0}}


def _days_ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


class CampusCollectorTests(TestCase):
    def _browser(self, pages, details, requests):
        opened = []

        def evaluate(_target, script):
            if "__campusHook" in script:
                return "hooked"
            if "pagination__number" in script:
                return "pager"
            if "window.__campusReq || null" in script:
                return json.dumps({"u": "https://cgate.example/searchPositions", "h": {}, "body": "{}"})
            if "S_SOU_FULL_INDEX" in script:
                page = int(re.search(r"body\.pageIndex = (\d+)", script).group(1))
                keyword = re.search(r'body\.S_SOU_FULL_INDEX = "([^"]*)"', script).group(1)
                requests.append((keyword, page))
                items = pages.get(page, [])
                return json.dumps({"status": 200, "body": json.dumps({"data": {
                    "list": items, "count": 40, "isEndPage": 1 if page >= len(pages) else 0}})})
            if "职位描述" in script:
                return json.dumps(details.pop(0) if details else {"status": "selector_changed"})
            return "null"

        return ZhilianBrowser(
            new_tab=lambda url, **_kw: opened.append(url) or f"tab{len(opened)}", close_tab=lambda _t: True,
            evaluate=evaluate, scroll=lambda *_a, **_k: True, wait_for_load=lambda *_a, **_k: True,
            navigate_action=lambda *_a: True,
        ), opened

    def _hooks(self, listed, saved, events, company_check=None):
        return CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda c: listed.append(c) or True,
            on_candidate=lambda c: saved.append(c) or True,
            on_parse_failed=lambda r: events.append(("parse_failed", r)),
            on_event=lambda **kw: events.append(("event", kw)),
            on_list_company=company_check,
        )

    def test_items_are_read_from_the_list_requests_and_only_the_survivors_get_a_detail_page(self):
        requests, listed, saved, events = [], [], [], []
        pages = {1: [_item("CC1J1", company="可用公司"), _item("CC2J2", name="跟单员", company="乙公司")], 2: [_item("CC3J3", company="丙公司")]}
        details = [
            {"status": "ready", "updated": _days_ago(3), "jd": "负责 AI 应用开发"},
            {"status": "ready", "updated": _days_ago(3), "jd": "负责 AI 应用开发 2"},
        ]
        browser, opened = self._browser(pages, details, requests)
        collector = ZhilianCampusCollector(browser=browser, config=_config(["AI Agent开发"]), sleep=lambda _s: None)
        result = collector.collect(
            PlatformCollectionRequest("zhilian_campus", ["Agent开发"], ["上海"], {"上海": "538"}, max_pages=5),
            self._hooks(listed, saved, events))
        self.assertEqual(requests, [("Agent开发", 1), ("Agent开发", 2)])              # page 1, then 2; the last page ends it
        self.assertEqual([c.source_job_id for c in saved], ["CC1J1", "CC3J3"])        # 跟单员 was ruled out from the list
        detail_pages = [url for url in opened if "/job/" in url]
        self.assertEqual(detail_pages, ["https://xiaoyuan.zhaopin.com/job/CC1J1", "https://xiaoyuan.zhaopin.com/job/CC3J3"])
        self.assertEqual(result.reason_code, "search_exhausted")
        self.assertTrue(all(c.platform == "zhilian_campus" and c.recruitment_type == "campus" for c in saved))
        self.assertEqual(saved[0].jd, "负责 AI 应用开发")

    def test_a_stale_posting_is_dropped_after_its_detail_page_by_the_campus_rule(self):
        requests, listed, saved, events = [], [], [], []
        browser, _ = self._browser({1: [_item("CC1J1")]}, [{"status": "ready", "updated": _days_ago(45), "jd": "JD"}], requests)
        collector = ZhilianCampusCollector(browser=browser, config=_config(["AI应用开发"]), sleep=lambda _s: None)
        collector.collect(PlatformCollectionRequest("zhilian_campus", ["AI应用开发"], ["上海"], {"上海": "538"}, max_pages=1),
                          self._hooks(listed, saved, events))
        self.assertEqual(saved, [])
        reasons = [e[1].get("message", "") for e in events if e[0] == "event" and e[1].get("increment_filtered")]
        self.assertTrue(any("校招网申上限 30 天" in reason for reason in reasons))

    def test_a_company_that_is_already_known_is_skipped_before_its_page_is_opened(self):
        requests, listed, saved, events = [], [], [], []
        browser, opened = self._browser({1: [_item("CC1J1", company="已投过的公司")]}, [], requests)
        collector = ZhilianCampusCollector(browser=browser, config=_config(["AI应用开发"]), sleep=lambda _s: None)
        collector.collect(PlatformCollectionRequest("zhilian_campus", ["AI应用开发"], ["上海"], {"上海": "538"}, max_pages=1),
                          self._hooks(listed, saved, events, company_check=lambda company, city, title: "公司近期已投递过"))
        self.assertEqual([url for url in opened if "/job/" in url], [])
        self.assertEqual(listed, [])

    def test_no_list_request_means_a_structure_change_not_a_silent_empty_result(self):
        browser = ZhilianBrowser(
            new_tab=lambda *_a, **_k: "t", close_tab=lambda _t: True, evaluate=lambda _t, _s: "null",
            scroll=lambda *_a, **_k: True, wait_for_load=lambda *_a, **_k: True, navigate_action=lambda *_a: True)
        collector = ZhilianCampusCollector(browser=browser, config=_config([]), sleep=lambda _s: None)
        import bosshunter.collection.platforms.zhilian_campus as module
        original = module.TEMPLATE_WAIT_SECONDS
        module.TEMPLATE_WAIT_SECONDS = 0.2
        try:
            result = collector.collect(PlatformCollectionRequest("zhilian_campus", ["开发"], ["上海"], {"上海": "538"}, max_pages=1),
                                       self._hooks([], [], []))
        finally:
            module.TEMPLATE_WAIT_SECONDS = original
        self.assertEqual((result.status, result.reason_code), ("blocked", "selector_changed"))

    def test_parse_item_builds_a_campus_candidate(self):
        candidate = parse_item(_item("CC9J9", name="软件开发实习生"), "开发")
        self.assertEqual(candidate.source_job_id, "CC9J9")
        self.assertEqual(candidate.url, "https://xiaoyuan.zhaopin.com/job/CC9J9")
        self.assertEqual(candidate.city, "上海·浦东·金桥")
        self.assertEqual((candidate.education, candidate.experience), ("本科", "在校/应届"))
        self.assertIsNone(parse_item({"number": "", "name": "x"}, "k"))

    def test_a_request_seen_on_the_network_is_the_template_when_the_page_hides_it_from_hooks(self):
        scripts = []

        def evaluate(_target, script):
            scripts.append(script)
            if "__campusHook" in script:
                return "hooked"
            if "pagination__number" in script:
                return "pager"
            return "null"                                              # the hook never sees the page's request

        seen = {"url": "https://cgate.example/searchPositions?x=1", "method": "POST", "postData": '{"pageIndex":2}',
                "headers": {"x-zp-platform": "14", "user-agent": "x", "content-type": "application/json", "cookie": "secret"}}
        browser = ZhilianBrowser(
            new_tab=lambda *_a, **_k: "t", close_tab=lambda _t: True, evaluate=evaluate, scroll=lambda *_a, **_k: True,
            wait_for_load=lambda *_a, **_k: True, navigate_action=lambda *_a: True,
            capture_action=lambda _t, match: match == "searchPositions", captured_action=lambda _t: seen)
        collector = ZhilianCampusCollector(browser=browser, config=_config([]), sleep=lambda _s: None)
        self.assertTrue(collector._capture_template("t"))
        injected = next(script for script in scripts if script.startswith("window.__campusReq ="))
        self.assertIn("searchPositions?x=1", injected)
        self.assertIn("x-zp-platform", injected)
        self.assertNotIn("cookie", injected)                           # only what a page script may set is replayed
        self.assertNotIn("user-agent", injected)

    def test_template_from_capture_needs_a_url_and_a_body(self):
        self.assertIsNone(template_from_capture(None))
        self.assertIsNone(template_from_capture({"url": "https://x"}))
        template = template_from_capture({"url": "https://x", "postData": "{}", "headers": {"X-ZP-Device-SN": "a", "Referer": "r"}})
        self.assertEqual(template, {"u": "https://x", "h": {"X-ZP-Device-SN": "a"}, "body": "{}"})
