"""Fill the review list in one run: full-time and internship slots, collect -> score -> filter -> repeat."""

from threading import Event

from bosshunter.db import get_db
from bosshunter.zombie import apply_zombie_filter, refresh_long_listed
from bosshunter.review_queue import (
    apply_company_dedup,
    apply_match_gate,
    automation_settings,
    kind_quotas,
    waiting_by_kind,
)

# Collector stop reasons that just mean "this keyword is done"; anything else is a safety stop.
BENIGN_STOP_REASONS = {None, "", "callback_stopped", "search_exhausted"}
KIND_LABELS = {"fulltime": "全职", "internship": "实习"}


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
        apply_zombie_filter(db, config)
        apply_company_dedup(db, config)
        apply_match_gate(db, config)
    finally:
        db.close()


def _internship_config(config: dict) -> dict:
    """Config copy that searches BOSS for internships only (both the legacy and the platform filter)."""
    filters = {"job_type": ["实习"]}
    search = {**(config.get("search") or {}), "filters": filters}
    platforms = dict(config.get("platforms") or {})
    boss = dict(platforms.get("boss") or {})
    boss["search"] = {**(boss.get("search") or {}), "filters": filters}
    platforms["boss"] = boss
    profile = {**(config.get("profile") or {}), "allow_internship": True}
    return {**config, "search": search, "platforms": platforms, "profile": profile}


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

    summary = {
        "collected": 0, "cap": settings["review_cap"], "stopped": "",
        "waiting": waiting_count(config), "by_kind": counts_by_kind(config), "quotas": quotas,
    }
    if not phases:
        summary["stopped"] = "没有搜索关键词，请先在配置页添加或运行 bosshunter keywords --apply"
        return summary

    if all(summary["by_kind"][kind] >= quotas[kind] for kind, _ in phases):
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

    for round_number in range(1, max_rounds + 1):
        progressed = False
        open_phases = 0
        for kind, words in phases:
            label = KIND_LABELS[kind]
            for keyword in words:
                if stopped():
                    summary["stopped"] = "已手动停止"
                    return summary
                counts = counts_by_kind(config)
                if counts[kind] >= quotas[kind]:
                    break
                open_phases += 1
                # Roughly half the collected jobs survive scoring, so ask for about twice the gap.
                want = min(per_keyword, max((quotas[kind] - counts[kind]) * 2, 4))
                report(f"第 {round_number} 轮 · {label} · 采集「{keyword}」（最多 {want} 个）…")
                run_config = _internship_config(config) if kind == "internship" else config
                new_count = scrape_jobs(run_config, [keyword], limit=want)
                stop_reason = (run_config.get("_workbench_collect_report") or {}).get("stop_reason")
                summary["collected"] += new_count
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
        if not open_phases or not progressed:
            break

    now = counts_by_kind(config)
    summary.update(by_kind=now, waiting=sum(now.values()))
    if any(now[kind] < quotas[kind] for kind, _ in phases) and not summary["stopped"]:
        summary["stopped"] = "关键词已采完，没有更多新岗位"
    return summary
