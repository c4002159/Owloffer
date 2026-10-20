"""Apply to a 51job posting from its detail page.

Flow (observed on the live site): the orange "立即投递" button applies with the default resume and opens a
"使用微信扫码与HR沟通" page; when the job page is opened again the button reads "已投递".
A delivery only counts as sent once that "已投递" state is seen.
"""

import json
import random
import time
from typing import Any, Callable

from bosshunter.browser import click_at, close_tab, evaluate, get_page_targets, navigate, new_tab, wait_for_load
from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop

CAPTCHA_PATTERN = "滑动验证|安全验证|访问过于频繁|请完成安全验证|人机验证|验证码"
CLOSED_PATTERN = "职位已下线|职位已关闭|该职位已关闭|职位不存在|页面不存在|没有职位不要怕|已停止招聘|已招满"

PAGE_STATE_JS = r"""
(() => {
  const body = document.body ? document.body.innerText : '';
  const lookup = [...document.querySelectorAll('a,button,div,span')].filter((el) => {
    const text = (el.innerText || '').replace(/\s+/g, '');
    return /apply/i.test(el.className + '') && (text === '立即投递' || text === '已投递');
  });
  const el = lookup.length ? lookup[0] : null;
  const label = el ? (el.innerText || '').replace(/\s+/g, '') : '';
  if (el && label === '立即投递') el.scrollIntoView({block: 'center', inline: 'center'});
  const rect = el ? el.getBoundingClientRect() : null;
  const cx = rect ? Math.round(rect.x + rect.width / 2) : 0;
  const cy = rect ? Math.round(rect.y + rect.height / 2) : 0;
  const top = rect ? document.elementFromPoint(cx, cy) : null;
  return JSON.stringify({
    captcha: new RegExp('__CAPTCHA__').test(body.slice(0, 600)),
    closed: new RegExp('__CLOSED__').test(body.slice(0, 800)) && !el,
    label,
    x: cx, y: cy,
    clickable: !!(el && top && (el === top || el.contains(top)) && cx > 0 && cy > 0 && cx < innerWidth && cy < innerHeight),
  });
})()
""".replace("__CAPTCHA__", CAPTCHA_PATTERN).replace("__CLOSED__", CLOSED_PATTERN)


def _state(target_id: str, evaluate_fn: Callable[..., Any]) -> dict:
    raw = evaluate_fn(target_id, PAGE_STATE_JS)
    try:
        if isinstance(raw, dict) and "value" in raw:
            raw = raw["value"]
        return json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
    except (TypeError, ValueError):
        return {}


def apply_51job(
    job: dict,
    throttle_config: dict,
    *,
    sleep: Callable[[float], None] = time.sleep,
    stop_requested: Callable[[], bool] = lambda: False,
    browser: Any = None,
) -> tuple[dict, str | None]:
    """Apply once. Returns ``(result, leftover_target_id)`` shaped like the BOSS sender's results."""
    b = browser or type("B", (), {})()
    open_tab = getattr(b, "new_tab", new_tab)
    close = getattr(b, "close_tab", close_tab)
    run_js = getattr(b, "evaluate", evaluate)
    click = getattr(b, "click_at", click_at)
    goto = getattr(b, "navigate", navigate)
    targets = getattr(b, "get_page_targets", get_page_targets)
    wait_load = getattr(b, "wait_for_load", wait_for_load)

    guard = throttle_config.get("_platform_access_guard")
    if isinstance(guard, PlatformAccessGuard):
        try:
            guard.reserve("job_page")
        except PlatformSafetyStop as exc:
            return {"success": False, "error": exc.reason, "history_detail": "为了账户安全，已达到平台页面访问上限或仍处于风险冷却"}, None

    url = str(job.get("url") or "")
    if not url.startswith("http"):
        return {"success": False, "error": "no_job_url", "history_detail": "岗位没有可用的链接", "skip_backoff": True}, None
    known = {str(t.get("targetId") or "") for t in targets() if t.get("targetId")}
    target_id = open_tab(url, background=False)
    if not target_id:
        return {"success": False, "error": "open_page_failed", "history_detail": "无法打开页面", "skip_backoff": True}, None

    def finish(result: dict) -> tuple[dict, str | None]:
        for target in targets():
            tid = str(target.get("targetId") or "")
            if tid and tid not in known and tid != target_id and "51job" in str(target.get("url") or ""):
                close(tid)
        close(target_id)
        return result, None

    try:
        wait_load(target_id, timeout=15)
        if throttle_config.get("browse_before_greet", True):
            sleep(random.uniform(float(throttle_config.get("browse_duration_min", 15)), float(throttle_config.get("browse_duration_max", 30))))
        if stop_requested():
            return finish({"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True})

        state = _state(target_id, run_js)
        if state.get("captcha"):
            return finish({"success": False, "error": "captcha", "history_detail": "51job 出现验证，已停止"})
        if state.get("closed"):
            return finish({"success": False, "error": "job_page_unavailable", "history_detail": "岗位已关闭或下架", "skip_backoff": True})
        if state.get("label") == "已投递":
            return finish({"success": True, "history_detail": "51job 页面显示已投递（此前已投过）", "already_applied": True})
        if state.get("label") != "立即投递":
            return finish({"success": False, "error": "no_apply_button", "history_detail": "没有找到“立即投递”按钮", "skip_backoff": True})

        state = _state(target_id, run_js)          # re-measure after scrolling the button into view
        if not state.get("clickable"):
            return finish({"success": False, "error": "apply_button_not_clickable", "history_detail": "投递按钮被遮挡或不在可视区域", "skip_backoff": True})
        click(target_id, f"{state['x']},{state['y']}")
        sleep(random.uniform(3, 5))

        for _ in range(3):                         # confirm: reopen the job page and read the button
            goto(target_id, url)
            wait_load(target_id, timeout=15)
            sleep(random.uniform(2, 3))
            after = _state(target_id, run_js)
            if after.get("captcha"):
                return finish({"success": False, "error": "captcha", "history_detail": "51job 出现验证，已停止"})
            if after.get("label") == "已投递":
                return finish({"success": True, "history_detail": "51job 页面已显示已投递"})
        return finish({
            "success": False, "error": "apply_not_confirmed", "skip_backoff": True,
            "history_detail": "点击后页面没有显示“已投递”，未确认成功，请到 51job“我的申请”核对",
        })
    except Exception as exc:  # keep one bad page from killing the whole delivery queue
        return finish({"success": False, "error": "apply_exception", "history_detail": f"投递异常：{type(exc).__name__}", "skip_backoff": True})
