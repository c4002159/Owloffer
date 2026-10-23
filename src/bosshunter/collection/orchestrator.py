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
from bosshunter.collection.platforms.zhilian_campus import ZhilianCampusCollector
from bosshunter.collection.registry import CollectorRegistry
from bosshunter.collection.streaming_scorer import StreamingScorer
from bosshunter.collection_run_store import (
    boss_combo_key, boss_resume_options, claim_boss_resume, create_collection_run,
    save_boss_checkpoint, update_collection_run,
)
from bosshunter.ai.prefilter import freshness_filter_result
from bosshunter.employment import internship_search_config
import time

from bosshunter.platform_limits import cooldown_minutes, mark_limit_reached
from bosshunter.platform_safety import PlatformPageBudget, PlatformStop, platform_search_page_limit
from bosshunter.company_dedup import approved_within, cooldown_days, matching_company, pool_companies
from bosshunter.rejected_companies import is_rejected_company, rejected_names
from bosshunter.db import (
    find_job_id, get_db, insert_job_if_new, job_identity_exists, record_sighting, recompute_outsourcing,
)
from bosshunter.employment import internship_config_error
from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker
from bosshunter.outsourcing import load_rules


SUPPORTED_PLATFORMS = {"boss", "zhilian", "zhilian_campus", "51job", "liepin"}
SORT_OPTIONS = {
    "boss": {"default", "newest"},
    "zhilian": {"default", "newest"},
    "zhilian_campus": {"default", "newest"},
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
    campus_search = configured_platforms.get("zhilian_campus", {}).get("search", {}) if isinstance(configured_platforms.get("zhilian_campus"), dict) else {}
    job51_search = configured_platforms.get("51job", {}).get("search", {}) if isinstance(configured_platforms.get("51job"), dict) else {}
    liepin_search = configured_platforms.get("liepin", {}).get("search", {}) if isinstance(configured_platforms.get("liepin"), dict) else {}

    platforms: dict[str, Any] = {}
    for platform, fallback in (("boss", boss_search), ("zhilian", zhilian_search), ("zhilian_campus", campus_search), ("51job", job51_search), ("liepin", liepin_search)):
        value = raw_platforms.get(platform) if isinstance(raw_platforms.get(platform), dict) else {}
        search = value.get("search") if isinstance(value.get("search"), dict) else value
        if not isinstance(search, dict):
            search = {}
        base = dict(fallback) if isinstance(fallback, dict) else {}
        base.update(search)
        if platform == "boss" and not base.get("cities"):
            base["cities"] = config.get("profile", {}).get("target_cities", ["北京"])
        if platform != "boss":
            # A platform that was never configured searches the same words and cities as BOSS.
            for key in ("keywords", "cities"):
                if key not in search and not _clean_strings(base.get(key)):
                    base[key] = platforms["boss"][key]
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
    if supplied.get("target_new") not in (None, "", 0):
        options["target_new"] = supplied["target_new"]
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
        raise ValueError("采集平台只支持 boss、zhilian、zhilian_campus、51job 或 liepin")
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
        if platform in {"zhilian", "zhilian_campus"}:
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
    if options.get("target_new") not in (None, "", 0):
        try:
            target_new = int(options["target_new"])
        except (TypeError, ValueError) as exc:
            raise ValueError("每个平台的目标新增数必须是整数") from exc
        if not 1 <= target_new <= 1000:
            raise ValueError("每个平台的目标新增数范围为 1-1000")
        normalized["target_new"] = target_new
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
        self.on_new_job: Callable[[str], None] | None = None          # told about every newly saved posting (streaming scoring)
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
        self.repeat_ratio = float(automation.get("repeat_ratio", 0.8) or 0.8)
        self.repeat_strikes_limit = int(automation.get("repeat_strikes", 2) or 2)
        self.min_yield = float(automation.get("min_yield", 0.2) if automation.get("min_yield") is not None else 0.2)
        self.yield_strikes_limit = int(automation.get("yield_strikes", 2) or 2)
        self.abandoned_keyword = False
        self.per_keyword = False          # the give-up rules only apply where every keyword is collected on its own
        self.active_new = 0
        self.reset_keyword_window()

    def reset_keyword_window(self) -> None:
        """Start counting stale / repeated postings afresh for the next keyword."""
        self._stale_samples: list[bool] = []
        self._stale_strikes = 0
        self._repeat_samples: list[bool] = []
        self._repeat_strikes = 0
        self._yield_samples: list[bool] = []
        self._yield_strikes = 0
        self.abandoned_keyword = False

    def _note_yield(self, useful: bool) -> None:
        """Move on to the next keyword when almost nothing it lists is of use.

        Every posting a keyword lists counts: saved ones are useful, repeats and anything filtered (company already in the
        pool or applied to, city, inactive or stale, excluded words ...) are not. A block of about one page with fewer than
        ``min_yield`` useful ones is a strike, a better block clears the strikes, ``yield_strikes`` in a row end the keyword.
        All platforms report through the same processor, so they all follow the same rule.
        """
        if self.min_yield <= 0 or not self.per_keyword:
            return
        self._yield_samples.append(useful)
        if len(self._yield_samples) < self.stale_block:
            return
        block, self._yield_samples = self._yield_samples, []
        if sum(block) / len(block) < self.min_yield:
            self._yield_strikes += 1
            if self._yield_strikes >= self.yield_strikes_limit and not self.abandoned_keyword:
                self.abandoned_keyword = True
                self.event(message=f"关键词「{self.progress.keyword}」连续 {self._yield_strikes} 页几乎没有可用的新岗位，换下一个关键词")
        else:
            self._yield_strikes = 0

    def _note_repeat(self, repeated: bool) -> None:
        """Move on to the next keyword when almost everything it lists is already in the database.

        Same block logic as for stale postings: a block of about one page with at least ``repeat_ratio`` known postings
        is a strike, a block with new ones clears the strikes, ``repeat_strikes`` in a row end the keyword.
        """
        if not self.per_keyword:
            return
        self._repeat_samples.append(repeated)
        if len(self._repeat_samples) < self.stale_block:
            return
        block, self._repeat_samples = self._repeat_samples, []
        if sum(block) / len(block) >= self.repeat_ratio:
            self._repeat_strikes += 1
            if self._repeat_strikes >= self.repeat_strikes_limit and not self.abandoned_keyword:
                self.abandoned_keyword = True
                self.event(message=f"关键词「{self.progress.keyword}」连续 {self._repeat_strikes} 页几乎都是已采过的岗位，换下一个关键词")
        else:
            self._repeat_strikes = 0

    def _note_staleness(self, candidate: JobCandidate, stale: bool) -> None:
        """Give up on a keyword whose results keep being stale.

        Postings are judged in blocks of about one page; a block with at least ``stale_ratio`` stale postings is a
        strike, a good block clears the strikes, and ``stale_strikes`` in a row end the keyword (the search order
        puts the freshest first, so the following pages are only going to be worse).
        """
        if not self.per_keyword:
            return
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
            self._note_yield(False)
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
        if self.abandoned_keyword:
            # Let the next posting through: save() answers False for it, which every collector understands as "stop
            # this keyword" (an exception from here would be swallowed by some collectors' parse-error handling).
            return True
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
            self._note_repeat(True)
            self._note_yield(False)
            return False
        self._note_repeat(False)
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

    def precheck_company(self, company: str, city: str, title: str = "") -> str:
        """The company-level part of inspect(): why a posting of this company is not wanted ('' = fine).

        Collectors that only see a company name and a city on their list cards ask this before opening a detail page.
        """
        profile = self.config.get("profile", {}) if isinstance(self.config.get("profile"), dict) else {}
        stub = JobCandidate(
            platform=self.request.platform, source_job_id=f"card:{company}:{city}", title=title or "（列表卡片，未打开详情页）",
            company=company, city=city,
        )
        reason = ""
        if reason_city := self._city_mismatch(stub):
            reason = reason_city
        elif matched := matching_blocked_company(company, profile.get("blocked_companies") or []):
            reason = f"公司命中屏蔽规则：{matched}"
        elif matched := is_rejected_company(company, self.rejected_companies):
            reason = f"公司已拒绝过：{matched}"
        elif self.company_dedup and (matched := matching_company(company, self.recent_companies)):
            reason = f"公司近期已投递过（任一平台）：{matched}"
        elif self.company_dedup and (matched := matching_company(company, self.pool_companies)):
            reason = f"公司已有岗位在岗位池中：{matched}"
        if reason:
            self.event(message=reason, increment_filtered=True, filtered_candidate=stub)
        return reason

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
        if self.abandoned_keyword:
            return False
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
            from bosshunter.ai.prefilter import hr_active_level

            if (hr_active_level(candidate.hr_active) or 0) >= 2:        # active within about three days
                self.active_new += 1
            record_sighting(self.conn, candidate.storage_id)
            if self.on_new_job is not None:
                try:
                    self.on_new_job(candidate.storage_id)             # saved and committed: scoring may pick it up now
                except Exception:
                    pass
            self._note_yield(True)
        else:
            self.progress.duplicate += 1
            self._note_yield(False)
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
            "zhilian_campus": ZhilianCampusCollector,
            "51job": Job51Collector,
            "liepin": LiepinCollector,
        })
        self.run_id = run_id or str(uuid4())
        self.task_id = task_id
        self.stop_event = config.get("_workbench_stop_event")
        self.sleep = time.sleep          # replaceable, so tests need not wait out a cooldown

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
        self._tell_experience_level()
        self._target_new = int(options.get("target_new") or 0) or None      # stop each platform at this many new jobs
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
        scorer: StreamingScorer | None = None
        automation_settings = self.config.get("automation") if isinstance(self.config.get("automation"), dict) else {}
        if options["auto_score"] and automation_settings.get("stream_scoring") is True:
            scorer = StreamingScorer(self._score_batch, stop_event=self.stop_event, log=self._stream_log)
            scorer.start()

        def checkpoint(city: str, keyword: str, page: int) -> None:
            checkpoint_pages[boss_combo_key(city, keyword)] = page
            save_boss_checkpoint(conn, self.run_id, checkpoint_pages)

        try:
            # Platforms run in order. One that stops on a verification / rate-limit wording is put on a cooldown and the
            # others go first; once they are done it is visited once more (after waiting out what is left of the cooldown).
            queue = list(order)
            index = 0
            blocked_at: dict[str, int] = {}
            retry_ready: dict[str, float] = {}
            while queue:
                platform = queue.pop(0)
                index += 1
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
                processor.on_new_job = scorer.submit if scorer is not None else None
                prior_save_failures = processor.progress.save_failed
                # Bind the processor into the callback after construction so the
                # current platform's progress is not confused with prior IDs.
                def emit(progress, p=platform, processor_ref=processor):
                    states[p]["filter_records"] = list(processor_ref.filter_records)
                    states[p]["error_records"] = list(processor_ref.error_records)
                    self._emit(states, p, progress, all_new_ids, processor_ref.new_job_ids)
                processor.emit = emit
                # The platforms without a guard of their own get a daily budget of list pages: the page events every collector
                # sends are counted, and the platform's own stop signal is set once the day's pages are used up.
                page_limit = platform_search_page_limit(self.config, platform)
                budget = PlatformPageBudget(conn, platform, page_limit) if page_limit else None
                platform_stop = PlatformStop(self.stop_event) if budget is not None else self.stop_event

                def on_event(p=processor, budget=budget, platform_stop=platform_stop, **kwargs):
                    page = kwargs.get("page")
                    if budget is not None and page:
                        key = (kwargs.get("city") or p.progress.city, kwargs.get("keyword") or p.progress.keyword, page)
                        if not budget.note_page(key):
                            platform_stop.set()
                            return
                    p.event(**kwargs)

                hooks = CollectorHooks(
                    stop_event=platform_stop,
                    on_list_candidate=processor.inspect,
                    on_candidate=processor.save,
                    on_parse_failed=lambda reason, p=processor: self._parse_failed(p, reason),
                    on_event=on_event,
                    can_checkpoint=lambda p=processor, baseline=prior_save_failures: p.progress.save_failed == baseline,
                    completed_page=(lambda city, keyword: checkpoint_pages.get(boss_combo_key(city, keyword), 0)),
                    on_page_complete=checkpoint if order == ["boss"] else lambda *_: None,
                    on_list_company=processor.precheck_company,
                )
                try:
                    collector = (
                        BossCollector(config=self.config, safety_conn=conn)
                        if platform == "boss" and self._uses_default_registry
                        else Job51Collector(config=self.config, safety_conn=conn)
                        if platform == "51job" and self._uses_default_registry
                        else ZhilianCollector(config=self.config, safety_conn=conn)
                        if platform == "zhilian" and self._uses_default_registry
                        else ZhilianCampusCollector(config=self.config, safety_conn=conn)
                        if platform == "zhilian_campus" and self._uses_default_registry
                        else LiepinCollector(config=self.config, safety_conn=conn)
                        if platform == "liepin" and self._uses_default_registry
                        else self.registry.get(platform)
                    )
                    ok_statuses = {"completed", "completed_with_shortage"}
                    quotas = options.get("quotas") or {}
                    if budget is not None and budget.exhausted():
                        result = PlatformCollectionResult(
                            platform, "completed_with_shortage", "daily_search_page_limit",
                            f"今天的搜索页已用完（{budget.used()}/{page_limit}），明天再继续；可在配置的安全设置里调整",
                        )
                    elif self._budget and request.keywords:
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
                        and not (budget is not None and (budget.tripped or budget.exhausted()))
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
                if budget is not None and budget.tripped and not (self.stop_event is not None and self.stop_event.is_set()):
                    result = PlatformCollectionResult(
                        platform, "completed_with_shortage", "daily_search_page_limit",
                        f"今天的搜索页已用完（{budget.used()}/{page_limit}）；已采集的岗位保留，明天再继续",
                    )
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
                cooldown_block = result.status == "blocked" and result.reason_code == "rate_limit"
                if result.status in {"blocked", "failed"} and result.reason_code not in {"login_required", "user_stopped"}:
                    # The platform stopped collecting because of an error: pause it, so unattended runs skip it and a
                    # manual run has to be confirmed. A verification / rate-limit stop only cools down for a while.
                    minutes = cooldown_minutes(self.config) if cooldown_block else None
                    note = (f"采集遇到验证或限流，暂停 {minutes} 分钟，先采集其他平台：{result.message}" if cooldown_block
                            else f"采集异常已暂停（{result.reason_code}）：{result.message}")
                    try:
                        mark_limit_reached(conn, platform, note[:300], minutes=minutes)
                    except Exception:
                        pass
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
                    (result.status == "blocked" and result.reason_code not in {"login_required", "rate_limit"})
                    or result.reason_code in {"user_stopped", "browser_disconnected"}
                    or (self.stop_event and self.stop_event.is_set())
                ):
                    break
                if cooldown_block and platform not in blocked_at:
                    blocked_at[platform] = index
                    retry_ready[platform] = time.time() + cooldown_minutes(self.config) * 60
                if not queue and retry_ready:
                    # Only come back when other platforms were collected in the meantime.
                    due = [name for name in retry_ready if index > blocked_at[name]]
                    for name in [name for name in retry_ready if name not in due]:
                        retry_ready.pop(name)
                    if due:
                        name = min(due, key=lambda item: retry_ready[item])
                        wait = min(max(retry_ready.pop(name) - time.time(), 0.0), 35 * 60)
                        states[name].update(status="queued", message=f"冷却中，{int(wait // 60)} 分钟后再回来采集")
                        self._persist(states, all_new_ids, name, status="queued")
                        if wait > 0:
                            if self.stop_event is not None:
                                self.stop_event.wait(wait)
                            else:
                                self.sleep(wait)
                        queue.append(name)
            if self._budget:
                self.keyword_moves = self._adjust_tiers(conn, options)
        finally:
            if scorer is not None:
                try:
                    stats = scorer.finish()
                    if stats["batches"] or stats["errors"]:
                        self._stream_log(f"边采边评结束：共 {stats['batches']} 批，{stats['errors']} 批出错，留给统一评分的 {stats['left_over']} 个在队列里")
                except Exception:
                    pass
            conn.close()

        unique_new_ids = list(dict.fromkeys(str(job_id) for job_id in all_new_ids if str(job_id)))
        stopped = bool(self.stop_event and self.stop_event.is_set()) or any(r.reason_code == "user_stopped" for r in platform_results)
        last_result = {r.platform: r for r in platform_results}      # a platform that was retried counts by its last try
        errors = any(r.status in {"blocked", "failed"} for r in last_result.values())
        shortages = any(r.status == "completed_with_shortage" for r in platform_results)
        outcome = "stopped" if stopped else "completed_with_errors" if errors else "completed_with_shortage" if shortages else "completed"
        leftovers = self._still_pending(unique_new_ids) if (scorer is not None and options["auto_score"] and unique_new_ids) else unique_new_ids
        if options["auto_score"] and leftovers and not stopped:
            try:
                self._emit_scoring(states, leftovers)
                from bosshunter.ai.scorer import score_jobs

                score_config = dict(self.config)
                score_config["_workbench_stop_event"] = self.stop_event
                score_jobs(score_config, scope="selected", job_ids=leftovers, limit=None, force_rescore=False)
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

    # ------------------------------------------------------------------ streaming scoring
    def _stream_log(self, message: str) -> None:
        log = self.config.get("_workbench_log")
        if callable(log):
            log(message)

    def _still_pending(self, job_ids: list[str]) -> list[str]:
        """Which of these postings are still unscored (their own short-lived connection: safe next to the writers)."""
        ids = [str(job_id) for job_id in job_ids if str(job_id)]
        if not ids:
            return []
        db = get_db(self.db_path)
        try:
            pending: set[str] = set()
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                marks = ",".join("?" for _ in chunk)
                rows = db.execute(f"SELECT id FROM jobs WHERE status = 'pending' AND deleted_at IS NULL AND id IN ({marks})", chunk).fetchall()
                pending.update(str(row["id"]) for row in rows)
        finally:
            db.close()
        return [job_id for job_id in ids if job_id in pending]

    def _score_batch(self, job_ids: list[str]) -> int:
        """Score one batch with the ordinary scoring code; returns how many of them are still unscored."""
        from bosshunter.ai.scorer import score_jobs

        score_config = dict(self.config)
        score_config["_workbench_stop_event"] = self.stop_event
        score_jobs(score_config, scope="selected", job_ids=list(job_ids), limit=None, force_rescore=False)
        return len(self._still_pending(job_ids))

    def _tell_experience_level(self) -> None:
        """Put the experience level the uploaded resume shows into the config the collectors read."""
        if "_experience_level" in self.config:
            return
        if not str((self.config.get("profile") or {}).get("resume_path") or "").strip():
            self.config["_experience_level"] = ""                   # no resume configured: nothing to read
            return
        try:
            from bosshunter.experience_level import resume_experience_level

            self.config["_experience_level"] = resume_experience_level(self.config, self.db_path.parent)
        except Exception:
            self.config["_experience_level"] = ""

    def _collect_with_budget(self, collector, request, processor, hooks, conn, quota, kind, *, intern, words=None):
        """One collect() per keyword, in tier order, each capped by its share of the platform's budget.

        Full-time words get core > expand > explore caps; paused words are skipped (unless every word is paused);
        results of every keyword are recorded so tiers can move. Internship words share the budget evenly.
        """
        from dataclasses import replace

        from bosshunter import keyword_strategy as ks

        from bosshunter import keyword_progress as kp

        words = list(words if words is not None else request.keywords)
        target = None if intern else getattr(self, "_target_new", None)
        raw = target or math.ceil(max(quota, 0) * 2 / 0.9)  # about half survive scoring; 10% is reserve
        share = raw if target else ks.platform_share(raw, getattr(self, "_platform_count", 1))
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
        # Carry on where the last run today ended; once every keyword is done only a few earlier ones are looked at again.
        done = kp.done_today(conn, request.platform, kind)
        pending = kp.pending_words(ordered, done)
        resume_after = kp.get_cursor(conn, request.platform, kind) if pending else None
        if pending:
            pending = kp.rotate_after(pending, resume_after)
        spot_check = not pending and bool(ordered)
        if spot_check:
            pending = kp.spot_check_words(ordered)
            request = replace(request, max_pages=min(request.max_pages, kp.SPOT_CHECK_PAGES))
            processor.event(message=f"今天的关键词都采完了，抽检 {len(pending)} 个：{'、'.join(pending)}")
        start_total = len(processor.new_job_ids)
        processor.per_keyword = True
        automation = self.config.get("automation") if isinstance(self.config.get("automation"), dict) else {}
        frontier_on = automation.get("page_frontier") is True
        variants: list[str] = []
        variant_key = ""
        if not intern:
            from bosshunter.experience_level import experience_variants

            variant_key, variants = experience_variants(request.platform, self.config, request.filters)
        for round_no in range(3 if target and not spot_check else 1):              # later rounds top up the keywords that hit their cap
            for word in list(pending):
                if self.stop_event is not None and self.stop_event.is_set():
                    break
                got = len(processor.new_job_ids) - start_total
                if target and got >= target:
                    break
                before, seen0, filtered0 = len(processor.new_job_ids), processor.progress.seen, processor.progress.filtered
                cap = caps.get(word, ks.MIN_SLICE) if round_no == 0 else (target - got if target else ks.MIN_SLICE)
                if target:
                    cap = max(1, min(cap, target - got))
                processor.cap_total = before + cap
                processor.reset_keyword_window()
                variant, active0 = "", processor.active_new
                word_request = replace(request, keywords=[word])
                if variants:
                    from bosshunter import experience_bandit as bandit

                    variant = bandit.choose(conn, request.platform, variants)
                    word_request = replace(word_request, filters={**request.filters, variant_key: variant})
                    processor.event(message=f"「{word}」这次用经验标签 {variant}")
                try:
                    if frontier_on and not spot_check and not intern:
                        result = self._scan_keyword(collector, word_request, hooks, processor, conn, word)
                    else:
                        result = collector.collect(word_request, hooks)
                except CollectionError as exc:
                    if exc.code != "callback_stopped":
                        raise
                    result = PlatformCollectionResult(request.platform, "completed", "callback_stopped", "采集回调已停止")
                capped = result.reason_code == "callback_stopped" and not processor.abandoned_keyword
                processor.cap_total = None
                ks.record_run(
                    conn, request.platform, word, kind,
                    seen=processor.progress.seen - seen0, new=len(processor.new_job_ids) - before,
                    filtered=processor.progress.filtered - filtered0,
                )
                results.append(result)
                if variant:
                    bandit.record(
                        conn, request.platform, variant, seen=processor.progress.seen - seen0,
                        new=len(processor.new_job_ids) - before, active=processor.active_new - active0,
                    )
                if result.status not in ok and result.reason_code != "callback_stopped":
                    processor.per_keyword = False
                    return result                               # a safety stop ends the whole platform
                if not spot_check:
                    if target and len(processor.new_job_ids) - start_total >= target:
                        kp.set_cursor(conn, request.platform, kind, resume_after)    # cut by the target: this keyword is picked up again
                    else:
                        resume_after = word
                        kp.set_cursor(conn, request.platform, kind, word)            # the next collection goes on after this keyword
                # A page or detail that could not be read (reported as "incomplete_search") does not make the keyword unfinished: the
                # list was read to its end. Without this a BOSS keyword was never "done" and every collection began at the top again.
                finished = (
                    (result.status == "completed" or (result.status == "completed_with_shortage" and result.reason_code == "incomplete_search"))
                    and result.reason_code not in {"callback_stopped", "day_off", "outside_window"}
                ) or processor.abandoned_keyword
                if finished and not capped and not spot_check:  # searched to its end (or given up as stale / repeated)
                    kp.mark_done(conn, request.platform, kind, word)
                    pending.remove(word)
            got = len(processor.new_job_ids) - start_total
            if not target or got >= target or not pending or (self.stop_event is not None and self.stop_event.is_set()):
                break
        processor.per_keyword = False
        if not results:
            return PlatformCollectionResult(request.platform, "completed", "search_exhausted", "没有可采集的关键词")
        merged = results[-1]
        shortage = any(r.status == "completed_with_shortage" for r in results)
        merged.status = "completed_with_shortage" if shortage else "completed"
        if merged.reason_code == "callback_stopped":
            merged.reason_code = "search_exhausted"
        merged.message = f"按关键词预算采集 {len(results)} 个词：" + "；".join(r.message for r in results if r.message)[:400]
        return merged

    def _scan_keyword(self, collector, request, hooks, processor, conn, word):
        """Read one keyword's list in two parts instead of its first N pages every day: the head (as deep as it is still
        fresh) and a window of deep pages that starts where an earlier day stopped (see ``scan_frontier``)."""
        from dataclasses import replace

        from bosshunter import scan_frontier as sf
        from bosshunter.db import checkpoint_scope

        platform = request.platform
        cities = ",".join(request.cities)
        window = max(int(request.max_pages), 1)
        state = sf.get(conn, platform, word, cities)
        ok = {"completed", "completed_with_shortage"}
        was_per_keyword = processor.per_keyword
        processor.per_keyword = False          # the scan decides how deep to read; the give-up rules must not cut the deep pages

        def run(first, last, scope=""):
            seen0, dup0 = processor.progress.seen, processor.progress.duplicate
            window_request = replace(request, first_page=first, max_pages=last)
            try:
                if scope:
                    with checkpoint_scope(scope):
                        result = collector.collect(window_request, hooks)
                else:
                    result = collector.collect(window_request, hooks)
            except CollectionError as exc:
                if exc.code != "callback_stopped":
                    raise
                result = PlatformCollectionResult(platform, "completed", "callback_stopped", "采集回调已停止")
            return result, processor.progress.seen - seen0, processor.progress.duplicate - dup0

        def stop_here(result):
            return result.status not in ok or result.reason_code == "callback_stopped"

        try:
            head = min(max(int(state["head_pages"]), sf.HEAD_MIN), window)
            result, seen, dup = run(1, head)
            if stop_here(result):
                return result
            head_rate = sf.refresh_rate(seen, dup)
            if head_rate is not None:
                processor.event(message=f"「{word}」头部 {head} 页新岗位占比 {head_rate:.0%}")
                sf.save(conn, platform, word, cities, head_pages=sf.next_head_pages(head, head_rate), head_rate=head_rate)
            frontier = int(state["frontier"])
            probe_rate = None
            if frontier > head:
                probe, probe_seen, probe_dup = run(frontier, frontier, scope=f"p{frontier}")
                if stop_here(probe):
                    return probe
                if probe_seen == 0:                      # the list ends before the remembered page: start deep reading afresh
                    frontier = head
                else:
                    probe_rate = sf.refresh_rate(probe_seen, probe_dup)
                    processor.event(message=f"「{word}」记住的第 {frontier} 页新岗位占比 {probe_rate:.0%}"
                                    + ("，列表有变动，从这一页往后读" if probe_rate >= sf.FRESH_HIGH else "，多为已知，从下一页往后读"))
            start = sf.deep_start(frontier, head, probe_rate)
            end = start + window - 1
            processor.event(message=f"「{word}」深层读第 {start}–{end} 页")
            deep, deep_seen, deep_dup = run(start, end, scope=f"w{start}-{end}")
            if stop_here(deep):
                return deep
            if deep_seen == 0:                           # nothing there: the end of the list, begin again from the top next time
                sf.save(conn, platform, word, cities, frontier=0, deep_rate=None)
            else:
                sf.save(conn, platform, word, cities, frontier=end, deep_rate=sf.refresh_rate(deep_seen, deep_dup))
            deep.message = f"头部 {head} 页 + 深层 {start}–{end} 页；" + (deep.message or "")
            return deep
        finally:
            processor.per_keyword = was_per_keyword

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
            intern_request = PlatformCollectionRequest(
                platform=platform,
                # Liepin's own 经验 filter: 实习生
                **{**raw, "keywords": intern_words, **({"filters": {"work_year": "2"}} if platform == "liepin" else
                    {"filters": {"job_type": "02", "work_year": ""}} if platform == "51job" else
                    {"filters": {"employment_type": "4"}} if platform == "zhilian" else {})},
            )
        # About half of the collected jobs survive scoring, so collect roughly twice the quota.
        target = int(quotas.get("internship", 0)) * 2
        before = len(processor.new_job_ids)
        processor.event(phase="loading_list", message="开始采集实习岗位")

        def limited_save(candidate):
            keep = processor.save(candidate)
            return keep and (target <= 0 or len(processor.new_job_ids) - before < target)

        intern_hooks = CollectorHooks(
            stop_event=hooks.stop_event,
            on_list_candidate=processor.inspect,
            on_candidate=limited_save,
            on_parse_failed=hooks.on_parse_failed,
            on_event=hooks.on_event,
        )
        classes = {"boss": BossCollector, "51job": Job51Collector, "zhilian": ZhilianCollector,
                   "zhilian_campus": ZhilianCampusCollector, "liepin": LiepinCollector}
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
