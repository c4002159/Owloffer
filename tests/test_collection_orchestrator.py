import tempfile
from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionResult
from bosshunter.collection.orchestrator import (
    CollectionOrchestrator,
    normalize_collection_options,
    validate_collection_options,
)
from bosshunter.collection.registry import CollectorRegistry
from bosshunter.db import get_db, insert_job


def _candidate(platform: str, source_id: str, title: str = "正常岗位") -> JobCandidate:
    return JobCandidate(
        platform=platform,
        source_job_id=source_id,
        title=title,
        company="示例公司",
        city="北京",
        city_code="530" if platform == "zhilian" else "101010100",
        jd="负责岗位相关工作",
        url=f"https://example.test/{platform}/{source_id}",
    )


def _options(*, order=None, auto_score=False):
    order = order or ["boss"]
    values = {}
    for platform in order:
        values[platform] = {
            "keywords": ["AI"],
            "cities": ["北京"],
            "city_codes": {"北京": "530"} if platform == "zhilian" else {"北京": "101010100"},
            "max_pages": 1,
            "sort": "default",
        }
    return {"platform_order": order, "auto_score": auto_score, "platforms": values}


class _FakeCollector:
    def __init__(self, platform, events, candidates, *, stop=False):
        self.platform = platform
        self.events = events
        self.candidates = candidates
        self.stop = stop

    def collect(self, _request, hooks: CollectorHooks):
        self.events.append(f"start:{self.platform}")
        for candidate in self.candidates:
            if hooks.stop_event and hooks.stop_event.is_set():
                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
            if not hooks.on_list_candidate(candidate):
                continue
            if not hooks.on_candidate(candidate):
                self.events.append(f"target:{self.platform}")
                return PlatformCollectionResult(self.platform, "completed", "target_reached", "达到目标")
            if self.stop:
                hooks.stop_event.set()
                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
        return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "无更多结果")


class _BlockedCollector:
    def __init__(self, platform, events, reason_code):
        self.platform = platform
        self.events = events
        self.reason_code = reason_code

    def collect(self, _request, _hooks: CollectorHooks):
        self.events.append(f"start:{self.platform}")
        return PlatformCollectionResult(self.platform, "blocked", self.reason_code, "平台已阻断")


class CollectionOrchestratorTests(TestCase):
    def test_collection_failures_preserve_context_and_remain_bounded(self):
        from bosshunter.collection_run_store import get_collection_run

        class FailureCollector:
            def collect(self, request, hooks):
                hooks.on_event(city="深圳", keyword="AI", page=2)
                for i in range(201):
                    hooks.on_parse_failed(f"详情缺少字段 {i}")
                candidate = _candidate("boss", "save-error", "保存失败测试岗位")
                if hooks.on_list_candidate(candidate):
                    hooks.on_candidate(candidate)
                return PlatformCollectionResult("boss", "completed_with_shortage", "incomplete", "部分岗位处理失败")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "errors.db"
            with patch("bosshunter.collection.orchestrator.insert_job_if_new", side_effect=RuntimeError("private data")):
                result = CollectionOrchestrator({}, db_path=path, registry=CollectorRegistry({"boss": FailureCollector})).run(_options())
            state = get_collection_run(path, result["run_id"])["platform_states"]["boss"]
            self.assertEqual(state["parse_failed"], 201)
            self.assertEqual(state["save_failed"], 1)
            records = state["error_records"]
            self.assertEqual(len(records), 200)
            self.assertEqual(records[0]["reason"], "详情缺少字段 2")
            self.assertEqual(records[0]["kind"], "parse")
            self.assertEqual(records[0]["city"], "深圳")
            self.assertEqual(records[0]["keyword"], "AI")
            self.assertEqual(records[0]["page"], 2)
            self.assertEqual(records[0]["title"], "")
            self.assertEqual(records[-1]["kind"], "save")
            self.assertEqual(records[-1]["title"], "保存失败测试岗位")
            self.assertEqual(records[-1]["reason"], "保存岗位失败：RuntimeError")
            self.assertNotIn("private data", str(records))
            self.assertEqual(result["collected_job_ids"], [])

    def test_filter_records_are_bounded_persisted_and_separate_from_jobs(self):
        from bosshunter.collection_run_store import get_collection_run

        candidates = [_candidate("boss", str(i), "外包岗位") for i in range(205)]
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", [], candidates)})
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            result = CollectionOrchestrator(
                {"profile": {"deal_breakers": ["外包"]}}, db_path=db_path, registry=registry,
            ).run(_options())
            saved = get_collection_run(db_path, result["run_id"])
            state = saved["platform_states"]["boss"]
            self.assertEqual(state["filtered"], 205)
            self.assertEqual(len(state["filter_records"]), 200)
            self.assertEqual(state["filter_records"][0]["source_job_id"], "5")
            self.assertEqual(state["filter_records"][-1]["source_job_id"], "204")
            self.assertIn("外包", state["filter_records"][0]["reason"])
            self.assertEqual(state["filter_records"][0]["company"], "示例公司")
            conn = get_db(db_path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
            conn.close()

    def test_filter_records_preserve_platform_event_company_and_jd_reasons(self):
        class FilterCollector:
            def collect(self, request, hooks):
                candidate = _candidate(request.platform, "salary")
                hooks.on_event(message="薪资低于硬性要求: 10K < 12K", increment_filtered=True,
                               filtered_candidate=candidate)
                blocked = _candidate(request.platform, "blocked")
                blocked.company = "屏蔽公司"
                hooks.on_list_candidate(blocked)
                jd = _candidate(request.platform, "jd")
                jd.jd = "要求驻场"
                if hooks.on_list_candidate(jd):
                    hooks.on_candidate(jd)
                return PlatformCollectionResult(request.platform, "completed", "search_exhausted", "无更多结果")

        registry = CollectorRegistry({p: FilterCollector for p in ("boss", "zhilian")})
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(
                {"profile": {"blocked_companies": ["屏蔽公司"], "jd_deal_breakers": ["驻场"]}},
                db_path=Path(tmp) / "collection.db", registry=registry,
            ).run(_options(order=["boss", "zhilian"]))
        for state in result["platforms"].values():
            self.assertEqual(state["filtered"], 3)
            self.assertEqual([r["source_job_id"] for r in state["filter_records"]], ["salary", "blocked", "jd"])
            self.assertIn("10K < 12K", state["filter_records"][0]["reason"])
            self.assertIn("屏蔽公司", state["filter_records"][1]["reason"])
            self.assertIn("驻场", state["filter_records"][2]["reason"])

    def test_boss_zero_new_reports_duplicate_and_filtered_counts(self):
        candidates = [_candidate("boss", "duplicate"), _candidate("boss", "filtered", "外包岗位")]
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", [], candidates)})
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            conn = get_db(db_path)
            insert_job(conn, candidates[0].as_job_record())
            conn.close()
            result = CollectionOrchestrator(
                {"profile": {"deal_breakers": ["外包"]}}, db_path=db_path, registry=registry,
            ).run(_options())
        state = result["platforms"]["boss"]
        self.assertEqual(result["status"], "completed_with_shortage")
        self.assertEqual(state["reason_code"], "no_new_jobs")
        for text in ("新增 0 条", "读取 2 条", "重复 1 条", "过滤 1 条"):
            self.assertIn(text, state["message"])
        self.assertEqual(result["collected_job_ids"], [])

    def test_boss_summary_preserves_empty_search_reason(self):
        collector = _BlockedCollector("boss", [], "no_jobs_extracted")
        collector.collect = lambda *_: PlatformCollectionResult(
            "boss", "completed_with_shortage", "no_jobs_extracted", "BOSS 本轮未读取到岗位",
        )
        registry = CollectorRegistry({"boss": lambda: collector})
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(
                {}, db_path=Path(tmp) / "collection.db", registry=registry,
            ).run(_options())
        state = result["platforms"]["boss"]
        self.assertEqual(state["reason_code"], "no_jobs_extracted")
        self.assertIn("本轮未读取到岗位", state["message"])
        self.assertIn("新增 0 条", state["message"])

    def test_two_platforms_are_strictly_serial_and_save_only_new_rows(self):
        events = []
        boss_candidates = [
            _candidate("boss", "duplicate"),
            _candidate("boss", "filtered", "包含黑名单词岗位"),
            _candidate("boss", "save-fail"),
            _candidate("boss", "boss-new-1"),
            _candidate("boss", "boss-new-2"),
        ]
        zhilian_candidates = [_candidate("zhilian", "zl-new-1"), _candidate("zhilian", "zl-new-2")]
        registry = CollectorRegistry({
            "boss": lambda: _FakeCollector("boss", events, boss_candidates),
            "zhilian": lambda: _FakeCollector("zhilian", events, zhilian_candidates),
        })
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            db = get_db(db_path)
            try:
                insert_job(db, {**_candidate("boss", "duplicate").as_job_record()})
            finally:
                db.close()
            config = {"profile": {"deal_breakers": ["黑名单词"]}, "automation": {"collect_company_dedup": False}}
            real_insert = __import__("bosshunter.db", fromlist=["insert_job_if_new"]).insert_job_if_new

            def insert(record_conn, record, **kwargs):
                if record.get("source_job_id") == "save-fail":
                    raise RuntimeError("fixture save failure")
                return real_insert(record_conn, record, **kwargs)

            with patch("bosshunter.collection.orchestrator.insert_job_if_new", side_effect=insert):
                result = CollectionOrchestrator(config, db_path=db_path, registry=registry).run(
                    _options(order=["boss", "zhilian"])
                )

            db = get_db(db_path)
            try:
                rows = db.execute("SELECT id, source_platform FROM jobs ORDER BY id").fetchall()
            finally:
                db.close()

        self.assertEqual(events, ["start:boss", "start:zhilian"])
        self.assertEqual(result["platforms"]["boss"]["new"], 2)
        self.assertEqual(result["platforms"]["boss"]["duplicate"], 1)
        self.assertEqual(result["platforms"]["boss"]["filtered"], 1)
        self.assertEqual(result["platforms"]["boss"]["save_failed"], 1)
        self.assertEqual(result["platforms"]["zhilian"]["new"], 2)
        self.assertEqual(result["collected_job_ids"], ["boss-new-1", "boss-new-2", "zhilian:zl-new-1", "zhilian:zl-new-2"])
        self.assertEqual({row["source_platform"] for row in rows}, {"boss", "zhilian"})

    def test_auto_score_is_opt_in_and_receives_only_this_run_ids(self):
        candidates = [_candidate("boss", "new-1")]
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", [], candidates)})
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            with patch("bosshunter.ai.scorer.score_jobs") as score_jobs:
                result = CollectionOrchestrator({}, db_path=db_path, registry=registry).run(
                    _options(auto_score=True)
                )
        score_jobs.assert_called_once()
        kwargs = score_jobs.call_args.kwargs
        self.assertEqual(kwargs["scope"], "selected")
        self.assertEqual(kwargs["job_ids"], ["new-1"])
        self.assertFalse(kwargs["force_rescore"])
        self.assertEqual(result["collected_job_ids"], ["new-1"])

        with tempfile.TemporaryDirectory() as tmp:
            with patch("bosshunter.ai.scorer.score_jobs") as score_jobs:
                CollectionOrchestrator({}, db_path=Path(tmp) / "collection.db", registry=registry).run(
                    _options(auto_score=False)
                )
        score_jobs.assert_not_called()

    def test_stop_event_does_not_start_the_next_platform_or_scoring(self):
        events = []
        stop_event = Event()
        registry = CollectorRegistry({
            "boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "one")], stop=True),
            "zhilian": lambda: _FakeCollector("zhilian", events, [_candidate("zhilian", "two")]),
        })
        with tempfile.TemporaryDirectory() as tmp:
            config = {"_workbench_stop_event": stop_event}
            with patch("bosshunter.ai.scorer.score_jobs") as score_jobs:
                result = CollectionOrchestrator(config, db_path=Path(tmp) / "collection.db", registry=registry).run(
                    _options(order=["boss", "zhilian"], auto_score=True)
                )
        self.assertEqual(events, ["start:boss"])
        self.assertEqual(result["status"], "stopped")
        self.assertNotIn("start:zhilian", events)
        score_jobs.assert_not_called()

    def test_login_required_stops_only_current_platform_and_continues_queue(self):
        events = []
        registry = CollectorRegistry({
            "zhilian": lambda: _BlockedCollector("zhilian", events, "login_required"),
            "boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "boss-after-login")]),
        })
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(
                {}, db_path=Path(tmp) / "collection.db", registry=registry
            ).run(_options(order=["zhilian", "boss"]))

        self.assertEqual(events, ["start:zhilian", "start:boss"])
        self.assertEqual(result["status"], "completed_with_errors")
        self.assertEqual(result["platforms"]["zhilian"]["reason_code"], "login_required")
        self.assertEqual(result["platforms"]["boss"]["new"], 1)

    def test_non_login_block_still_stops_the_remaining_queue(self):
        events = []
        registry = CollectorRegistry({
            "zhilian": lambda: _BlockedCollector("zhilian", events, "rate_limit"),
            "boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "not-started")]),
        })
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(
                {}, db_path=Path(tmp) / "collection.db", registry=registry
            ).run(_options(order=["zhilian", "boss"]))

        self.assertEqual(events, ["start:zhilian"])
        self.assertEqual(result["status"], "completed_with_errors")
        self.assertEqual(result["platforms"]["boss"]["status"], "queued")

    def test_legacy_search_values_override_empty_platform_defaults(self):
        result = normalize_collection_options({
            "search": {"keywords": ["后端"], "cities": ["上海"]},
            "platforms": {"boss": {"search": {"keywords": [], "cities": []}}},
            "profile": {"target_cities": ["北京"]},
        })
        self.assertEqual(result["platforms"]["boss"]["keywords"], ["后端"])
        self.assertEqual(result["platforms"]["boss"]["cities"], ["上海"])

    def test_boss_filters_survive_normalization_and_invalid_values_are_removed(self):
        result = normalize_collection_options({
            "search": {
                "keywords": ["后端"],
                "cities": ["北京"],
                "filters": {
                    "job_type": ["全职"],
                    "experience": ["1-3年", "任意经验"],
                    "industry": ["100001", "bad&sortType=2"],
                },
            },
        })

        self.assertEqual(result["platforms"]["boss"]["filters"], {
            "job_type": ["全职"],
            "experience": ["1-3年"],
            "industry": ["100001"],
        })

    def test_zhilian_city_code_is_resolved_from_city_name(self):
        result = normalize_collection_options({}, {
            "platform_order": ["zhilian"],
            "auto_score": False,
            "platforms": {
                "zhilian": {
                    "keywords": ["AI"],
                    "cities": ["北京市"],
                    "max_pages": 1,
                    "sort": "default",
                },
            },
        })
        self.assertEqual(result["platforms"]["zhilian"]["city_codes"], {"北京市": "530"})

        legacy_boss_code = normalize_collection_options({}, {
            "platform_order": ["zhilian"],
            "auto_score": False,
            "platforms": {
                "zhilian": {
                    "keywords": ["AI"],
                    "cities": ["北京"],
                    "city_codes": {"北京": "101010100"},
                    "max_pages": 1,
                    "sort": "default",
                },
            },
        })
        self.assertEqual(legacy_boss_code["platforms"]["zhilian"]["city_codes"], {"北京": "530"})

    def test_disabled_zhilian_is_not_in_implicit_default_queue(self):
        result = normalize_collection_options({
            "search": {"keywords": ["后端"], "cities": ["北京"]},
            "collection": {"default_order": ["boss", "zhilian"]},
            "platforms": {"zhilian": {"enabled": False, "search": {}}},
        })
        self.assertEqual(result["platform_order"], ["boss"])


class ValidateCollectionOptionsTests(TestCase):
    """validate_collection_options 错误路径覆盖。"""

    def test_rejects_empty_platform_order(self):
        with self.assertRaises(ValueError, msg="至少选择一个采集平台"):
            normalize_collection_options({}, {"platform_order": [], "platforms": {}})

    def test_rejects_duplicate_platforms(self):
        with self.assertRaises(ValueError, msg="不能重复"):
            normalize_collection_options({}, {
                "platform_order": ["boss", "boss"],
                "platforms": {"boss": {"keywords": ["AI"], "cities": ["北京"]}},
            })

    def test_rejects_unsupported_platform(self):
        with self.assertRaises(ValueError, msg="只支持"):
            normalize_collection_options({}, {
                "platform_order": ["unknown"],
                "platforms": {"unknown": {"keywords": ["AI"], "cities": ["北京"]}},
            })

    def test_rejects_missing_keywords(self):
        with self.assertRaises(ValueError, msg="关键词"):
            normalize_collection_options({}, {
                "platform_order": ["boss"],
                "platforms": {"boss": {"keywords": [], "cities": ["北京"]}},
            })

    def test_rejects_missing_cities(self):
        with self.assertRaises(ValueError, msg="城市"):
            normalize_collection_options({}, {
                "platform_order": ["zhilian"],
                "platforms": {"zhilian": {"keywords": ["AI"], "cities": []}},
            })

    def test_rejects_invalid_max_pages(self):
        with self.assertRaises(ValueError, msg="最大页数"):
            normalize_collection_options({}, {
                "platform_order": ["boss"],
                "platforms": {"boss": {"keywords": ["AI"], "cities": ["北京"], "max_pages": 0}},
            })

    def test_rejects_max_pages_over_30(self):
        with self.assertRaises(ValueError, msg="范围"):
            normalize_collection_options({}, {
                "platform_order": ["boss"],
                "platforms": {"boss": {"keywords": ["AI"], "cities": ["北京"], "max_pages": 31}},
            })

    def test_accepts_up_to_30_pages_and_carries_intern_keywords_and_quotas(self):
        options = normalize_collection_options(
            {"automation": {"review_cap": 150, "internship_quota": 30}, "search": {"intern_keywords": ["AI实习生"]}},
            {
                "platform_order": ["boss"],
                "platforms": {"boss": {"keywords": ["AI"], "cities": ["上海"], "max_pages": 30}},
            },
        )
        boss = options["platforms"]["boss"]
        self.assertEqual(boss["max_pages"], 30)
        self.assertEqual(boss["intern_keywords"], ["AI实习生"])
        self.assertEqual(options["quotas"], {"fulltime": 120, "internship": 30})

    def test_dialog_values_override_saved_intern_keywords_and_quotas(self):
        options = normalize_collection_options(
            {"automation": {"review_cap": 150, "internship_quota": 30}, "search": {"intern_keywords": ["旧词"]}},
            {
                "platform_order": ["boss"],
                "platforms": {"boss": {"keywords": ["AI"], "cities": ["上海"], "intern_keywords": ["产品实习生"]}},
                "quotas": {"fulltime": 100, "internship": 20},
            },
        )
        self.assertEqual(options["platforms"]["boss"]["intern_keywords"], ["产品实习生"])
        self.assertEqual(options["quotas"], {"fulltime": 100, "internship": 20})

    def test_rejects_invalid_sort(self):
        with self.assertRaises(ValueError, msg="排序"):
            normalize_collection_options({}, {
                "platform_order": ["boss"],
                "platforms": {"boss": {"keywords": ["AI"], "cities": ["北京"], "sort": "invalid"}},
            })

    def test_rejects_non_bool_auto_score(self):
        with self.assertRaises(ValueError, msg="auto_score"):
            validate_collection_options({
                "platform_order": ["boss"],
                "auto_score": "yes",
                "platforms": {"boss": {"keywords": ["AI"], "cities": ["北京"]}},
            })


class CityCodeResolutionTests(TestCase):
    """51job / liepin 城市编码解析。"""

    def test_51job_city_code_resolved(self):
        result = normalize_collection_options({}, {
            "platform_order": ["51job"],
            "platforms": {"51job": {"keywords": ["AI"], "cities": ["北京"], "max_pages": 1}},
        })
        self.assertEqual(result["platforms"]["51job"]["city_codes"], {"北京": "010000"})

    def test_51job_unsupported_city_raises(self):
        with self.assertRaises(ValueError, msg="尚未支持"):
            normalize_collection_options({}, {
                "platform_order": ["51job"],
                "platforms": {"51job": {"keywords": ["AI"], "cities": ["未知城市"], "max_pages": 1}},
            })

    def test_liepin_city_code_resolved(self):
        result = normalize_collection_options({}, {
            "platform_order": ["liepin"],
            "platforms": {"liepin": {"keywords": ["AI"], "cities": ["北京"], "max_pages": 1}},
        })
        self.assertEqual(result["platforms"]["liepin"]["city_codes"]["北京"], "010")

    def test_liepin_unsupported_city_raises(self):
        with self.assertRaises(ValueError, msg="尚未支持"):
            normalize_collection_options({}, {
                "platform_order": ["liepin"],
                "platforms": {"liepin": {"keywords": ["AI"], "cities": ["未知城市"], "max_pages": 1}},
            })

    def test_zhilian_unsupported_city_raises(self):
        with self.assertRaises(ValueError, msg="暂未内置"):
            normalize_collection_options({}, {
                "platform_order": ["zhilian"],
                "platforms": {"zhilian": {"keywords": ["AI"], "cities": ["未知城市"], "max_pages": 1}},
            })


class NormalizeCollectionOptionsTests(TestCase):
    """normalize_collection_options 配置合并路径。"""

    def test_config_default_order_with_enabled_platforms(self):
        result = normalize_collection_options({
            "search": {"keywords": ["后端"], "cities": ["北京"]},
            "collection": {"default_order": ["boss", "zhilian", "51job"]},
            "platforms": {
                "zhilian": {"enabled": True, "search": {"keywords": ["AI"], "cities": ["北京"]}},
                "51job": {"enabled": True, "search": {"keywords": ["AI"], "cities": ["北京"]}},
            },
        })
        self.assertEqual(result["platform_order"], ["boss", "zhilian", "51job"])

    def test_explicit_platform_order_filters_to_selected(self):
        result = normalize_collection_options({
            "search": {"keywords": ["后端"], "cities": ["北京"]},
        }, {
            "platform_order": ["boss", "zhilian"],
            "platforms": {"boss": {"keywords": ["AI"], "cities": ["北京"]}},
        })
        self.assertEqual(result["platform_order"], ["boss"])

    def test_platform_search_merges_with_config_defaults(self):
        result = normalize_collection_options({
            "platforms": {"boss": {"search": {"keywords": ["默认词"], "cities": ["北京"]}}},
        }, {
            "platform_order": ["boss"],
            "platforms": {"boss": {"keywords": ["覆盖词"]}},
        })
        self.assertEqual(result["platforms"]["boss"]["keywords"], ["覆盖词"])
        self.assertEqual(result["platforms"]["boss"]["cities"], ["北京"])

    def test_boss_defaults_to_target_cities_when_missing(self):
        result = normalize_collection_options({
            "profile": {"target_cities": ["上海", "深圳"]},
            "search": {"keywords": ["AI"]},
        })
        self.assertEqual(result["platforms"]["boss"]["cities"], ["上海", "深圳"])

    def test_non_bool_max_pages_raises(self):
        with self.assertRaises(ValueError, msg="整数"):
            normalize_collection_options({}, {
                "platform_order": ["boss"],
                "platforms": {"boss": {"keywords": ["AI"], "cities": ["北京"], "max_pages": "abc"}},
            })


class SharedProcessorTests(TestCase):
    """_SharedProcessor inspect/save 逻辑。"""

    def test_inspect_deduplicates_existing_job(self):
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db = get_db(db_path)
            insert_job(db, {**_candidate("boss", "dup").as_job_record()})
            db.close()
            registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "dup")])})
            result = CollectionOrchestrator({}, db_path=db_path, registry=registry).run(_options())
            self.assertEqual(result["platforms"]["boss"]["duplicate"], 1)
            self.assertEqual(result["platforms"]["boss"]["new"], 0)

    def test_inspect_filters_deal_breaker_in_title(self):
        events = []
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "x", "外包岗位")])})
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(
                {"profile": {"deal_breakers": ["外包"]}},
                db_path=Path(tmp) / "test.db", registry=registry,
            ).run(_options())
        self.assertEqual(result["platforms"]["boss"]["filtered"], 1)
        self.assertEqual(result["platforms"]["boss"]["new"], 0)

    def test_inspect_filters_blocked_company(self):
        events = []
        c = _candidate("boss", "x")
        c = JobCandidate(**{**c.__dict__, "company": "黑名单公司"})
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", events, [c])})
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(
                {"profile": {"blocked_companies": ["黑名单公司"]}},
                db_path=Path(tmp) / "test.db", registry=registry,
            ).run(_options())
        self.assertEqual(result["platforms"]["boss"]["filtered"], 1)

    def test_save_filters_jd_deal_breaker(self):
        events = []
        c = _candidate("boss", "x", "正常岗位")
        c = JobCandidate(**{**c.__dict__, "jd": "包含外包关键词的JD"})
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", events, [c])})
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(
                {"profile": {"jd_deal_breakers": ["外包"]}},
                db_path=Path(tmp) / "test.db", registry=registry,
            ).run(_options())
        self.assertEqual(result["platforms"]["boss"]["filtered"], 1)
        self.assertEqual(result["platforms"]["boss"]["new"], 0)


class OrchestrationFlowTests(TestCase):
    """CollectionOrchestrator.run 编排流程。"""

    def test_collection_error_becomes_blocked_result(self):
        from bosshunter.collection.base import CollectionError

        class _ErrorCollector:
            platform = "boss"
            def collect(self, _req, _hooks):
                raise CollectionError("test_error", "test error")

        registry = CollectorRegistry({"boss": lambda: _ErrorCollector()})
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator({}, db_path=Path(tmp) / "test.db", registry=registry).run(_options())
        self.assertEqual(result["platforms"]["boss"]["status"], "blocked")
        self.assertEqual(result["platforms"]["boss"]["reason_code"], "test_error")

    def test_generic_exception_becomes_failed_result(self):
        class _CrashCollector:
            platform = "boss"
            def collect(self, _req, _hooks):
                raise RuntimeError("unexpected crash")

        registry = CollectorRegistry({"boss": lambda: _CrashCollector()})
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator({}, db_path=Path(tmp) / "test.db", registry=registry).run(_options())
        self.assertEqual(result["platforms"]["boss"]["status"], "failed")
        self.assertEqual(result["platforms"]["boss"]["reason_code"], "network_error")

    def test_empty_result_reports_shortage(self):
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", [], [])})
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator({}, db_path=Path(tmp) / "test.db", registry=registry).run(_options())
        self.assertEqual(result["status"], "completed_with_shortage")
        self.assertEqual(result["platforms"]["boss"]["reason_code"], "no_new_jobs")
        self.assertEqual(result["platforms"]["boss"]["new"], 0)

    def test_progress_callback_is_invoked(self):
        progress_calls = []
        events = []
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "new-1")])})
        config = {"_workbench_collect_progress": lambda state: progress_calls.append(state)}
        with tempfile.TemporaryDirectory() as tmp:
            CollectionOrchestrator(config, db_path=Path(tmp) / "test.db", registry=registry).run(_options())
        self.assertGreater(len(progress_calls), 0)

    def test_auto_score_failure_does_not_crash(self):
        events = []
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "new-1")])})
        with tempfile.TemporaryDirectory() as tmp:
            with patch("bosshunter.ai.scorer.score_jobs", side_effect=RuntimeError("score crash")):
                result = CollectionOrchestrator({}, db_path=Path(tmp) / "test.db", registry=registry).run(
                    _options(auto_score=True)
                )
        self.assertEqual(result["status"], "completed_with_errors")
        self.assertEqual(result["collected_job_ids"], ["new-1"])


class CrossPlatformCompanyDedupTests(TestCase):
    def _run(self, config, candidates, seed=None):
        registry = CollectorRegistry({"liepin": lambda: _FakeCollector("liepin", [], candidates)})
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            db = get_db(db_path)
            try:
                for record in seed or []:
                    insert_job(db, record)
                    if record.get("_sent"):
                        db.execute("UPDATE jobs SET status='sent' WHERE id=?", (record["id"],))
                        db.execute("INSERT INTO history (job_id, action) VALUES (?, 'sent')", (record["id"],))
                        db.commit()
            finally:
                db.close()
            result = CollectionOrchestrator(config, db_path=db_path, registry=registry).run(_options(order=["liepin"]))
            return result["platforms"]["liepin"]

    def _candidate(self, source_id, company):
        return JobCandidate(**{**_candidate("liepin", source_id, f"岗位{source_id}").__dict__, "company": company})

    def test_company_applied_on_boss_is_not_collected_again_from_liepin(self):
        boss = {**_candidate("boss", "b1").as_job_record(), "company": "商泰汽车", "_sent": True}
        state = self._run({}, [self._candidate("l1", "上海商泰汽车信息系统有限公司")], seed=[boss])
        self.assertEqual(state["new"], 0)

    def test_second_posting_of_one_company_in_the_same_run_is_skipped(self):
        state = self._run({}, [self._candidate("l1", "云从科技"), self._candidate("l2", "云从科技有限公司"), self._candidate("l3", "别家")])
        self.assertEqual(state["new"], 2)

    def test_dedup_can_be_switched_off(self):
        state = self._run({"automation": {"collect_company_dedup": False}},
                          [self._candidate("l1", "云从科技"), self._candidate("l2", "云从科技")])
        self.assertEqual(state["new"], 2)


class LocationAndUpdateAgeTests(TestCase):
    def _run(self, candidates, cities=("上海",), config=None):
        registry = CollectorRegistry({"liepin": lambda: _FakeCollector("liepin", [], candidates)})
        options = _options(order=["liepin"])
        options["platforms"]["liepin"]["cities"] = list(cities)
        with tempfile.TemporaryDirectory() as tmp:
            return CollectionOrchestrator(config or {}, db_path=Path(tmp) / "c.db", registry=registry).run(options)["platforms"]["liepin"]

    def _cand(self, source_id, city, updated_days=None):
        base = _candidate("liepin", source_id, f"岗位{source_id}").__dict__
        return JobCandidate(**{**base, "company": f"公司{source_id}乙", "city": city, "updated_days": updated_days})

    def test_postings_outside_the_searched_city_are_dropped_before_saving(self):
        state = self._run([self._cand("a", "上海-浦东新区"), self._cand("b", "苏州"), self._cand("c", "")])
        self.assertEqual(state["new"], 2)  # the city-less one is unknown, not wrong

    def test_stale_postings_are_dropped_by_update_age(self):
        state = self._run([self._cand("a", "上海", 3), self._cand("b", "上海", 90)])
        self.assertEqual(state["new"], 1)
        state = self._run([self._cand("a", "上海", 90)], config={"profile": {"max_job_update_days": 0}})
        self.assertEqual(state["new"], 1)


def test_update_label_days_and_51job_update_time():
    from bosshunter.ai.prefilter import update_label_days
    from bosshunter.collection.platforms.job51 import _updated_days

    assert update_label_days("90天前更新") == 90 and update_label_days("3小时前更新") == 3 / 24
    assert update_label_days("今天更新") == 0 and update_label_days("2周前更新") == 14 and update_label_days("x") is None
    assert _updated_days({"updateDateTime": "2000-01-01 00:00:00"}) > 9000 and _updated_days({}) is None


class InternPassOnOtherPlatformsTests(TestCase):
    def test_non_boss_platform_gets_a_second_pass_with_intern_keywords_and_internships_allowed(self):
        seen = []

        class Recorder:
            platform = "liepin"

            def __init__(self, config):
                self.config = config

            def collect(self, request, hooks):
                seen.append((list(request.keywords), bool((self.config.get("profile") or {}).get("allow_internship"))))
                return PlatformCollectionResult("liepin", "completed", "search_exhausted", "ok")

        config = {"profile": {}}
        registry = CollectorRegistry({"liepin": lambda: Recorder(config)})
        options = _options(order=["liepin"])
        options["platforms"]["liepin"]["cities"] = ["上海"]
        options["platforms"]["liepin"]["intern_keywords"] = ["AI开发实习生"]
        options["quotas"] = {"fulltime": 120, "internship": 30}
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(config, db_path=Path(tmp) / "c.db", registry=registry).run(options)
        self.assertEqual([keywords for keywords, _ in seen], [["AI"], ["AI开发实习生"]])
        self.assertIn("实习采集", result["platforms"]["liepin"]["message"])


class KeywordLimitTests(TestCase):
    def test_keyword_limit_stops_the_collector_after_that_many_new_jobs(self):
        candidates = [JobCandidate(**{**_candidate("liepin", f"k{i}", f"岗位{i}").__dict__, "company": f"独立公司{i}号"}) for i in range(6)]
        registry = CollectorRegistry({"liepin": lambda: _FakeCollector("liepin", [], candidates)})
        options = _options(order=["liepin"])
        options["platforms"]["liepin"]["cities"] = ["北京"]
        with tempfile.TemporaryDirectory() as tmp:
            state = CollectionOrchestrator({"_keyword_limit": 2}, db_path=Path(tmp) / "c.db", registry=registry).run(options)["platforms"]["liepin"]
        self.assertEqual(state["new"], 2)


class KeywordBudgetInRunsTests(TestCase):
    """Full-flow and standalone collection: one search per keyword, in tier order, each within its tier's budget."""

    def _run(self, tiers, quotas, extra_config=None):
        from bosshunter import keyword_strategy as ks

        calls = []

        class PerKeyword:
            platform = "liepin"

            def collect(self, request, hooks):
                word = request.keywords[0]
                calls.append(word)
                for index in range(10):
                    candidate = JobCandidate(**{**_candidate("liepin", f"{word}-{index}", f"{word}岗位{index}").__dict__,
                                                "company": f"{word}公司{index}号", "city": "上海"})
                    if not hooks.on_list_candidate(candidate) or not hooks.on_candidate(candidate):
                        break
                return PlatformCollectionResult("liepin", "completed", "search_exhausted", "ok")

        registry = CollectorRegistry({"liepin": PerKeyword})
        options = _options(order=["liepin"])
        options["platforms"]["liepin"].update(cities=["上海"], keywords=list(tiers))
        options["quotas"] = quotas
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "c.db"
            db = get_db(db_path)
            try:
                for word, tier in tiers.items():
                    ks.set_pinned(db, word, True, tier=tier)
            finally:
                db.close()
            config = {"automation": {"keyword_budget": True, **(extra_config or {})}}
            result = CollectionOrchestrator(config, db_path=db_path, registry=registry).run(options)
            db = get_db(db_path)
            try:
                runs = {row["keyword"]: row["new"] for row in db.execute("SELECT keyword, new FROM keyword_runs")}
            finally:
                db.close()
        return calls, runs, result

    def test_keywords_run_core_first_and_each_stays_within_its_tier_budget(self):
        calls, runs, result = self._run({"甲": "expand", "乙": "core", "丙": "explore"}, {"fulltime": 10, "internship": 0})
        self.assertEqual(calls, ["乙", "甲", "丙"])
        self.assertEqual(runs, {"乙": 10, "甲": 7, "丙": 4})                    # core 12 > expand 7 > explore 4
        self.assertEqual(result["platforms"]["liepin"]["new"], 21)
        self.assertIn("keyword_moves", result)

    def test_budget_is_off_without_the_switch(self):
        calls, runs, _ = self._run({"甲": "core", "乙": "core"}, {"fulltime": 10, "internship": 0}, {"keyword_budget": False})
        self.assertEqual(runs, {})


class RaisingCallbackStopTests(TestCase):
    """51job raises CollectionBlockedError('callback_stopped') when a keyword reaches its budget; that must end the
    keyword, not the platform and not the whole queue."""

    def test_a_keyword_that_reaches_its_budget_by_raising_does_not_block_the_platform_or_the_queue(self):
        from bosshunter.collection.base import CollectionBlockedError

        calls = []

        class Raising:
            platform = "51job"

            def collect(self, request, hooks):
                word = request.keywords[0]
                calls.append(("51job", word))
                for index in range(10):
                    candidate = JobCandidate(**{**_candidate("51job", f"{word}-{index}", f"{word}岗位{index}").__dict__,
                                                "company": f"{word}公司{index}号", "city": "上海"})
                    if not hooks.on_list_candidate(candidate):
                        continue
                    if not hooks.on_candidate(candidate):
                        raise CollectionBlockedError("callback_stopped", "采集回调已停止")
                return PlatformCollectionResult("51job", "completed", "search_exhausted", "ok")

        class Next:
            platform = "liepin"

            def collect(self, request, hooks):
                calls.append(("liepin", request.keywords[0]))
                return PlatformCollectionResult("liepin", "completed", "search_exhausted", "ok")

        registry = CollectorRegistry({"51job": Raising, "liepin": Next})
        options = _options(order=["51job", "liepin"])
        options["platforms"]["51job"].update(cities=["上海"], keywords=["甲", "乙"])
        options["platforms"]["liepin"].update(cities=["上海"], keywords=["丙"])
        options["quotas"] = {"fulltime": 10, "internship": 0}
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator({"automation": {"keyword_budget": True}}, db_path=Path(tmp) / "c.db", registry=registry).run(options)
        self.assertEqual([c for c in calls if c[0] == "51job"], [("51job", "甲"), ("51job", "乙")])   # both keywords ran
        self.assertEqual(calls[-1], ("liepin", "丙"))                                                # the queue went on
        self.assertEqual(result["platforms"]["51job"]["status"], "completed")
        self.assertEqual(result["status"], "completed")


class StaleKeywordTests(TestCase):
    """A keyword whose pages keep being mostly inactive HR is dropped and the next keyword starts."""

    def test_keyword_with_mostly_inactive_pages_is_abandoned_for_the_next_one(self):
        seen = []

        class Stale:
            platform = "51job"

            def collect(self, request, hooks):
                word = request.keywords[0]
                for index in range(40):
                    stale = word == "甲" and index % 10 < 6              # 6 of every 10 postings: inactive
                    candidate = JobCandidate(**{**_candidate("51job", f"{word}-{index}", f"{word}岗位{index}").__dict__,
                                                "company": f"{word}公司{index}号", "city": "上海",
                                                "hr_active": "3月前活跃" if stale else "刚刚活跃"})
                    seen.append((word, index))
                    if not hooks.on_list_candidate(candidate):
                        continue
                    if not hooks.on_candidate(candidate):
                        return PlatformCollectionResult("51job", "completed", "callback_stopped", "stopped")
                return PlatformCollectionResult("51job", "completed", "search_exhausted", "ok")

        registry = CollectorRegistry({"51job": Stale})
        options = _options(order=["51job"])
        options["platforms"]["51job"].update(cities=["上海"], keywords=["甲", "乙"])
        options["quotas"] = {"fulltime": 100, "internship": 0}
        config = {"automation": {"keyword_budget": True}, "profile": {"hr_active_min_level": 2}}
        with tempfile.TemporaryDirectory() as tmp:
            CollectionOrchestrator(config, db_path=Path(tmp) / "c.db", registry=registry).run(options)
        jia = [index for word, index in seen if word == "甲"]
        self.assertEqual(len(jia), 20)                                   # two stale pages, then it gave up
        self.assertGreater(len([1 for word, _ in seen if word == "乙"]), 20)   # the next keyword went on past that point


class UnconfiguredPlatformDefaultsTests(TestCase):
    def test_a_platform_without_its_own_keywords_and_cities_uses_the_ones_of_boss(self):
        config = {"platforms": {
            "boss": {"search": {"keywords": ["AI Agent开发"], "cities": ["上海"], "city_codes": {"上海": "101020100"}}},
            "zhilian": {"enabled": False, "search": {"keywords": [], "cities": [], "city_codes": {}}},
        }}
        options = normalize_collection_options(config, {"platform_order": ["zhilian"], "auto_score": True})
        self.assertEqual(options["platforms"]["zhilian"]["keywords"], ["AI Agent开发"])
        self.assertEqual(options["platforms"]["zhilian"]["cities"], ["上海"])
        self.assertTrue(options["platforms"]["zhilian"]["city_codes"].get("上海"))   # resolved from the built-in snapshot

    def test_a_platform_with_its_own_keywords_keeps_them(self):
        config = {"platforms": {
            "boss": {"search": {"keywords": ["AI Agent开发"], "cities": ["上海"]}},
            "liepin": {"search": {"keywords": ["RAG工程师"], "cities": ["上海"], "city_codes": {"上海": "020"}}},
        }}
        options = normalize_collection_options(config, {"platform_order": ["liepin"]})
        self.assertEqual(options["platforms"]["liepin"]["keywords"], ["RAG工程师"])
