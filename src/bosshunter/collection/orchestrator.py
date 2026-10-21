"""Shared filtering, atomic persistence and strictly serial collection queue."""

from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path
from threading import Event
from typing import Any, Callable
from uuid import uuid4

from bosshunter.collection.base import CollectionError, CollectorHooks
from bosshunter.collection.models import (
    CollectionProgress,
    JobCandidate,
    PlatformCollectionRequest,
    PlatformCollectionResult,
)
from bosshunter.collection.platforms.boss import BossCollector, normalize_boss_search_filters
from bosshunter.collection.platforms.job51 import Job51Collector, get_51job_city_code
from bosshunter.collection.platforms.liepin import LiepinCollector, get_liepin_city_code
from bosshunter.collection.platforms.zhilian import ZhilianCollector, get_zhilian_city_code
from bosshunter.collection.registry import CollectorRegistry
from bosshunter.collection_run_store import (
    boss_combo_key, boss_resume_options, claim_boss_resume, create_collection_run,
    save_boss_checkpoint, update_collection_run,
)
from bosshunter.ai.prefilter import freshness_filter_result
from bosshunter.employment import internship_search_config
from bosshunter.company_dedup import approved_within, cooldown_days, matching_company, pool_companies
from bosshunter.rejected_companies import is_rejected_company, rejected_names
from bosshunter.db import (
    find_job_id, get_db, insert_job_if_new, job_identity_exists, record_sighting, recompute_outsourcing,
)
from bosshunter.employment import internship_config_error
from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker
from bosshunter.outsourcing import load_rules


SUPPORTED_PLATFORMS = {"boss", "zhilian", "51job", "liepin"}
SORT_OPTIONS = {
    "boss": {"default", "newest"},
    "zhilian": {"default", "newest"},
    "51job": {"default", "newest", "active"},
    "liepin": {"default", "newest"},
}


def _clean_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def normalize_collection_options(config: dict[str, Any], raw_options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a validated collection request while keeping legacy BOSS config compatible."""
    supplied = deepcopy(raw_options) if isinstance(raw_options, dict) else {}
    raw_platforms = supplied.get("platforms") if isinstance(supplied.get("platforms"), dict) else {}
    configured_platforms = config.get("platforms") if isinstance(config.get("platforms"), dict) else {}
    legacy_search = config.get("search") if isinstance(config.get("search"), dict) else {}
    boss_search = configured_platforms.get("boss", {}).get("search", {}) if isinstance(configured_platforms.get("boss"), dict) else {}
    if not boss_search:
        boss_search = legacy_search
    elif isinstance(legacy_search, dict):
        # ``load_config`` supplies platform defaults even for old config.yaml
        # files. Non-empty legacy search values must still win over those empty
        # defaults, while explicit platform values remain authoritative.
        boss_search = dict(boss_search)
        for key, value in legacy_search.items():
            if value not in (None, "", [], {}):
                if boss_search.get(key) in (None, "", [], {}):
                    boss_search[key] = value
    if (
        isinstance(legacy_search, dict)
        and not raw_platforms.get("boss")
        and not (boss_search.get("keywords") or boss_search.get("cities"))
        and (legacy_search.get("keywords") or legacy_search.get("cities"))
    ):
        boss_search = dict(legacy_search)
    zhilian_search = configured_platforms.get("zhilian", {}).get("search", {}) if isinstance(configured_platforms.get("zhilian"), dict) else {}
    job51_search = configured_platforms.get("51job", {}).get("search", {}) if isinstance(configured_platforms.get("51job"), dict) else {}
    liepin_search = configured_platforms.get("liepin", {}).get("search", {}) if isinstance(configured_platforms.get("liepin"), dict) else {}

    platforms: dict[str, Any] = {}
    for platform, fallback in (("boss", boss_search), ("zhilian", zhilian_search), ("51job", job51_search), ("liepin", liepin_search)):
        value = raw_platforms.get(platform) if isinstance(raw_platforms.get(platform), dict) else {}
        search = value.get("search") if isinstance(value.get("search"), dict) else value
        if not isinstance(search, dict):
            search = {}
        base = dict(fallback) if isinstance(fallback, dict) else {}
        base.update(search)
        if platform == "boss" and not base.get("cities"):
            base["cities"] = config.get("profile", {}).get("target_cities", ["北京"])
        platforms[platform] = {
            "keywords": _clean_strings(base.get("keywords")),
            "cities": _clean_strings(base.get("cities")),
            "city_codes": {
                str(key).strip(): str(value).strip()
                for key, value in (base.get("city_codes") or {}).items()
                if str(key).strip() and str(value).strip()
            } if isinstance(base.get("city_codes"), dict) else {},
            "max_pages": base.get("max_pages", 3 if platform == "boss" else 1),
            "sort": str(base.get("sort") or ("newest" if platform == "boss" else "active" if platform == "51job" else "default")),
            "filters": normalize_boss_search_filters(base.get("filters")) if platform == "boss" else {},
        }
        # Internship words: the dialog's own value wins, otherwise the saved config search.intern_keywords.
        intern_source = search.get("intern_keywords") if "intern_keywords" in search else legacy_search.get("intern_keywords")
        platforms[platform]["intern_keywords"] = _clean_strings(intern_source)

    order = supplied.get("platform_order")
    if order is None:
        configured_order = config.get("collection", {}).get("default_order") if isinstance(config.get("collection"), dict) else None
        order = configured_order if isinstance(configured_order, list) else ["boss"]
        if not supplied:
            enabled_platforms = {"boss"}
            for platform, value in configured_platforms.items():
                if isinstance(value, dict) and value.get("enabled"):
                    enabled_platforms.add(str(platform))
            order = [platform for platform in order if platform in enabled_platforms]
            if not order:
                order = ["boss"]
    order = [str(value).strip() for value in order if str(value).strip()] if isinstance(order, list) else []
    selected_platforms = supplied.get("platforms") if isinstance(supplied.get("platforms"), dict) else None
    if supplied and selected_platforms is not None:
        selected = {str(key).strip() for key in selected_platforms if str(key).strip()}
        order = [platform for platform in order if platform in selected]
    automation = config.get("automation") if isinstance(config.get("automation"), dict) else {}
    try:
        configured_intern = max(int(automation.get("internship_quota", 0) or 0), 0)
        configured_total = max(int(automation.get("review_cap", 150) or 150), 1)
    except (TypeError, ValueError):
        configured_intern, configured_total = 0, 150
    quota_source = supplied.get("quotas") if isinstance(supplied.get("quotas"), dict) else {}
    options = {
        "platform_order": order,
        "auto_score": supplied.get("auto_score", False) is True,
        "platforms": {platform: platforms[platform] for platform in order if platform in platforms},
        "quotas": {
            "fulltime": quota_source.get("fulltime", max(configured_total - configured_intern, 0)),
            "internship": quota_source.get("internship", configured_intern),
        },
    }
    if supplied.get("resume_run_id"):
        options["resume_run_id"] = supplied["resume_run_id"]
    normalized = validate_collection_options(options)
    if "boss" in normalized["platform_order"]:
        effective = {"profile": config.get("profile", {}), "platforms": {
            "boss": {"search": {"filters": normalized["platforms"]["boss"]["filters"]}},
        }}
        if conflict := internship_config_error(effective):
            raise ValueError(conflict)
    return normalized


def validate_collection_options(options: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(options, dict):
        raise ValueError("采集参数必须是对象")
    order = options.get("platform_order")
    platforms = options.get("platforms")
    if not isinstance(order, list) or not order:
        raise ValueError("至少选择一个采集平台")
    order = [str(value).strip() for value in order]
    if len(order) != len(set(order)):
        raise ValueError("采集平台顺序不能重复")
    if any(platform not in SUPPORTED_PLATFORMS for platform in order):
        raise ValueError("采集平台只支持 boss、zhilian、51job 或 liepin")
    if not isinstance(platforms, dict) or set(platforms) != set(order):
        raise ValueError("平台顺序与平台配置不一致")
    if not isinstance(options.get("auto_score", False), bool):
        raise ValueError("auto_score 必须是布尔值")
    normalized: dict[str, Any] = {"platform_order": order, "auto_score": bool(options.get("auto_score")), "platforms": {}}
    if options.get("resume_run_id"):
        if not isinstance(options["resume_run_id"], str) or order != ["boss"]:
            raise ValueError("继续采集必须选择原来的 BOSS 任务")
        normalized["resume_run_id"] = options["resume_run_id"]
    for platform in order:
        value = platforms.get(platform)
        if not isinstance(value, dict):
            raise ValueError(f"{platform} 平台配置无效")
        keywords = _clean_strings(value.get("keywords"))
        cities = _clean_strings(value.get("cities"))
        if not keywords:
            raise ValueError(f"{platform} 至少需要一个非空关键词")
        if not cities:
            raise ValueError(f"{platform} 至少需要一个城市")
        city_codes = value.get("city_codes") if isinstance(value.get("city_codes"), dict) else {}
        city_codes = {str(key).strip(): str(code).strip() for key, code in city_codes.items() if str(key).strip()}
        if platform == "zhilian":
            for city in cities:
                resolved = get_zhilian_city_code(city)
                if resolved:
                    # The platform snapshot is authoritative for known cities;
                    # this prevents a legacy BOSS code from crossing platforms.
                    city_codes[city] = resolved
            missing_cities = [city for city in cities if not city_codes.get(city)]
            if missing_cities:
                names = "、".join(missing_cities)
                raise ValueError(f"智联暂未内置城市编码：{names}；请换用采集窗口提供的城市名称，不能填写 BOSS 编码")
        if platform == "51job":
            unsupported_cities: list[str] = []
            for city in cities:
                resolved = get_51job_city_code(city)
                if resolved:
                    city_codes[city] = resolved
                else:
                    unsupported_cities.append(city)
            if unsupported_cities:
                names = "、".join(unsupported_cities)
                raise ValueError(f"51job 当前只开放已验证城市：{names} 尚未支持；不会猜测城市编码")
        if platform == "liepin":
            unsupported_cities = []
            for city in cities:
                resolved = get_liepin_city_code(city)
                if resolved:
                    city_codes[city] = resolved
                else:
                    unsupported_cities.append(city)
            if unsupported_cities:
                names = "、".join(unsupported_cities)
                raise ValueError(f"猎聘当前只开放已验证城市：{names} 尚未支持；不会猜测城市编码")
        try:
            max_pages = int(value.get("max_pages", 3))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{platform} 最大页数必须是整数") from exc
        if not 1 <= max_pages <= MAX_PAGES_LIMIT:
            raise ValueError(f"{platform} 最大页数范围为 1-{MAX_PAGES_LIMIT}")
        sort = str(value.get("sort") or "default").strip()
        if sort not in SORT_OPTIONS[platform]:
            raise ValueError(f"{platform} 排序方式无效")
        normalized["platforms"][platform] = {
            "keywords": keywords,
            "cities": cities,
            "city_codes": city_codes,
            "max_pages": max_pages,
            "sort": sort,
            "filters": normalize_boss_search_filters(value.get("filters")) if platform == "boss" else {},
        }
        normalized["platforms"][platform]["intern_keywords"] = _clean_strings(value.get("intern_keywords"))
    quotas = options.get("quotas") if isinstance(options.get("quotas"), dict) else {}
    parsed_quotas = {}
    for kind in ("fulltime", "internship"):
        try:
            amount = int(quotas.get(kind, 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("全职和实习名额必须是整数") from exc
        if not 0 <= amount <= 1000:
            raise ValueError("全职和实习名额范围为 0-1000")
        parsed_quotas[kind] = amount
    normalized["quotas"] = parsed_quotas
    return normalized


MAX_PAGES_LIMIT = 30


class _SharedProcessor:
    def __init__(
        self,
        conn,
        request: PlatformCollectionRequest,
        *,
        run_id: str,
        platform_index: int,
        platform_total: int,
        stop_event: Event | None,
        config: dict[str, Any],
        emit: Callable[[CollectionProgress], None],
    ):
        self.conn = conn
        self.request = request
        self.run_id = run_id
        self.platform_index = platform_index
        self.platform_total = platform_total
        self.stop_event = stop_event
        self.config = config
        self.emit = emit
        self.outsourcing_rules = load_rules(config)
        recompute_outsourcing(conn, self.outsourcing_rules)
        self.rejected_companies = rejected_names(conn)
        try:
            self.cap_total: int | None = int(config.get("_keyword_limit") or 0) or None
        except (TypeError, ValueError):
            self.cap_total = None
        # Cross-platform dedup: a company with a job already in the pool, or applied to within the
        # cooldown window on ANY platform, is not collected again.
        automation = config.get("automation") if isinstance(config.get("automation"), dict) else {}
        self.company_dedup = automation.get("collect_company_dedup", True) is not False
        self.pool_companies = pool_companies(conn) if self.company_dedup else []
        self.recent_companies = (
            [item["company"] for item in approved_within(conn, cooldown_days(config))] if self.company_dedup else []
        )
        self.progress = CollectionProgress(
            run_id=run_id, platform=request.platform, platform_index=platform_index,
            platform_total=platform_total, phase="queued", target=None,
            max_pages=request.max_pages,
        )
        self.new_job_ids: list[str] = []
        self.filter_records: list[dict[str, str]] = []
        self.error_records: list[dict[str, Any]] = []
        self.stale_block = int(automation.get("stale_block_size", 10) or 10)
        self.stale_ratio = float(automation.get("stale_ratio", 0.6) or 0.6)
        self.stale_strikes_limit = int(automation.get("stale_strikes", 2) or 2)
        self.abandoned_keyword = False
        self.reset_keyword_window()

    def reset_keyword_window(self) -> None:
        """Start counting stale postings afresh for the next keyword."""
        self._stale_samples: list[bool] = []
        self._stale_strikes = 0
        self.abandoned_keyword = False

    def _note_staleness(self, candidate: JobCandidate, stale: bool) -> None:
        """Give up on a keyword whose results keep being stale.

        Postings are judged in blocks of about one page; a block with at least ``stale_ratio`` stale postings is a
        strike, a good block clears the strikes, and ``stale_strikes`` in a row end the keyword (the search order
        puts the freshest first, so the following pages are only going to be worse).
        """
        self._stale_samples.append(stale)
        if len(self._stale_samples) < self.stale_block:
            return
        block, self._stale_samples = self._stale_samples, []
        if sum(block) / len(block) >= self.stale_ratio:
            self._stale_strikes += 1
            if self._stale_strikes >= self.stale_strikes_limit:
                self.abandoned_keyword = True
                self.event(message=f"关键词「{self.progress.keyword}」连续 {self._stale_strikes} 页不活跃岗位占 "
                                   f"{sum(block)}/{len(block)} 以上，放弃该词，换下一个")
        else:
            self._stale_strikes = 0

    def event(self, *, phase: str | None = None, **values: Any) -> None:
        if phase:
            self.progress.phase = phase
        candidate = values.pop("filtered_candidate", None)
        if values.pop("increment_filtered", False):
            self.progress.filtered += 1
            if isinstance(candidate, JobCandidate):
                self.filter_records.append({
                    "title": candidate.title[:300], "company": candidate.company[:300],
                    "source_job_id": candidate.source_job_id[:200],
                    "reason": str(values.get("message") or "命中过滤规则")[:500],
                })
                self.filter_records = self.filter_records[-200:]
        for key, value in values.items():
            if hasattr(self.progress, key):
                setattr(self.progress, key, value)
        self.emit(self.progress)

    def record_failure(self, kind: str, reason: str, candidate: JobCandidate | None = None) -> None:
        self.error_records.append({
            "kind": kind, "reason": reason[:500],
            "title": candidate.title[:300] if candidate else "",
            "company": candidate.company[:300] if candidate else "",
            "city": self.progress.city, "keyword": self.progress.keyword,
            "page": self.progress.page,
        })
        self.error_records = self.error_records[-200:]

    def inspect(self, candidate: JobCandidate) -> bool:
        self.progress.seen += 1
        if job_identity_exists(
            self.conn,
            candidate.platform,
            candidate.source_job_id,
            legacy_job_id=candidate.storage_id,
        ):
            # Still listed today: remember the sighting so long-running postings can be spotted.
            known_id = find_job_id(
                self.conn, candidate.platform, candidate.source_job_id, candidate.storage_id
            )
            if known_id:
                record_sighting(self.conn, known_id)
            self.progress.duplicate += 1
            self.event()
            return False
        profile = self.config.get("profile", {}) if isinstance(self.config.get("profile"), dict) else {}
        if reason := self._city_mismatch(candidate):
            self.event(message=reason, increment_filtered=True, filtered_candidate=candidate)
            return False
        if matched := matching_deal_breaker(candidate.title, profile.get("deal_breakers") or []):
            self.event(message=f"职位名命中排除词：{matched}", increment_filtered=True, filtered_candidate=candidate)
            return False
        if matched := matching_blocked_company(candidate.company, profile.get("blocked_companies") or []):
            self.event(message=f"公司命中屏蔽规则：{matched}", increment_filtered=True, filtered_candidate=candidate)
            return False
        if matched := is_rejected_company(candidate.company, self.rejected_companies):
            self.event(message=f"公司已拒绝过：{matched}", increment_filtered=True, filtered_candidate=candidate)
            return False
        if self.company_dedup and (matched := matching_company(candidate.company, self.recent_companies)):
            self.event(message=f"公司近期已投递过（任一平台）：{matched}", increment_filtered=True, filtered_candidate=candidate)
            return False
        if self.company_dedup and (matched := matching_company(candidate.company, self.pool_companies)):
            self.event(message=f"公司已有岗位在岗位池中：{matched}", increment_filtered=True, filtered_candidate=candidate)
            return False
        self.event()
        return True

    def _city_mismatch(self, candidate: JobCandidate) -> str:
        """Reason when a non-BOSS posting is located outside the searched cities ('' = fine or unknown)."""
        wanted = [str(c).strip().removesuffix("市") for c in self.request.cities if str(c).strip()]
        where = str(candidate.city or "").strip()
        if candidate.platform == "boss" or not wanted or not where:
            return ""
        if any(city in where for city in wanted):
            return ""
        return f"工作地点不在目标城市：{where}（目标：{'、'.join(wanted)}）"

    def save(self, candidate: JobCandidate) -> bool:
        profile = self.config.get("profile", {}) if isinstance(self.config.get("profile"), dict) else {}
        if matched := matching_deal_breaker(candidate.jd, profile.get("jd_deal_breakers") or []):
            self.event(message=f"JD 命中排除词：{matched}", increment_filtered=True, filtered_candidate=candidate)
            return True
        if reason := self._city_mismatch(candidate):
            self.event(message=reason, increment_filtered=True, filtered_candidate=candidate)
            return True
        fresh_score, fresh_reason = freshness_filter_result(
            {"hr_active": candidate.hr_active, "updated_days": candidate.updated_days}, profile
        )
        self._note_staleness(candidate, fresh_score <= 0)
        if fresh_score <= 0:
            self.event(message=fresh_reason, increment_filtered=True, filtered_candidate=candidate)
            return not self.abandoned_keyword
        try:
            inserted = insert_job_if_new(self.conn, candidate.as_job_record(), rules=self.outsourcing_rules)
        except Exception as exc:
            self.progress.save_failed += 1
            reason = f"保存岗位失败：{type(exc).__name__}"
            self.record_failure("save", reason, candidate)
            self.event(message=reason)
            return True
        if inserted:
            self.new_job_ids.append(candidate.storage_id)
            self.pool_companies.append(candidate.company)
            record_sighting(self.conn, candidate.storage_id)
        else:
            self.progress.duplicate += 1
        self.event(phase="saving")
        if self.stop_event is not None and self.stop_event.is_set():
            return False
        if self.cap_total is not None and len(self.new_job_ids) >= self.cap_total:
            return False
        if self.abandoned_keyword:
            return False
        return self.stop_event is None or not self.stop_event.is_set()


class CollectionOrchestrator:
    """Run selected platform collectors one after another in the given order."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        db_path: Path | None = None,
        registry: CollectorRegistry | None = None,
        run_id: str | None = None,
        task_id: str = "",
    ):
        self.config = config
        self.db_path = db_path or Path("./data/bosshunter.db")
        self._uses_default_registry = registry is None
        self.registry = registry or CollectorRegistry({
            "boss": BossCollector,
            "zhilian": ZhilianCollector,
            "51job": Job51Collector,
            "liepin": LiepinCollector,
        })
        self.run_id = run_id or str(uuid4())
        self.task_id = task_id
        self.stop_event = config.get("_workbench_stop_event")

    def run(self, raw_options: dict[str, Any] | None = None) -> dict[str, Any]:
        resume_id = raw_options.get("resume_run_id") if isinstance(raw_options, dict) else None
        if resume_id:
            # The stored search is authoritative, including filters and page limits.
            raw_options = boss_resume_options(self.db_path, str(resume_id))
        options = normalize_collection_options(self.config, raw_options)
        order = options["platform_order"]
        automation = self.config.get("automation") if isinstance(self.config.get("automation"), dict) else {}
        self._budget = automation.get("keyword_budget") is True and not resume_id
        self._budget_plan = None
        if self._budget:
            try:
                from bosshunter.ai.keywords import load_plan

                self._budget_plan = load_plan(self.db_path.parent.parent)
            except Exception:
                self._budget_plan = None
        self._platform_count = len(order)
        states: dict[str, dict[str, Any]] = {
            platform: {"status": "queued", "new": 0, "target": None, "percent": None}
            for platform in order
        }
        previous = None
        if resume_id:
            self.run_id = str(resume_id)
            previous = claim_boss_resume(self.db_path, self.run_id, self.task_id)
            states = deepcopy(previous["platform_states"])
            states.setdefault("boss", {}).update(status="queued", reason_code="", message="继续原任务")
        else:
            create_collection_run(
                self.db_path, run_id=self.run_id, task_id=self.task_id, options=options,
                platform_states=states, enable_boss_resume=order == ["boss"],
            )
        checkpoint_pages = dict(previous["boss_checkpoint"]["pages"]) if previous else {}
        all_new_ids: list[str] = list(previous["collected_job_ids"]) if previous else []
        platform_results: list[PlatformCollectionResult] = []
        conn = get_db(self.db_path)

        def checkpoint(city: str, keyword: str, page: int) -> None:
            checkpoint_pages[boss_combo_key(city, keyword)] = page
            save_boss_checkpoint(conn, self.run_id, checkpoint_pages)

        try:
            for index, platform in enumerate(order, start=1):
                if self.stop_event is not None and self.stop_event.is_set():
                    states[platform]["status"] = "stopped"
                    states[platform]["reason_code"] = "user_stopped"
                    break
                raw = dict(options["platforms"][platform])
                intern_words = list(raw.pop("intern_keywords", []) or [])
                request = PlatformCollectionRequest(platform=platform, **raw)
                states[platform]["status"] = "running"
                self._persist(states, all_new_ids, platform, status="running")
                processor = _SharedProcessor(
                    conn, request, run_id=self.run_id, platform_index=index, platform_total=len(order),
                    stop_event=self.stop_event, config=self.config,
                    emit=lambda progress, p=platform, processor_ref=None: self._emit(
                        states, p, progress, all_new_ids, processor_ref.new_job_ids if processor_ref else []
                    ),
                )
                if previous:
                    processor.new_job_ids = list(previous["collected_job_ids"])
                    processor.filter_records = list(previous["platform_states"].get(platform, {}).get("filter_records") or [])[-200:]
                    processor.error_records = list(previous["platform_states"].get(platform, {}).get("error_records") or [])[-200:]
                    for name in ("seen", "duplicate", "filtered", "parse_failed", "save_failed"):
                        setattr(processor.progress, name, int(previous["platform_states"].get(platform, {}).get(name) or 0))
                prior_save_failures = processor.progress.save_failed
                # Bind the processor into the callback after construction so the
                # current platform's progress is not confused with prior IDs.
                def emit(progress, p=platform, processor_ref=processor):
                    states[p]["filter_records"] = list(processor_ref.filter_records)
                    states[p]["error_records"] = list(processor_ref.error_records)
                    self._emit(states, p, progress, all_new_ids, processor_ref.new_job_ids)
                processor.emit = emit
                hooks = CollectorHooks(
                    stop_event=self.stop_event,
                    on_list_candidate=processor.inspect,
                    on_candidate=processor.save,
                    on_parse_failed=lambda reason, p=processor: self._parse_failed(p, reason),
                    on_event=lambda p=processor, **kwargs: p.event(**kwargs),
                    can_checkpoint=lambda p=processor, baseline=prior_save_failures: p.progress.save_failed == baseline,
                    completed_page=(lambda city, keyword: checkpoint_pages.get(boss_combo_key(city, keyword), 0)),
                    on_page_complete=checkpoint if order == ["boss"] else lambda *_: None,
                )
                try:
                    collector = (
                        BossCollector(config=self.config, safety_conn=conn)
                        if platform == "boss" and self._uses_default_registry
                        else Job51Collector(config=self.config, safety_conn=conn)
                        if platform == "51job" and self._uses_default_registry
                        else ZhilianCollector(config=self.config, safety_conn=conn)
                        if platform == "zhilian" and self._uses_default_registry
                        else LiepinCollector(config=self.config, safety_conn=conn)
                        if platform == "liepin" and self._uses_default_registry
                        else self.registry.get(platform)
                    )
                    ok_statuses = {"completed", "completed_with_shortage"}
                    quotas = options.get("quotas") or {}
                    if self._budget and request.keywords:
                        result = self._collect_with_budget(
                            collector, request, processor, hooks, conn, int(quotas.get("fulltime", 0) or 0),
                            "fulltime", intern=False,
                        )
                    else:
                        result = collector.collect(request, hooks)
                    if (
                        intern_words and int(quotas.get("internship", 0) or 0) > 0
                        and result.status in ok_statuses
                        and not (self.stop_event is not None and self.stop_event.is_set())
                    ):
                        intern_result = self._collect_internships(
                            request, raw, intern_words, quotas, processor, hooks, conn
                        )
                        if intern_result.status not in ok_statuses:
                            result = intern_result
                        else:
                            result.message += f"；实习采集：{intern_result.message}"
                except CollectionError as exc:
                    result = PlatformCollectionResult(platform, "blocked", exc.code, exc.message, error=str(exc))
                except Exception as exc:
                    result = PlatformCollectionResult(platform, "failed", "network_error", f"{platform} 采集失败", error=str(exc)[:500])
                result.new_job_ids = list(processor.new_job_ids)
                result.counts = self._counts(processor.progress)
                if platform == "boss" and result.status in {"completed", "completed_with_shortage"}:
                    if result.status == "completed" and not result.new_job_ids:
                        result.status = "completed_with_shortage"
                        result.reason_code = "no_new_jobs"
                    result.message += (
                        f"；本轮新增 {len(result.new_job_ids)} 条，读取 {processor.progress.seen} 条，"
                        f"重复 {processor.progress.duplicate} 条，过滤 {processor.progress.filtered} 条，"
                        f"解析失败 {processor.progress.parse_failed} 条，保存失败 {processor.progress.save_failed} 条"
                    )
                platform_results.append(result)
                states[platform].update({
                    "status": result.status,
                    "new": len(result.new_job_ids),
                    "percent": processor.progress.percent,
                    "seen": processor.progress.seen,
                    "duplicate": processor.progress.duplicate,
                    "filtered": processor.progress.filtered,
                    "parse_failed": processor.progress.parse_failed,
                    "save_failed": processor.progress.save_failed,
                    "keyword": processor.progress.keyword,
                    "city": processor.progress.city,
                    "page": processor.progress.page,
                    "max_pages": processor.progress.max_pages,
                    "reason_code": result.reason_code,
                    "message": result.message,
                })
                all_new_ids.extend(result.new_job_ids)
                self._persist(states, all_new_ids, platform, stop_reason=result.reason_code, error=result.error)
                # A login wall belongs to the current recruitment platform:
                # keep its blocked result but let independent later platforms
                # continue. Other unknown/risk blocks remain queue-wide until
                # they have an equally explicit platform-local classification.
                if (
                    (result.status == "blocked" and result.reason_code != "login_required")
                    or result.reason_code in {"user_stopped", "browser_disconnected"}
                    or (self.stop_event and self.stop_event.is_set())
                ):
                    break
            if self._budget:
                self.keyword_moves = self._adjust_tiers(conn, options)
        finally:
            conn.close()

        unique_new_ids = list(dict.fromkeys(str(job_id) for job_id in all_new_ids if str(job_id)))
        stopped = bool(self.stop_event and self.stop_event.is_set()) or any(r.reason_code == "user_stopped" for r in platform_results)
        errors = any(r.status in {"blocked", "failed"} for r in platform_results)
        shortages = any(r.status == "completed_with_shortage" for r in platform_results)
        outcome = "stopped" if stopped else "completed_with_errors" if errors else "completed_with_shortage" if shortages else "completed"
        if options["auto_score"] and unique_new_ids and not stopped:
            try:
                self._emit_scoring(states, unique_new_ids)
                from bosshunter.ai.scorer import score_jobs

                score_config = dict(self.config)
                score_config["_workbench_stop_event"] = self.stop_event
                score_jobs(score_config, scope="selected", job_ids=unique_new_ids, limit=None, force_rescore=False)
            except Exception as exc:
                outcome = "completed_with_errors"
                self._persist(states, unique_new_ids, "", error=f"自动评分失败：{str(exc)[:500]}")
        self._persist(states, unique_new_ids, "", status=outcome, stop_reason="user_stopped" if stopped else "")
        return {
            "run_id": self.run_id,
            "status": outcome,
            "platforms": states,
            "collected_job_ids": unique_new_ids,
            "results": [result.__dict__ for result in platform_results],
            "keyword_moves": list(getattr(self, "keyword_moves", [])),
        }

    def _collect_with_budget(self, collector, request, processor, hooks, conn, quota, kind, *, intern, words=None):
        """One collect() per keyword, in tier order, each capped by its share of the platform's budget.

        Full-time words get core > expand > explore caps; paused words are skipped (unless every word is paused);
        results of every keyword are recorded so tiers can move. Internship words share the budget evenly.
        """
        from dataclasses import replace

        from bosshunter import keyword_strategy as ks

        words = list(words if words is not None else request.keywords)
        raw = math.ceil(max(quota, 0) * 2 / 0.9)            # about half survive scoring; 10% is reserve
        share = ks.platform_share(raw, getattr(self, "_platform_count", 1))
        if intern:
            tiers = {"core": words, "expand": [], "explore": [], "paused": []}
        else:
            tiers = ks.current_tiers(conn, words, getattr(self, "_budget_plan", None))
            if not any(tiers[t] for t in ks.TIERS):
                tiers = {"core": words, "expand": [], "explore": [], "paused": []}
        caps = ks.first_pass_caps(tiers, share)
        ordered = [word for tier in ks.TIERS for word in tiers[tier]]
        results: list[PlatformCollectionResult] = []
        ok = {"completed", "completed_with_shortage"}
        for word in ordered:
            if self.stop_event is not None and self.stop_event.is_set():
                break
            before, seen0, filtered0 = len(processor.new_job_ids), processor.progress.seen, processor.progress.filtered
            processor.cap_total = before + caps.get(word, ks.MIN_SLICE)
            processor.reset_keyword_window()
            try:
                result = collector.collect(replace(request, keywords=[word]), hooks)
            except CollectionError as exc:
                if exc.code != "callback_stopped":
                    raise
                result = PlatformCollectionResult(request.platform, "completed", "callback_stopped", "采集回调已停止")
            processor.cap_total = None
            ks.record_run(
                conn, request.platform, word, kind,
                seen=processor.progress.seen - seen0, new=len(processor.new_job_ids) - before,
                filtered=processor.progress.filtered - filtered0,
            )
            results.append(result)
            if result.status not in ok and result.reason_code != "callback_stopped":
                return result                                   # a safety stop ends the whole platform
        if not results:
            return PlatformCollectionResult(request.platform, "completed", "search_exhausted", "没有可采集的关键词")
        merged = results[-1]
        shortage = any(r.status == "completed_with_shortage" for r in results)
        merged.status = "completed_with_shortage" if shortage else "completed"
        if merged.reason_code == "callback_stopped":
            merged.reason_code = "search_exhausted"
        merged.message = f"按关键词预算采集 {len(results)} 个词：" + "；".join(r.message for r in results if r.message)[:400]
        return merged

    def _adjust_tiers(self, conn, options) -> list[dict]:
        """Move keywords between tiers from their recent results, once per run."""
        from bosshunter import keyword_strategy as ks

        words: list[str] = []
        for platform in options["platform_order"]:
            words.extend(options["platforms"][platform].get("keywords") or [])
        try:
            return ks.adjust_tiers(conn, list(dict.fromkeys(words)), getattr(self, "_budget_plan", None))
        except Exception:
            return []

    def _collect_internships(self, request, raw, intern_words, quotas, processor, hooks, conn):
        """Second pass of one platform: the internship keywords with internships accepted, capped near the quota.

        BOSS also switches to its internship-only job-type filter; the other platforms search the keywords as typed.
        """
        platform = request.platform
        if platform == "boss":
            intern_config = internship_search_config(self.config)
            intern_request = PlatformCollectionRequest(
                platform="boss", **{**raw, "keywords": intern_words, "filters": {"job_type": ["实习"]}}
            )
        else:
            profile = {**(self.config.get("profile") or {}), "allow_internship": True}
            intern_config = {**self.config, "profile": profile}
            intern_request = PlatformCollectionRequest(platform=platform, **{**raw, "keywords": intern_words})
        # About half of the collected jobs survive scoring, so collect roughly twice the quota.
        target = int(quotas.get("internship", 0)) * 2
        before = len(processor.new_job_ids)
        processor.event(phase="loading_list", message="开始采集实习岗位")

        def limited_save(candidate):
            keep = processor.save(candidate)
            return keep and (target <= 0 or len(processor.new_job_ids) - before < target)

        intern_hooks = CollectorHooks(
            stop_event=self.stop_event,
            on_list_candidate=processor.inspect,
            on_candidate=limited_save,
            on_parse_failed=hooks.on_parse_failed,
            on_event=hooks.on_event,
        )
        classes = {"boss": BossCollector, "51job": Job51Collector, "zhilian": ZhilianCollector, "liepin": LiepinCollector}
        collector = (
            classes[platform](config=intern_config, safety_conn=conn)
            if self._uses_default_registry
            else self.registry.get(platform)
        )
        try:
            if getattr(self, "_budget", False):
                return self._collect_with_budget(
                    collector, intern_request, processor, intern_hooks, conn,
                    int(quotas.get("internship", 0) or 0), "internship", intern=True, words=intern_words,
                )
            return collector.collect(intern_request, intern_hooks)
        except CollectionError as exc:
            if exc.code == "callback_stopped":            # the internship cap was reached: the pass is done
                return PlatformCollectionResult(platform, "completed", "callback_stopped", "实习名额已采满")
            return PlatformCollectionResult(platform, "blocked", exc.code, exc.message, error=str(exc))
        except Exception as exc:
            return PlatformCollectionResult(platform, "failed", "network_error", f"{platform} 实习采集失败", error=str(exc)[:500])

    @staticmethod
    def _counts(progress: CollectionProgress) -> dict[str, int]:
        return {
            "seen": progress.seen, "new": progress.new, "duplicate": progress.duplicate,
            "filtered": progress.filtered, "parse_failed": progress.parse_failed, "save_failed": progress.save_failed,
        }

    def _parse_failed(self, processor: _SharedProcessor, reason: str) -> None:
        processor.progress.parse_failed += 1
        processor.record_failure("parse", reason)
        processor.event(phase="loading_detail", message=reason)

    def _emit(
        self,
        states: dict[str, dict[str, Any]],
        platform: str,
        progress: CollectionProgress,
        all_new_ids: list[str],
        platform_new_ids: list[str],
    ) -> None:
        progress.new = len(platform_new_ids) if progress.platform == platform else progress.new
        states[platform].update({
            "status": "running", "new": progress.new, "target": progress.target, "percent": progress.percent,
            "seen": progress.seen, "duplicate": progress.duplicate, "filtered": progress.filtered,
            "parse_failed": progress.parse_failed, "save_failed": progress.save_failed,
            "keyword": progress.keyword, "city": progress.city, "page": progress.page,
            "max_pages": progress.max_pages, "phase": progress.phase, "reason_code": progress.reason_code,
            "message": progress.message,
        })
        callback = self.config.get("_workbench_collect_progress")
        state = {
            **self._counts(progress), "progress": {
                "run_id": self.run_id, "outcome": "running", "current_platform": platform,
                "platform_index": progress.platform_index, "platform_total": progress.platform_total,
                "platforms": deepcopy(states),
            },
        }
        if callable(callback):
            callback(state)
        self._persist(states, [*all_new_ids, *platform_new_ids], platform)

    def _emit_scoring(self, states: dict[str, dict[str, Any]], new_ids: list[str]) -> None:
        log = self.config.get("_workbench_log")
        if callable(log):
            log(f"开始 AI 评分：本轮采集已结束，处理 {len(new_ids)} 个新增岗位")
        callback = self.config.get("_workbench_collect_progress")
        if callable(callback):
            callback({
                "seen": sum(int(value.get("seen") or 0) for value in states.values()),
                "new": len(new_ids),
                "duplicate": sum(int(value.get("duplicate") or 0) for value in states.values()),
                "filtered": sum(int(value.get("filtered") or 0) for value in states.values()),
                "parse_failed": sum(int(value.get("parse_failed") or 0) for value in states.values()),
                "save_failed": sum(int(value.get("save_failed") or 0) for value in states.values()),
                "progress": {"run_id": self.run_id, "outcome": "scoring", "current_platform": "", "platforms": deepcopy(states)},
            })

    def _persist(
        self,
        states: dict[str, dict[str, Any]],
        new_ids: list[str],
        current_platform: str,
        *,
        status: str | None = None,
        stop_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        update_collection_run(
            self.db_path, self.run_id, status=status, platform_states=states,
            collected_job_ids=list(dict.fromkeys(new_ids)), current_platform=current_platform,
            stop_reason=stop_reason, error=error,
        )
