"""Keep the memory of the platform's resumes current: read what Liepin offers in its "选择附件简历" dialog, read
the preview text of resumes that are new or replaced, and have the AI model tag them. See ``resume_tags``."""

from pathlib import Path

from bosshunter import db as db_module
from bosshunter.resume_tags import REFRESH_HOURS, is_stale, sync_resumes


def refresh_hours(config: dict) -> float:
    automation = config.get("automation") if isinstance(config.get("automation"), dict) else {}
    try:
        return max(float(automation.get("resume_refresh_hours", REFRESH_HOURS)), 0.5)
    except (TypeError, ValueError):
        return REFRESH_HOURS


def _liepin_job_urls(conn) -> list[str]:
    """Pool jobs of Liepin that are not applied to yet, newest first: their pages still offer 投简历."""
    rows = conn.execute(
        "SELECT url FROM jobs WHERE source_platform = 'liepin' AND COALESCE(url, '') != '' "
        "AND status IN ('pending', 'ready', 'approved', 'filtered', 'rejected', 'error') AND deleted_at IS NULL "
        "ORDER BY rowid DESC LIMIT 6"
    ).fetchall()
    return [row[0] for row in rows]


def _browser_connected() -> bool:
    try:
        from bosshunter.browser import get_page_targets

        return bool(get_page_targets())
    except Exception:
        return False


def needs_refresh(config: dict) -> bool:
    conn = db_module.get_db()
    try:
        return is_stale(conn, "liepin", refresh_hours(config))
    finally:
        conn.close()


def run_sync(config: dict, *, detect: bool = True, force: bool = True) -> dict:
    """Sync once and return the report. Without the debug Chrome (or a job page to open) the memory stays as it is.

    ``force=False`` only syncs when the last check is older than ``automation.resume_refresh_hours``.
    """
    from bosshunter.executor.liepin_apply import scan_picker

    conn = db_module.get_db()
    try:
        scan = None
        if detect and _browser_connected():
            urls = _liepin_job_urls(conn)
            if urls:
                scan = lambda need_text: scan_picker(urls, need_text)  # noqa: E731
        return sync_resumes(conn, config, scan=scan, force=force or is_stale(conn, "liepin", refresh_hours(config)))
    finally:
        conn.close()


def format_report(report: dict) -> list[str]:
    """Plain-text lines for the console and the server log."""
    lines = [
        f"简历同步：{'已读取猎聘里的附件简历' if report['detected'] else '这次没能读取猎聘，沿用已记住的简历'}；"
        f"标签由{'AI 模型' if report['ai'] else '内置词表（未配置 AI）'}生成"
    ]
    if report["changed"]:
        lines.append("  新增或更换（已重新读取预览并打标签）：" + "、".join(report["changed"]))
    if report["removed"]:
        lines.append("  猎聘里已经没有：" + "、".join(report["removed"]))
    for item in report["resumes"]:
        source = {"ai": "AI", "file": "词表", "name": "仅名称"}.get(item["source"], item["source"])
        lines.append(f"  「{item['name']}」（{item['uploaded'] or '上传时间未知'}，{source}）标签：{'、'.join(item['tags']) or '无'}")
    if not report["resumes"]:
        lines.append("  还没有记住任何猎聘附件简历：请保持调试 Chrome 已登录猎聘，岗位池里要有尚未投递的猎聘岗位")
    return lines
