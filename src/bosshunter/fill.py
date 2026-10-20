"""Fill the review list in one run: full-time and internship slots, collect -> score -> filter -> repeat."""

from threading import Event

from bosshunter import keyword_strategy as strategy
from bosshunter.db import get_db
from bosshunter.employment import internship_search_config
from bosshunter.rejected_companies import apply_rejected_companies
from bosshunter.zombie import apply_zombie_filter, refresh_long_listed
from bosshunter.review_queue import (
    apply_activity_gate,
    apply_company_dedup,
    apply_match_gate,
    automation_settings,
    kind_quotas,
    refresh_recruit_scores,
    waiting_by_kind,
)

# Collector stop reasons that just mean "this keyword is done"; anything else is a safety stop.
BENIGN_STOP_REASONS = {None, "", "callback_stopped", "search_exhausted"}
KIND_LABELS = {"fulltime": "全职", "internship": "实习"}
EXTERNAL_PLATFORMS = ("zhilian", "51job", "liepin")
PLATFORM_NAMES = {"zhilian": "智联招聘", "51job": "前程无忧", "liepin": "猎聘"}
# External platforms: "login_required" only closes that platform for this run; other stops are safety stops.
EXTERNAL_BENIGN_REASONS = BENIGN_STOP_REASONS | {"no_new_jobs"}


def counts_by_kind(config: dict) -> dict[str, int]:
    db = get_db()
    try:
        return waiting_by_kind(db, config)
    finally:
        db.close()


def waiting_count(config: dict) -> int:
    """Jobs waiting for review plus approved jobs not sent yet (what counts against the cap)."""
    return sum(counts_by_kind(config).values())


def _tidy(config: dict) -> None:
    db = get_db()
    try:
        apply_rejected_companies(db)
        apply_zombie_filter(db, config)
        apply_company_dedup(db, config)
        refresh_recruit_scores(db, config)
        apply_activity_gate(db, config)
        apply_match_gate(db, config)
    finally:
        db.close()


def external_plan(config: dict) -> list[tuple[str, list[str]]]:
    """Enabled collection-only platforms with the keywords to search (their own, else the BOSS ones)."""
    platforms = config.get("platforms") or {}
    fallback = _keywords((config.get("search") or {}).get("keywords"))
    plan = []
    for name in EXTERNAL_PLATFORMS:
        entry = platforms.get(name) or {}
        if entry.get("enabled") is not True:
            continue
        words = _keywords((entry.get("search") or {}).get("keywords")) or fallback
        if words:
            plan.append((name, words))
    return plan


def collect_external(config: dict, platform: str, keyword: str, stop_event: Event | None) -> tuple[int, str]:
    """Collect one keyword on a collection-only platform. Returns (new jobs, stop reason)."""
    from bosshunter import db as db_module
    from bosshunter.collection.orchestrator import CollectionOrchestrator

    run_config = dict(config)
    run_config["_workbench_stop_event"] = stop_event
    # No internship second pass inside one keyword: fill() schedules internship keywords itself.
    options = {
        "platform_order": [platform], "auto_score": False,
        "platforms": {platform: {"keywords": [keyword], "intern_keywords": []}},
    }
    result = CollectionOrchestrator(run_config, db_path=db_module.DB_PATH).run(options)
    state = (result.get("platforms") or {}).get(platform, {})
    kind = "internship" if (config.get("profile") or {}).get("allow_internship") else "fulltime"
    db = get_db()
    try:
        strategy.record_run(
            db, platform, keyword, kind, seen=int(state.get("seen") or 0),
            new=int(state.get("new") or 0), filtered=int(state.get("filtered") or 0),
        )
    finally:
        db.close()
    return int(state.get("new") or 0), str(state.get("reason_code") or "")


def _load_plan() -> dict | None:
    from bosshunter import db as db_module
    from bosshunter.ai.keywords import load_plan

    return load_plan(db_module.DB_PATH.parent.parent)


def keyword_schedule(kind: str, words: list[str], round_number: int, gap: int, plan: dict | None, per_keyword: int) -> list[tuple[str, int]]:
    """(keyword, cap) in the order to search. Pass 1 covers every keyword by tier; later passes favour the best ones."""
    db = get_db()
    try:
        tiers = (
            {"core": list(words), "expand": [], "explore": [], "paused": []}   # internship words are one flat group
            if kind == "internship" else strategy.current_tiers(db, words, plan)
        )
        raw = max(gap * 2, 0)                                                  # about half survive scoring
        if round_number == 1:
            caps = strategy.first_pass_caps(tiers, raw)
            return [(word, caps[word]) for tier in strategy.TIERS for word in tiers[tier]]
        scores = strategy.keyword_scores(db)
        return [(word, per_keyword) for word in strategy.ordered_for_next_pass(tiers, scores)]
    finally:
        db.close()


def _record_run(platform: str, keyword: str, kind: str, new: int) -> None:
    db = get_db()
    try:
        strategy.record_run(db, platform, keyword, kind, seen=0, new=new, filtered=0)
    finally:
        db.close()


def _keywords(value) -> list[str]:
    return [str(k).strip() for k in value or [] if str(k).strip()]


def fill_review_list(
    config: dict,
    report=print,
    *,
    per_keyword: int = 20,
    max_rounds: int = 3,
    stop_event: Event | None = None,
) -> dict:
    """Collect and score until the full-time and internship slots are filled.

    Stops early on a safety stop (captcha, rate limit, daily page limit), on a user stop,
    or when a full round finds no new jobs. Returns a summary dict.
    """
    from bosshunter.ai.scorer import score_jobs
    from bosshunter.scraper.jobs import scrape_jobs

    settings = automation_settings(config)
    quotas = kind_quotas(config)
    search = config.get("search") or {}
    phases = [("fulltime", _keywords(search.get("keywords")))]
    if quotas["internship"] > 0:
        phases.append(("internship", _keywords(search.get("intern_keywords"))))
    phases = [(kind, words) for kind, words in phases if words and quotas[kind] > 0]
    externals = external_plan(config) if quotas["fulltime"] > 0 else []
    closed_platforms: set[str] = set()

    summary = {
        "collected": 0, "cap": settings["review_cap"], "stopped": "",
        "waiting": waiting_count(config), "by_kind": counts_by_kind(config), "quotas": quotas,
    }
    if not phases and not externals:
        summary["stopped"] = "没有搜索关键词，请先在配置页添加或运行 bosshunter keywords --apply"
        return summary

    open_kinds = {kind for kind, _ in phases} | ({"fulltime"} if externals else set())
    if all(summary["by_kind"][kind] >= quotas[kind] for kind in open_kinds):
        summary["stopped"] = "待审查岗位已达上限"
        return summary

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    # Long-listed jobs: re-read the HR status so zombies are judged on current data, then drop them.
    refreshed = refresh_long_listed(config, report, stop_event=stop_event)
    if refreshed["stopped"] and "风险" in refreshed["stopped"]:
        summary["stopped"] = refreshed["stopped"]
        return summary
    _tidy(config)

    def full(kind: str) -> bool:
        return counts_by_kind(config)[kind] >= quotas[kind]

    plan = _load_plan()
    platform_count = 1 + len(externals)
    taken: dict[tuple[str, str], int] = {}      # new jobs per (platform, kind) in this run, for the even platform split

    def share_left(platform: str, kind: str, round_number: int) -> int | None:
        """Budget the platform may still use in the coverage pass (None = no limit in later passes)."""
        if round_number > 1:
            return None
        return strategy.platform_share(quotas[kind] * 2, platform_count) - taken.get((platform, kind), 0)

    for round_number in range(1, max_rounds + 1):
        progressed = False
        open_phases = 0
        for kind, words in phases:
            label = KIND_LABELS[kind]
            schedule = keyword_schedule(kind, words, round_number, quotas[kind] - counts_by_kind(config)[kind], plan, per_keyword)
            for keyword, cap in schedule:
                if stopped():
                    summary["stopped"] = "已手动停止"
                    return summary
                counts = counts_by_kind(config)
                if counts[kind] >= quotas[kind]:
                    break
                gap = quotas[kind] - counts[kind]
                left = share_left("boss", kind, round_number)
                if left is not None and left <= 0:
                    break
                open_phases += 1
                # Roughly half the collected jobs survive scoring, so ask for about twice the gap.
                want = min(cap, max(gap * 2, 4), left if left is not None else cap)
                report(f"第 {round_number} 轮 · {label} · 采集「{keyword}」（最多 {want} 个）…")
                run_config = internship_search_config(config) if kind == "internship" else config
                new_count = scrape_jobs(run_config, [keyword], limit=want)
                stop_reason = (run_config.get("_workbench_collect_report") or {}).get("stop_reason")
                summary["collected"] += new_count
                taken[("boss", kind)] = taken.get(("boss", kind), 0) + new_count
                _record_run("boss", keyword, kind, new_count)
                if new_count:
                    progressed = True
                    report(f"  新增 {new_count} 个，评分中…")
                    score_jobs(config)
                    _tidy(config)
                now = counts_by_kind(config)
                report(
                    f"  全职 {now['fulltime']}/{quotas['fulltime']} · 实习 {now['internship']}/{quotas['internship']}"
                )
                if stop_reason not in BENIGN_STOP_REASONS:
                    summary.update(by_kind=now, waiting=sum(now.values()), stopped=f"采集已安全停止：{stop_reason}")
                    return summary
        for platform, words in externals:
            if platform in closed_platforms:
                continue
            jobs = [("fulltime", word, cap) for word, cap in keyword_schedule(
                "fulltime", words, round_number, quotas["fulltime"] - counts_by_kind(config)["fulltime"], plan, per_keyword)]
            if quotas["internship"] > 0:
                jobs += [("internship", word, cap) for word, cap in keyword_schedule(
                    "internship", _keywords(search.get("intern_keywords")), round_number,
                    quotas["internship"] - counts_by_kind(config)["internship"], plan, per_keyword)]
            for kind, keyword, cap in jobs:
                if stopped():
                    summary["stopped"] = "已手动停止"
                    return summary
                counts = counts_by_kind(config)
                if counts[kind] >= quotas[kind]:
                    continue
                left = share_left(platform, kind, round_number)
                if left is not None and left <= 0:
                    continue
                open_phases += 1
                want = min(cap, max((quotas[kind] - counts[kind]) * 2, 4), left if left is not None else cap)
                report(f"第 {round_number} 轮 · {PLATFORM_NAMES[platform]} · {KIND_LABELS[kind]} · 采集「{keyword}」（最多 {want} 个）…")
                run_config = {**config, "_keyword_limit": want}
                if kind == "internship":
                    run_config["profile"] = {**(config.get("profile") or {}), "allow_internship": True}
                try:
                    new_count, stop_reason = collect_external(run_config, platform, keyword, stop_event)
                except ValueError as exc:
                    report(f"  {PLATFORM_NAMES[platform]} 无法采集：{exc}")
                    closed_platforms.add(platform)
                    break
                summary["collected"] += new_count
                taken[(platform, kind)] = taken.get((platform, kind), 0) + new_count
                if new_count:
                    progressed = True
                    report(f"  新增 {new_count} 个，评分中…")
                    score_jobs(config)
                    _tidy(config)
                now = counts_by_kind(config)
                report(
                    f"  全职 {now['fulltime']}/{quotas['fulltime']} · 实习 {now['internship']}/{quotas['internship']}"
                )
                if stop_reason == "login_required":
                    report(f"  {PLATFORM_NAMES[platform]} 需要先登录，本次跳过该平台")
                    closed_platforms.add(platform)
                    break
                if stop_reason not in EXTERNAL_BENIGN_REASONS:
                    summary.update(by_kind=now, waiting=sum(now.values()), stopped=f"{PLATFORM_NAMES[platform]}采集已安全停止：{stop_reason}")
                    return summary
        if not open_phases or not progressed:
            break

    db = get_db()
    try:
        all_words = list(dict.fromkeys(_keywords(search.get("keywords")) + [w for _, words in externals for w in words]))
        for move in strategy.adjust_tiers(db, all_words, plan):
            report(f"关键词梯队调整：「{move['keyword']}」{move['from']} → {move['to']}（{move['reason']}）")
    finally:
        db.close()
    now = counts_by_kind(config)
    summary.update(by_kind=now, waiting=sum(now.values()))
    if any(now[kind] < quotas[kind] for kind in open_kinds) and not summary["stopped"]:
        summary["stopped"] = "关键词已采完，没有更多新岗位"
    return summary
