"""Fill the review list up to ``automation.review_cap`` in one run (collect, score, filter, repeat)."""

from threading import Event

from bosshunter.db import get_db
from bosshunter.review_queue import (
    apply_company_dedup,
    apply_match_gate,
    approved_unsent,
    automation_settings,
    review_pending,
)

# Collector stop reasons that just mean "this keyword is done"; anything else is a safety stop.
BENIGN_STOP_REASONS = {None, "", "callback_stopped", "search_exhausted"}


def waiting_count(config: dict) -> int:
    """Jobs waiting for review plus approved jobs not sent yet (what counts against the cap)."""
    settings = automation_settings(config)
    db = get_db()
    try:
        return len(review_pending(db, settings["review_min_score"])) + len(approved_unsent(db))
    finally:
        db.close()


def _tidy(config: dict) -> None:
    db = get_db()
    try:
        apply_company_dedup(db)
        apply_match_gate(db, config)
    finally:
        db.close()


def fill_review_list(
    config: dict,
    report=print,
    *,
    per_keyword: int = 20,
    max_rounds: int = 3,
    stop_event: Event | None = None,
) -> dict:
    """Collect and score keyword by keyword until the review list holds ``review_cap`` jobs.

    Stops early on a safety stop (captcha, rate limit, daily page limit), on a user stop,
    or when a full round finds no new jobs. Returns a summary dict.
    """
    from bosshunter.ai.scorer import score_jobs
    from bosshunter.scraper.jobs import scrape_jobs

    cap = automation_settings(config)["review_cap"]
    keywords = [str(k).strip() for k in (config.get("search") or {}).get("keywords", []) if str(k).strip()]
    summary = {"collected": 0, "waiting": waiting_count(config), "cap": cap, "stopped": "", "keywords": len(keywords)}
    if not keywords:
        summary["stopped"] = "没有搜索关键词，请先在配置页添加或运行 bosshunter keywords --apply"
        return summary
    if summary["waiting"] >= cap:
        summary["stopped"] = "待审查岗位已达上限"
        return summary

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    for round_number in range(1, max_rounds + 1):
        progressed = False
        for keyword in keywords:
            if stopped():
                summary["stopped"] = "已手动停止"
                return summary
            summary["waiting"] = waiting_count(config)
            if summary["waiting"] >= cap:
                summary["stopped"] = ""
                return summary
            # Roughly half the collected jobs survive scoring, so ask for about twice the gap.
            want = min(per_keyword, max((cap - summary["waiting"]) * 2, 4))
            report(f"第 {round_number} 轮 · 采集「{keyword}」（最多 {want} 个）…")
            new_count = scrape_jobs(config, [keyword], limit=want)
            stop_reason = (config.get("_workbench_collect_report") or {}).get("stop_reason")
            summary["collected"] += new_count
            if new_count:
                progressed = True
                report(f"  新增 {new_count} 个，评分中…")
                score_jobs(config)
                _tidy(config)
            summary["waiting"] = waiting_count(config)
            report(f"  待审查 {summary['waiting']} / {cap}")
            if stop_reason not in BENIGN_STOP_REASONS:
                summary["stopped"] = f"采集已安全停止：{stop_reason}"
                return summary
        if not progressed:
            break
    summary["waiting"] = waiting_count(config)
    if summary["waiting"] < cap and not summary["stopped"]:
        summary["stopped"] = "关键词已采完，没有更多新岗位"
    return summary
