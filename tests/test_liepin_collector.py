import json
from unittest import TestCase
from unittest.mock import patch

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.orchestrator import normalize_collection_options
from bosshunter.collection.platforms.liepin import (
    JS_EXTRACT_DETAIL,
    JS_EXTRACT_LIST,
    LiepinBrowser,
    LiepinCollector,
    get_liepin_city_code,
)
from bosshunter.collection.text import clean_job_description


class LiepinCollectorTests(TestCase):
    def setUp(self):
        self._patches = [
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
    def test_list_script_uses_stable_attribute_selector(self):
        self.assertIn('a[data-nick="job-detail-job-info"]', JS_EXTRACT_LIST)
        self.assertIn("liepin", JS_EXTRACT_LIST)
        self.assertIn("job", JS_EXTRACT_LIST)

    def test_detail_script_targets_job_description(self):
        self.assertIn("job-intro-container", JS_EXTRACT_DETAIL)
        self.assertIn("job-title-box", JS_EXTRACT_DETAIL)

    def test_city_and_option_defaults_are_fail_closed(self):
        self.assertEqual(get_liepin_city_code("北京市"), "010")
        self.assertEqual(get_liepin_city_code("上海市"), "020")
        self.assertEqual(get_liepin_city_code("广州市"), "050020")
        self.assertIsNone(get_liepin_city_code("未知城市"))
        options = normalize_collection_options({}, {
            "platform_order": ["liepin"],
            "platforms": {"liepin": {"keywords": ["AI 产品"], "cities": ["上海"]}},
        })
        search = options["platforms"]["liepin"]
        self.assertEqual(search["city_codes"], {"上海": "020"})
        self.assertEqual(search["max_pages"], 1)
        self.assertNotIn("target_count", search)

    def test_unknown_city_cannot_bypass_snapshot_with_external_code(self):
        with self.assertRaises(ValueError):
            normalize_collection_options({}, {
                "platform_order": ["liepin"],
                "platforms": {"liepin": {
                    "keywords": ["AI 产品"],
                    "cities": ["未知城市"],
                    "city_codes": {"未知城市": "999"},
                }},
            })

    def test_beijing_search_uses_verified_liepin_city_code(self):
        request = PlatformCollectionRequest(
            "liepin",
            ["AI 产品"],
            ["北京"],
            {"北京": "010"},
            max_pages=1,
        )

        url = LiepinCollector.build_search_url(request, "北京", "AI 产品")

        self.assertIn("dqs=010", url)
        self.assertIn("key=AI%20%E4%BA%A7%E5%93%81", url)

    def test_collection_uses_platform_identity_and_rate_limit(self):
        list_payload = json.dumps({"status": "ready", "jobs": [
            {
                "source_job_id": "job-1",
                "salary": "15-25K",
                "title": "AI 产品经理",
                "company": "示例公司",
                "city": "上海",
                "url": "https://www.liepin.com/job/1001.shtml",
            },
            {
                "source_job_id": "job-2",
                "salary": "15-25K",
                "title": "AI 产品运营",
                "company": "示例公司",
                "city": "上海",
                "url": "https://www.liepin.com/job/1002.shtml",
            },
        ]}, ensure_ascii=False)
        detail_payload = json.dumps({
            "status": "ready",
            "hr_active": "3小时前在线",
            "title": "AI 产品",
            "company": "示例公司",
            "city": "上海",
            "jd": "负责需求分析，来自BOSS直聘要求会SQL。",
        }, ensure_ascii=False)
        sleeps: list[float] = []

        def evaluate(_target, script):
            return list_payload if "job-detail-job-info" in script else detail_payload

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or len(collected) < 2,
            on_parse_failed=lambda reason: self.fail(reason),
            on_event=lambda **_kwargs: None,
        )
        result = LiepinCollector(
            browser=browser,
            sleep=sleeps.append,
            uniform=lambda _low, _high: 13.0,
        ).collect(
            PlatformCollectionRequest("liepin", ["AI 产品"], ["上海"], {"上海": "020"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual([candidate.storage_id for candidate in collected], ["liepin:job-1", "liepin:job-2"])
        self.assertEqual(sleeps, [13.0, 13.0])
        self.assertIn("需求分析", collected[0].jd)

    def test_verification_page_stops_platform(self):
        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=lambda _target, _script: json.dumps({"status": "blocked", "jobs": []}),
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda _candidate: True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        sleeps = []
        collector = LiepinCollector(browser=browser, sleep=sleeps.append, uniform=lambda _a, _b: 1.0)
        result = collector.collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason_code, "rate_limit")
        self.assertEqual([s for s in sleeps if s >= 60], [60, 180])        # waited and retried before giving up
        self.assertEqual(collector.page_delay_range[0], LiepinCollector(browser=browser).page_delay_range[0] * 4)

    def test_a_one_off_verification_wording_recovers_after_backing_off(self):
        calls = []

        def evaluate(_target, _script):
            calls.append(1)
            if len(calls) == 1:                                            # the first read looks like a verification page
                return json.dumps({"status": "blocked", "jobs": []})
            return json.dumps({"status": "empty", "jobs": []})

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda _candidate: True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        sleeps = []
        result = LiepinCollector(browser=browser, sleep=sleeps.append, uniform=lambda _a, _b: 1.0).collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
            hooks,
        )
        self.assertNotEqual(result.status, "blocked")
        self.assertIn(60, sleeps)

    def test_collection_waits_for_spa_list_render(self):
        evaluations = 0
        sleeps = []

        def evaluate(_target, script):
            nonlocal evaluations
            if "job-detail-job-info" in script:
                evaluations += 1
                if evaluations < 3:
                    return json.dumps({"status": "waiting", "jobs": []})
                return json.dumps({"status": "ready", "jobs": [{
                    "source_job_id": "job-spa",
                    "salary": "15-25K",
                    "title": "AI 运营",
                    "company": "示例公司",
                    "city": "上海",
                    "url": "https://www.liepin.com/job/2001.shtml",
                }]})
            return json.dumps({
                "status": "ready", "hr_active": "3小时前在线", "title": "AI 运营", "company": "示例公司",
                "city": "上海", "jd": "负责 AI 产品运营与数据分析。",
            })

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda _candidate: False,
            on_parse_failed=lambda reason: self.fail(reason),
            on_event=lambda **_kwargs: None,
        )

        result = LiepinCollector(browser=browser, sleep=sleeps.append).collect(
            PlatformCollectionRequest("liepin", ["AI运营"], ["上海"], {"上海": "020"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual(evaluations, 3)
        self.assertEqual(sleeps[:2], [0.75, 0.75])

    def test_multi_page_collection_uses_url_pagination_and_pacing(self):
        list_calls = {"n": 0}
        navigation_urls = []
        sleeps = []

        def evaluate(_target, script):
            if "job-detail-job-info" in script:
                list_calls["n"] += 1
                if list_calls["n"] == 1:
                    return json.dumps({"status": "ready", "jobs": [{
                        "source_job_id": "job-p1",
                        "salary": "15-25K",
                        "title": "AI 资深工程师",
                        "company": "示例公司",
                        "city": "上海",
                        "url": "https://www.liepin.com/job/3001.shtml",
                    }]})
                return json.dumps({"status": "ready", "jobs": [{
                    "source_job_id": "job-p2",
                    "salary": "15-25K",
                    "title": "AI 数据工程师",
                    "company": "示例公司",
                    "city": "上海",
                    "url": "https://www.liepin.com/job/3002.shtml",
                }]})
            return json.dumps({
                "status": "ready", "hr_active": "3小时前在线", "title": "AI 工程师", "company": "示例公司",
                "city": "上海", "jd": "负责 AI 平台研发。",
            })

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
            navigate_action=lambda _target, url: navigation_urls.append(url) or True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        result = LiepinCollector(
            browser=browser,
            sleep=sleeps.append,
            uniform=lambda _low, _high: 33.0,
        ).collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=2),
            hooks,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "search_exhausted")
        self.assertEqual(
            [c.storage_id for c in collected],
            ["liepin:job-p1", "liepin:job-p2"],
        )
        self.assertIn("dqs=020", navigation_urls[0])
        self.assertTrue(any("curPage=1" in url for url in navigation_urls))
        self.assertEqual(sleeps, [33.0, 33.0, 33.0])

    def test_empty_next_page_completes_gracefully(self):
        list_calls = {"n": 0}

        def evaluate(_target, script):
            if "job-detail-job-info" in script:
                list_calls["n"] += 1
                if list_calls["n"] > 1:
                    return json.dumps({"status": "empty", "jobs": []})
                return json.dumps({"status": "ready", "jobs": [{
                    "source_job_id": f"job-{list_calls['n']}",
                    "salary": "15-25K",
                    "title": "AI 工程师",
                    "company": "示例公司",
                    "city": "上海",
                    "url": f"https://www.liepin.com/job/{4000 + list_calls['n']}.shtml",
                }]})
            return json.dumps({
                "status": "ready", "hr_active": "3小时前在线", "title": "AI 工程师", "company": "示例公司",
                "city": "上海", "jd": "负责 AI 研发。",
            })

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        result = LiepinCollector(
            browser=browser,
            sleep=lambda _s: None,
            uniform=lambda _low, _high: 33.0,
        ).collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=3),
            hooks,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "search_exhausted")
        self.assertEqual(len(collected), 1)
        self.assertEqual(list_calls["n"], 2)

    def test_login_required_detail_keeps_list_info(self):
        list_payload = json.dumps({"status": "ready", "jobs": [{
            "source_job_id": "job-login",
            "salary": "15-25K",
            "title": "AI 架构师",
            "company": "示例公司",
            "city": "上海",
            "url": "https://www.liepin.com/job/5001.shtml",
        }]}, ensure_ascii=False)

        detail_evaluations = 0
        sleeps = []

        def evaluate(_target, script):
            nonlocal detail_evaluations
            if "job-detail-job-info" in script:
                return list_payload
            detail_evaluations += 1
            return json.dumps({"status": "login_required"})

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        parse_failures = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or True,
            on_parse_failed=lambda reason: parse_failures.append(reason),
            on_event=lambda **_kwargs: None,
        )
        result = LiepinCollector(
            browser=browser,
            sleep=sleeps.append,
            uniform=lambda _low, _high: 13.0,
        ).collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0].title, "AI 架构师")
        self.assertEqual(detail_evaluations, 1)
        self.assertEqual(sleeps, [13.0])
        self.assertTrue(any("登录" in f or "wow.liepin" in f for f in parse_failures))

    def test_offline_detail_skips_gracefully(self):
        list_payload = json.dumps({"status": "ready", "jobs": [{
            "source_job_id": "job-offline",
            "title": "AI 研究员",
            "company": "示例公司",
            "city": "上海",
            "url": "https://www.liepin.com/job/6001.shtml",
        }]}, ensure_ascii=False)

        def evaluate(_target, script):
            if "job-detail-job-info" in script:
                return list_payload
            return json.dumps({"status": "offline"})

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        result = LiepinCollector(
            browser=browser,
            sleep=lambda _s: None,
            uniform=lambda _low, _high: 13.0,
        ).collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(collected), 0)


class LiepinEnhancedTests(TestCase):
    """猎聘采集器增强：时间窗口 / 过滤链 / config 集成。"""

    def _hooks(self, collected=None):
        collected = collected if collected is not None else []
        return CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _c: True,
            on_candidate=lambda c: collected.append(c) or True,
            on_parse_failed=lambda _r: None,
            on_event=lambda **_: None,
        )

    def _browser_with_list(self, jobs):
        list_payload = json.dumps({"status": "ready", "jobs": jobs}, ensure_ascii=False)
        detail_payload = json.dumps({"status": "login_required"})
        return LiepinBrowser(
            new_tab=lambda url, **_kw: url,
            close_tab=lambda _t: True,
            evaluate=lambda _t, s: list_payload if "job-detail-job-info" in s else detail_payload,
            scroll=lambda *_a, **_kw: True,
            wait_for_load=lambda *_a, **_kw: True,
        )

    def test_outside_send_window_skips_collection(self):
        browser = self._browser_with_list([])
        collector = LiepinCollector(browser=browser)
        with patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=False):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                self._hooks(),
            )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "outside_window")

    def test_day_off_skips_collection(self):
        browser = self._browser_with_list([])
        collector = LiepinCollector(browser=browser)
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=True),
        ):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                self._hooks(),
            )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "day_off")

    def test_deal_breaker_in_title_filtered(self):
        jobs = [{"source_job_id": "1", "title": "外包AI", "company": "公司", "city": "上海",
                 "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_list(jobs)
        collected = []
        collector = LiepinCollector(
            browser=browser, sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
            config={"profile": {"deal_breakers": ["外包"]}},
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
        ):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                self._hooks(collected),
            )
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(collected), 0)

    def test_blocked_company_filtered(self):
        jobs = [{"source_job_id": "1", "title": "AI工程师", "company": "黑名单公司", "city": "上海",
                 "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_list(jobs)
        collected = []
        collector = LiepinCollector(
            browser=browser, sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
            config={"profile": {"blocked_companies": ["黑名单公司"]}},
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
        ):
            collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                self._hooks(collected),
            )
        self.assertEqual(len(collected), 0)

    def test_internship_filtered_when_disallowed(self):
        jobs = [{"source_job_id": "1", "title": "AI实习工程师", "company": "公司", "city": "上海",
                 "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_list(jobs)
        collected = []
        collector = LiepinCollector(
            browser=browser, sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
            config={"profile": {"allow_internship": False}},
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
        ):
            collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                self._hooks(collected),
            )
        self.assertEqual(len(collected), 0)

    def test_internship_allowed_when_explicitly_enabled(self):
        jobs = [{"source_job_id": "1", "title": "AI实习工程师", "company": "公司", "city": "上海",
                 "salary": "15-25K", "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_list(jobs)
        collected = []
        collector = LiepinCollector(
            browser=browser, sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
            config={"profile": {"allow_internship": True}},
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
        ):
            collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                self._hooks(collected),
            )
        self.assertEqual(len(collected), 1)

    def test_no_filter_config_passes_all(self):
        jobs = [{"source_job_id": "1", "title": "AI工程师", "company": "公司", "city": "上海",
                 "salary": "15-25K", "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_list(jobs)
        collected = []
        collector = LiepinCollector(
            browser=browser, sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
        ):
            collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                self._hooks(collected),
            )
        self.assertEqual(len(collected), 1)

    def test_salary_below_min_filtered_with_event(self):
        jobs = [{"source_job_id": "1", "title": "AI工程师", "company": "公司", "city": "上海",
                 "salary": "5-8K", "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_list(jobs)
        collected = []
        events = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _c: True,
            on_candidate=lambda c: collected.append(c) or True,
            on_parse_failed=lambda _r: None,
            on_event=lambda **kw: events.append(kw),
        )
        collector = LiepinCollector(
            browser=browser, sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
            config={"profile": {"salary_min": 10, "salary_max": 20}},
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
        ):
            collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                hooks,
            )
        self.assertEqual(len(collected), 0)
        filtered_events = [e for e in events if e.get("increment_filtered")]
        self.assertEqual(len(filtered_events), 1)
        self.assertIn("薪资低于硬性要求", filtered_events[0].get("message", ""))

    def test_salary_within_range_collected(self):
        jobs = [{"source_job_id": "1", "title": "AI工程师", "company": "公司", "city": "上海",
                 "salary": "1-2万", "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_list(jobs)
        collected = []
        collector = LiepinCollector(
            browser=browser, sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
            config={"profile": {"salary_min": 10, "salary_max": 20}},
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
        ):
            collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                self._hooks(collected),
            )
        self.assertEqual(len(collected), 1)


class LiepinResumeCheckpointTests(TestCase):
    """猎聘断点续采：词级跳过 / 页级恢复 / checkpoint 记录 / 完成标记。"""

    def _hooks(self, collected=None, events=None):
        collected = collected if collected is not None else []
        events = events if events is not None else []
        return CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _c: True,
            on_candidate=lambda c: collected.append(c) or True,
            on_parse_failed=lambda _r: None,
            on_event=lambda **kw: events.append(kw),
        )

    def _browser_with_pages(self, pages_jobs, detail_status="login_required"):
        """pages_jobs: dict[int, list[dict]] — page number to list jobs."""
        call_state = {"page": 0}

        def evaluate(_target, script):
            if "job-detail-job-info" in script:
                call_state["page"] += 1
                page_num = call_state["page"]
                jobs = pages_jobs.get(page_num, [])
                if jobs is None:
                    return json.dumps({"status": "empty", "jobs": []})
                return json.dumps({"status": "ready", "jobs": jobs}, ensure_ascii=False)
            return json.dumps({"status": detail_status})

        return LiepinBrowser(
            new_tab=lambda url, **_kw: url,
            close_tab=lambda _t: True,
            evaluate=evaluate,
            scroll=lambda *_a, **_kw: True,
            wait_for_load=lambda *_a, **_kw: True,
        )

    def _resume_patches(self, collected_combos=None, saved_page=0):
        collected_combos = collected_combos if collected_combos is not None else set()
        return (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
            patch("bosshunter.collection.platforms.liepin.prune_collected_combos"),
            patch("bosshunter.collection.platforms.liepin.prune_page_progress"),
            patch("bosshunter.collection.platforms.liepin.get_collected_combos", return_value=collected_combos),
            patch("bosshunter.collection.platforms.liepin.get_page_progress", return_value=saved_page),
            patch("bosshunter.collection.platforms.liepin.upsert_page_progress"),
            patch("bosshunter.collection.platforms.liepin.mark_combo_collected"),
            patch("bosshunter.collection.platforms.liepin.delete_page_progress"),
        )

    def test_completed_combo_skipped_entirely(self):
        jobs = [{"source_job_id": "1", "title": "AI", "company": "c", "city": "上海",
                 "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_pages({1: jobs})
        collected = []
        events = []
        collector = LiepinCollector(
            browser=browser, safety_conn=object(),
            sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
            patch("bosshunter.collection.platforms.liepin.prune_collected_combos"),
            patch("bosshunter.collection.platforms.liepin.prune_page_progress"),
            patch("bosshunter.collection.platforms.liepin.get_collected_combos", return_value={("上海", "AI")}),
            patch("bosshunter.collection.platforms.liepin.get_page_progress", return_value=0),
            patch("bosshunter.collection.platforms.liepin.upsert_page_progress"),
            patch("bosshunter.collection.platforms.liepin.mark_combo_collected"),
            patch("bosshunter.collection.platforms.liepin.delete_page_progress"),
        ):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
                self._hooks(collected, events),
            )
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(collected), 0)
        skip_events = [e for e in events if e.get("phase") == "completed_keyword"]
        self.assertTrue(any("断点续采" in str(e.get("message", "")) for e in skip_events))

    def test_page_progress_resumes_from_saved_page(self):
        jobs_p3 = [{"source_job_id": "j3", "title": "AI", "company": "c", "city": "上海",
                    "url": "https://www.liepin.com/job/3001.shtml"}]
        browser = self._browser_with_pages({3: jobs_p3, 4: None})
        collected = []
        collector = LiepinCollector(
            browser=browser, safety_conn=object(),
            sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
        )
        navigation_urls = []
        browser.navigate_action = lambda _t, url: navigation_urls.append(url) or True
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
            patch("bosshunter.collection.platforms.liepin.prune_collected_combos"),
            patch("bosshunter.collection.platforms.liepin.prune_page_progress"),
            patch("bosshunter.collection.platforms.liepin.get_collected_combos", return_value=set()),
            patch("bosshunter.collection.platforms.liepin.get_page_progress", return_value=2),
            patch("bosshunter.collection.platforms.liepin.upsert_page_progress"),
            patch("bosshunter.collection.platforms.liepin.mark_combo_collected"),
            patch("bosshunter.collection.platforms.liepin.delete_page_progress"),
        ):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=4),
                self._hooks(collected),
            )
        self.assertEqual(result.status, "completed")
        self.assertTrue(any("curPage=2" in url for url in navigation_urls))

    def test_pages_are_checkpointed_in_ascending_order(self):
        jobs = [{"source_job_id": "j", "title": "AI", "company": "c", "city": "上海",
                 "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_pages({1: jobs, 2: jobs, 3: None})
        collected = []
        checkpoints = []
        collector = LiepinCollector(
            browser=browser, safety_conn=object(),
            sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
            patch("bosshunter.collection.platforms.liepin.prune_collected_combos"),
            patch("bosshunter.collection.platforms.liepin.prune_page_progress"),
            patch("bosshunter.collection.platforms.liepin.get_collected_combos", return_value=set()),
            patch("bosshunter.collection.platforms.liepin.get_page_progress", return_value=0),
            patch("bosshunter.collection.platforms.liepin.upsert_page_progress",
                  side_effect=lambda _c, _s, _ci, _k, page: checkpoints.append(page)),
            patch("bosshunter.collection.platforms.liepin.mark_combo_collected"),
            patch("bosshunter.collection.platforms.liepin.delete_page_progress"),
        ):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=3),
                self._hooks(collected),
            )
        self.assertEqual(result.status, "completed")
        self.assertEqual(checkpoints, [1, 2])

    def test_word_completion_marks_combo_and_clears_page_progress(self):
        jobs = [{"source_job_id": "j", "title": "AI", "company": "c", "city": "上海",
                 "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_pages({1: jobs, 2: None})
        collected = []
        collector = LiepinCollector(
            browser=browser, safety_conn=object(),
            sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
            patch("bosshunter.collection.platforms.liepin.prune_collected_combos"),
            patch("bosshunter.collection.platforms.liepin.prune_page_progress"),
            patch("bosshunter.collection.platforms.liepin.get_collected_combos", return_value=set()),
            patch("bosshunter.collection.platforms.liepin.get_page_progress", return_value=0),
            patch("bosshunter.collection.platforms.liepin.upsert_page_progress"),
            patch("bosshunter.collection.platforms.liepin.mark_combo_collected") as mark_complete,
            patch("bosshunter.collection.platforms.liepin.delete_page_progress") as delete_progress,
        ):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=2),
                self._hooks(collected),
            )
        self.assertEqual(result.status, "completed")
        mark_complete.assert_called_once()
        delete_progress.assert_called_once()

    def test_blocked_page_does_not_mark_combo(self):
        browser = LiepinBrowser(
            new_tab=lambda url, **_kw: url,
            close_tab=lambda _t: True,
            evaluate=lambda _t, _s: json.dumps({"status": "blocked", "jobs": []}),
            scroll=lambda *_a, **_kw: True,
            wait_for_load=lambda *_a, **_kw: True,
        )
        collector = LiepinCollector(
            browser=browser, safety_conn=object(),
            sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
            patch("bosshunter.collection.platforms.liepin.prune_collected_combos"),
            patch("bosshunter.collection.platforms.liepin.prune_page_progress"),
            patch("bosshunter.collection.platforms.liepin.get_collected_combos", return_value=set()),
            patch("bosshunter.collection.platforms.liepin.get_page_progress", return_value=0),
            patch("bosshunter.collection.platforms.liepin.upsert_page_progress"),
            patch("bosshunter.collection.platforms.liepin.mark_combo_collected") as mark_complete,
            patch("bosshunter.collection.platforms.liepin.delete_page_progress"),
        ):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=3),
                self._hooks(),
            )
        self.assertEqual(result.status, "blocked")
        mark_complete.assert_not_called()

    def test_saved_page_exceeds_max_pages_skips_keyword(self):
        jobs = [{"source_job_id": "j", "title": "AI", "company": "c", "city": "上海",
                 "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_pages({1: jobs})
        collected = []
        events = []
        collector = LiepinCollector(
            browser=browser, safety_conn=object(),
            sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
            patch("bosshunter.collection.platforms.liepin.prune_collected_combos"),
            patch("bosshunter.collection.platforms.liepin.prune_page_progress"),
            patch("bosshunter.collection.platforms.liepin.get_collected_combos", return_value=set()),
            patch("bosshunter.collection.platforms.liepin.get_page_progress", return_value=5),
            patch("bosshunter.collection.platforms.liepin.upsert_page_progress"),
            patch("bosshunter.collection.platforms.liepin.mark_combo_collected") as mark_complete,
            patch("bosshunter.collection.platforms.liepin.delete_page_progress") as delete_progress,
        ):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=3),
                self._hooks(collected, events),
            )
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(collected), 0)
        mark_complete.assert_called_once()
        delete_progress.assert_called_once()
        skip_events = [e for e in events if e.get("phase") == "completed_keyword"]
        self.assertTrue(any("页级断点" in str(e.get("message", "")) for e in skip_events))

    def test_prune_called_on_collect_start(self):
        jobs = [{"source_job_id": "j", "title": "AI", "company": "c", "city": "上海",
                 "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_pages({1: jobs, 2: None})
        collector = LiepinCollector(
            browser=browser, safety_conn=object(),
            sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
            patch("bosshunter.collection.platforms.liepin.prune_collected_combos") as prune_combos,
            patch("bosshunter.collection.platforms.liepin.prune_page_progress") as prune_pages,
            patch("bosshunter.collection.platforms.liepin.get_collected_combos", return_value=set()),
            patch("bosshunter.collection.platforms.liepin.get_page_progress", return_value=0),
            patch("bosshunter.collection.platforms.liepin.upsert_page_progress"),
            patch("bosshunter.collection.platforms.liepin.mark_combo_collected"),
            patch("bosshunter.collection.platforms.liepin.delete_page_progress"),
        ):
            collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=2),
                self._hooks(),
            )
        prune_combos.assert_called_once()
        prune_pages.assert_called_once()

    def test_resume_ttl_hours_from_config(self):
        collector = LiepinCollector(
            config={"platforms": {"liepin": {"search": {"resume_ttl_hours": 48}}}},
        )
        self.assertEqual(collector._resume_ttl_hours(), 48)

    def test_resume_ttl_hours_clamped_to_range(self):
        collector_low = LiepinCollector(
            config={"platforms": {"liepin": {"search": {"resume_ttl_hours": -5}}}},
        )
        self.assertEqual(collector_low._resume_ttl_hours(), 1)
        collector_high = LiepinCollector(
            config={"platforms": {"liepin": {"search": {"resume_ttl_hours": 9999}}}},
        )
        self.assertEqual(collector_high._resume_ttl_hours(), 720)

    def test_resume_ttl_hours_default_when_missing(self):
        from bosshunter.collection.base import hours_since_midnight

        collector = LiepinCollector()
        self.assertEqual(collector._resume_ttl_hours(), hours_since_midnight())      # checkpoints count for today only

    def test_resume_ttl_hours_invalid_falls_back_to_default(self):
        collector = LiepinCollector(
            config={"platforms": {"liepin": {"search": {"resume_ttl_hours": "invalid"}}}},
        )
        self.assertEqual(collector._resume_ttl_hours(), 24)

    def test_no_safety_conn_skips_resume_logic(self):
        jobs = [{"source_job_id": "j", "title": "AI", "company": "c", "city": "上海",
                 "url": "https://www.liepin.com/job/1001.shtml"}]
        browser = self._browser_with_pages({1: jobs, 2: None})
        collected = []
        collector = LiepinCollector(
            browser=browser, safety_conn=None,
            sleep=lambda _s: None, uniform=lambda _a, _b: 10.0,
        )
        with (
            patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False),
            patch("bosshunter.collection.platforms.liepin.prune_collected_combos") as prune_combos,
            patch("bosshunter.collection.platforms.liepin.get_collected_combos") as get_combos,
            patch("bosshunter.collection.platforms.liepin.get_page_progress") as get_progress,
            patch("bosshunter.collection.platforms.liepin.upsert_page_progress") as upsert,
            patch("bosshunter.collection.platforms.liepin.mark_combo_collected") as mark,
        ):
            result = collector.collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=2),
                self._hooks(collected),
            )
        self.assertEqual(result.status, "completed")
        prune_combos.assert_not_called()
        get_combos.assert_not_called()
        get_progress.assert_not_called()
        upsert.assert_not_called()
        mark.assert_not_called()


def test_detail_candidate_carries_hr_activity_and_polls_for_a_late_hr_card():
    from bosshunter.collection.models import JobCandidate
    from bosshunter.collection.platforms.liepin import HR_RENDER_POLLS, LiepinCollector

    base = JobCandidate(platform="liepin", source_job_id="1", title="t", company="c", url="https://www.liepin.com/job/1.shtml")
    final = LiepinCollector._candidate_from_detail({"jd": "x", "hr_active": "一周前在线"}, base)
    assert final.hr_active == "一周前在线" and HR_RENDER_POLLS < 10


def test_hr_activity_floor_becomes_the_search_pages_recruiter_activity_filter():
    from bosshunter.collection.models import PlatformCollectionRequest
    from bosshunter.collection.platforms.liepin import PUB_TIME_BY_LEVEL, LiepinCollector

    assert PUB_TIME_BY_LEVEL == {3: 1, 2: 3, 1: 30}
    request = PlatformCollectionRequest(platform="liepin", keywords=["AI"], cities=["上海"], city_codes={"上海": "020"})
    assert "pubTime" not in LiepinCollector.build_search_url(request, "上海", "AI")
    request.filters["pub_time"] = 3
    assert LiepinCollector.build_search_url(request, "上海", "AI").endswith("&pubTime=3")


class LiepinListPatienceTests(TestCase):
    def _run(self, waits_before_ready):
        polls = []
        sleeps = []

        def evaluate(_target, script):
            if "job-detail-job-info" in script:
                polls.append(1)
                if len(polls) <= waits_before_ready:
                    return json.dumps({"status": "waiting", "jobs": []})
                return json.dumps({"status": "empty", "jobs": []})
            return json.dumps({"status": "ready"})

        navigations = []
        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
            navigate_action=lambda _target, url: navigations.append(url) or True,
        )
        hooks = CollectorHooks(
            stop_event=None, on_list_candidate=lambda _c: True, on_candidate=lambda _c: True,
            on_parse_failed=lambda _r: None, on_event=lambda **_kw: None,
        )
        result = LiepinCollector(browser=browser, config={"throttle": {"collect_windows": ["00:00-23:59"], "day_off_probability": 0}}, sleep=sleeps.append, uniform=lambda _a, _b: 1.0).collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1), hooks,
        )
        return result, len(polls), len(navigations)

    def test_a_slow_list_is_waited_for_instead_of_giving_the_keyword_up(self):
        result, polls, _ = self._run(waits_before_ready=25)           # needs more than the old 10 polls
        self.assertNotEqual(result.reason_code, "list_not_ready")
        self.assertEqual(polls, 26)

    def test_a_list_that_never_loads_is_reloaded_once_then_given_up(self):
        result, polls, navigations = self._run(waits_before_ready=10_000)
        self.assertEqual(result.reason_code, "list_not_ready")
        self.assertEqual(polls, 10 + 30 + 10)                          # first poll, patience, after the reload
        self.assertGreaterEqual(navigations, 2)                        # the search page plus one reload


class LiepinSiteFilterTests(TestCase):
    def test_the_sites_own_experience_filter_is_in_the_search_url(self):
        request = PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, filters={"pub_time": 3, "work_year": "1"})
        url = LiepinCollector.build_search_url(request, "上海", "AI")
        self.assertIn("pubTime=3", url)
        self.assertIn("workYearCode=1", url)
        request.filters["work_year"] = "0$1"
        self.assertIn("workYearCode=0%241", LiepinCollector.build_search_url(request, "上海", "AI"))
        self.assertNotIn("workYearCode", LiepinCollector.build_search_url(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}), "上海", "AI"))

    def test_work_year_comes_from_the_boss_experience_filter_unless_set_explicitly(self):
        from bosshunter.collection.platforms.liepin import _work_year_code

        boss = {"platforms": {"boss": {"search": {"filters": {"experience": ["经验不限", "应届生"]}}}}}
        self.assertEqual(_work_year_code(boss), "1")
        self.assertEqual(_work_year_code({}), "")
        explicit = {"platforms": {**boss["platforms"], "liepin": {"search": {"filters": {"work_year": "0$1"}}}}}
        self.assertEqual(_work_year_code(explicit), "0$1")
        none_wanted = {"platforms": {**boss["platforms"], "liepin": {"search": {"filters": {"work_year": ""}}}}}
        self.assertEqual(_work_year_code(none_wanted), "")

    def test_unrelated_titles_from_the_fuzzy_search_are_dropped_before_any_detail_page(self):
        from bosshunter.collection.models import JobCandidate

        collector = LiepinCollector(config={"search": {"keywords": ["AI Agent开发", "Java后端"]}})

        def candidate(title):
            return JobCandidate(platform="liepin", source_job_id="1", title=title, company="某公司X", source_keyword="Agent工程师")

        self.assertEqual(collector._filter_reason(candidate("跟单员")), "职位与搜索关键词、简历关键词都不相关")
        self.assertEqual(collector._filter_reason(candidate("酒店前台")), "职位与搜索关键词、简历关键词都不相关")
        self.assertEqual(collector._filter_reason(candidate("Agent工程师（上海）")), "")
        self.assertEqual(collector._filter_reason(candidate("Java后端开发")), "")             # speaks of the resume


class LiepinVerifyWallTests(TestCase):
    def test_an_sms_verification_wall_stops_the_platform_at_once_without_retrying(self):
        polls, navigations = [], []

        def evaluate(_target, script):
            if "job-detail-job-info" in script:
                polls.append(1)
                return json.dumps({"status": "verify", "jobs": []})
            return json.dumps({"status": "ready"})

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url, close_tab=lambda _target: True, evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True, wait_for_load=lambda *_args, **_kwargs: True,
            navigate_action=lambda _target, url: navigations.append(url) or True,
        )
        hooks = CollectorHooks(stop_event=None, on_list_candidate=lambda _c: True, on_candidate=lambda _c: True,
                               on_parse_failed=lambda _r: None, on_event=lambda **_kw: None)
        with patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True),                 patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False):
            result = LiepinCollector(browser=browser, config={"throttle": {"collect_windows": ["00:00-23:59"], "day_off_probability": 0}},
                                     sleep=lambda _s: None, uniform=lambda _a, _b: 1.0).collect(
                PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1), hooks)
        self.assertEqual((result.status, result.reason_code), ("blocked", "captcha"))
        self.assertEqual(len(polls), 1)
        self.assertEqual(len(navigations), 1)                      # no reload against a wall that only the user can pass

    def test_the_list_script_recognises_the_verification_page(self):
        self.assertIn("safe.liepin.com", JS_EXTRACT_LIST)
        self.assertIn("'verify'", JS_EXTRACT_LIST)
