"""Apply to a Zhilian (普通招聘) posting from its detail page.

Flow (observed on the live site): the posting's "立即投递" button sends the account's default resume together with the
default greeting and shows a dialog "已向对方发送简历和打招呼语" with two buttons, "留在此页" and "继续沟通". "继续沟通" opens
the chat (i.zhaopin.com/im), where a customized greeting can be typed and sent. A delivery only counts as sent once that
dialog (or the button turning into "继续沟通") is seen; the customized greeting is a second, optional step.

Campus postings (智联校园) are never applied to from here: they go to the manual list.
"""

import json
import random
import time
from typing import Any, Callable

from bosshunter.browser import click_at, close_tab, evaluate, get_page_targets, new_tab, type_text, wait_for_load
from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop

CAPTCHA_PATTERN = "验证码|滑块|访问频繁|频率限制|账号异常|拒绝访问|安全验证"
CLOSED_PATTERN = "职位已下线|职位已关闭|职位不存在|该职位已被删除|页面不存在|职位已暂停"
DELIVERED_TEXT = "已向对方发送简历和打招呼语"
IM_URL_PART = "i.zhaopin.com/im"

PAGE_STATE_JS = r"""
(() => {
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };  // fixed elements have no offsetParent
  const body = document.body ? document.body.innerText : '';
  const point = (el) => {
    if (!el) return {x: 0, y: 0, clickable: false};
    el.scrollIntoView({block: 'center', inline: 'center'});
    const r = el.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
    const top = document.elementFromPoint(x, y);
    return {x, y, clickable: !!(top && (el === top || el.contains(top)) && x > 0 && y > 0 && x < innerWidth && y < innerHeight)};
  };
  const apply = [...document.querySelectorAll('.summary-planes__action button, button.job-detail-summary__apply')].find((b) => shown(b));
  const modal = [...document.querySelectorAll('.deliver-greeting-modal')].find((m) => shown(m));
  const byText = (text) => modal ? [...modal.querySelectorAll('button')].find((b) => (b.innerText || '').replace(/\s+/g, '').includes(text)) : null;
  const stay = byText('留在此页'), next = byText('继续沟通');
  const applyPoint = modal ? {x: 0, y: 0, clickable: false} : point(apply);
  const stayPoint = point(stay), nextPoint = point(next);
  return JSON.stringify({
    url: location.href, title: document.title || '',
    captcha: new RegExp('__CAPTCHA__').test(body.slice(0, 600)),
    closed: new RegExp('__CLOSED__').test(body.slice(0, 800)) && !apply,
    applyLabel: apply ? (apply.innerText || '').replace(/\s+/g, '') : '',
    applyX: applyPoint.x, applyY: applyPoint.y, applyClickable: applyPoint.clickable,
    modal: !!modal, modalText: modal ? (modal.innerText || '').replace(/\s+/g, ' ').slice(0, 300) : '',
    stayX: stayPoint.x, stayY: stayPoint.y, stayClickable: stayPoint.clickable,
    nextX: nextPoint.x, nextY: nextPoint.y, nextClickable: nextPoint.clickable,
  });
})()
""".replace("__CAPTCHA__", CAPTCHA_PATTERN).replace("__CLOSED__", CLOSED_PATTERN)

CHAT_STATE_JS = r"""
(() => {
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };  // fixed elements have no offsetParent
  const point = (el) => {
    if (!el) return {x: 0, y: 0, ok: false};
    const r = el.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
    const top = document.elementFromPoint(x, y);
    return {x, y, ok: !!(top && (el === top || el.contains(top)) && x > 0 && y > 0 && x < innerWidth && y < innerHeight)};
  };
  const input = [...document.querySelectorAll('textarea.im-sender__input')].find((e) => shown(e));
  const send = [...document.querySelectorAll('button.im-sender__send-btn')].find((e) => shown(e));
  const inputPoint = point(input), sendPoint = point(send);
  const messages = [...document.querySelectorAll('.im-msg-text')].map((e) => (e.innerText || '').replace(/\s+/g, ' ').trim());
  return JSON.stringify({
    url: location.href, inputReady: !!input, inputX: inputPoint.x, inputY: inputPoint.y, inputOk: inputPoint.ok,
    sendReady: !!send, sendX: sendPoint.x, sendY: sendPoint.y, sendOk: sendPoint.ok,
    typed: input ? input.value : '', messages: messages.slice(-12),
  });
})()
"""


def _json(raw: Any) -> dict:
    try:
        if isinstance(raw, dict) and "value" in raw:
            raw = raw["value"]
        return json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
    except (TypeError, ValueError):
        return {}


def _snippet(greeting: str) -> str:
    return "".join(str(greeting).split())[:14]


def _said(messages: list[Any], greeting: str) -> bool:
    return any(_snippet(greeting) in "".join(str(message).split()) for message in messages)


def send_chat_greeting(
    chat_tab: str,
    greeting: str,
    *,
    run_js: Callable[..., Any],
    click: Callable[[str, str], Any],
    type_into: Callable[[str, str], Any],
    sleep: Callable[[float], None],
) -> tuple[bool, str]:
    """Type ``greeting`` into the open Zhilian chat and send it. Returns (sent, detail)."""
    text = " ".join(str(greeting or "").split())
    if not text:
        return False, "没有招呼语"
    state: dict = {}
    for _ in range(8):                                   # the chat page renders its input a moment after it opens
        state = _json(run_js(chat_tab, CHAT_STATE_JS))
        if state.get("inputReady") and state.get("sendReady"):
            break
        sleep(1.0)
    if not (state.get("inputOk") and state.get("sendOk")):
        return False, "聊天输入框没有出现，未发送招呼语"
    if _said(state.get("messages") or [], text):
        return True, "聊天里已经有这条招呼语，未重复发送"
    click(chat_tab, f"{state['inputX']},{state['inputY']}")
    type_into(chat_tab, text)
    sleep(0.8)
    typed = _json(run_js(chat_tab, CHAT_STATE_JS))
    if _snippet(text) not in "".join(str(typed.get("typed") or "").split()):
        return False, "招呼语没有写进输入框，未发送"
    click(chat_tab, f"{typed['sendX']},{typed['sendY']}")
    for _ in range(6):
        sleep(1.0)
        if _said(_json(run_js(chat_tab, CHAT_STATE_JS)).get("messages") or [], text):
            return True, "招呼语已发送"
    return False, "点击发送后聊天记录里没有看到这条招呼语，请到智联“消息”核对"


def apply_zhilian(
    job: dict,
    throttle_config: dict,
    *,
    sleep: Callable[[float], None] = time.sleep,
    stop_requested: Callable[[], bool] = lambda: False,
    browser: Any = None,
    greeting: str = "",
) -> tuple[dict, str | None]:
    """Apply once. Returns ``(result, leftover_target_id)`` shaped like the BOSS sender's results."""
    b = browser or type("B", (), {})()
    open_tab = getattr(b, "new_tab", new_tab)
    close = getattr(b, "close_tab", close_tab)
    run_js = getattr(b, "evaluate", evaluate)
    click = getattr(b, "click_at", click_at)
    targets = getattr(b, "get_page_targets", get_page_targets)
    wait_load = getattr(b, "wait_for_load", wait_for_load)
    type_into = getattr(b, "type_text", type_text)

    if str(job.get("source_platform") or "") == "zhilian_campus":
        return {"success": False, "error": "campus_is_manual", "skip_backoff": True,
                "history_detail": "智联校招岗位只能手动投递"}, None
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
        # Close the job tab and any chat tab the apply opened; nothing else on those pages is touched.
        for target in targets():
            tid = str(target.get("targetId") or "")
            if tid and tid not in known and tid != target_id and "zhaopin.com" in str(target.get("url") or ""):
                close(tid)
        close(target_id)
        return result, None

    try:
        wait_load(target_id, timeout=15)
        if throttle_config.get("browse_before_greet", True):
            sleep(random.uniform(float(throttle_config.get("browse_duration_min", 15)), float(throttle_config.get("browse_duration_max", 30))))
        if stop_requested():
            return finish({"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True})

        state: dict = {}
        for _ in range(6):                                 # the detail panel renders after the page itself
            state = _json(run_js(target_id, PAGE_STATE_JS))
            if state.get("applyLabel") or state.get("captcha") or state.get("closed"):
                break
            sleep(1.0)
        if state.get("captcha"):
            return finish({"success": False, "error": "captcha", "history_detail": "智联出现验证，已停止"})
        if state.get("closed"):
            return finish({"success": False, "error": "job_page_unavailable", "history_detail": "岗位已关闭或下架", "skip_backoff": True})
        label = str(state.get("applyLabel") or "")
        if "已投" in label:
            return finish({"success": True, "history_detail": "智联页面显示已投递（此前已投过）", "already_applied": True})
        if "继续沟通" in label:
            # Applying (or any earlier chat) turns the button into 继续沟通: do not apply twice.
            return finish({"success": False, "error": "already_contacted", "skip_backoff": True,
                           "history_detail": "智联页面显示“继续沟通”，说明已经投递或沟通过，未重复投递"})
        if label != "立即投递":
            return finish({"success": False, "error": "no_apply_button", "history_detail": "没有找到“立即投递”按钮", "skip_backoff": True})
        state = _json(run_js(target_id, PAGE_STATE_JS))      # re-measure after scrolling the button into view
        if not state.get("applyClickable"):
            return finish({"success": False, "error": "apply_button_not_clickable", "history_detail": "立即投递按钮被遮挡或不在可视区域", "skip_backoff": True})
        click(target_id, f"{state['applyX']},{state['applyY']}")

        confirmed: dict = {}
        for _ in range(10):
            sleep(1.0)
            confirmed = _json(run_js(target_id, PAGE_STATE_JS))
            if confirmed.get("captcha"):
                return finish({"success": False, "error": "captcha", "history_detail": "智联出现验证，已停止"})
            if confirmed.get("modal") and DELIVERED_TEXT in str(confirmed.get("modalText") or ""):
                break
            if "继续沟通" in str(confirmed.get("applyLabel") or ""):
                break                                          # the button itself says it was delivered
        confirmed_by_dialog = bool(confirmed.get("modal") and DELIVERED_TEXT in str(confirmed.get("modalText") or ""))
        if not confirmed_by_dialog and "继续沟通" not in str(confirmed.get("applyLabel") or ""):
            return finish({"success": False, "error": "apply_not_confirmed", "skip_backoff": True,
                           "history_detail": "点击“立即投递”后没有看到“已向对方发送简历和打招呼语”，未确认成功，请到智联“投递记录”核对"})
        result = {"success": True, "history_detail": "智联已显示“已向对方发送简历和打招呼语”（默认简历和默认招呼语）"}

        want_greeting = bool(greeting) and throttle_config.get("_send_zhilian_greeting", True)
        if want_greeting and confirmed.get("modal") and confirmed.get("nextClickable"):
            before = {str(t.get("targetId") or "") for t in targets()}
            click(target_id, f"{confirmed['nextX']},{confirmed['nextY']}")
            chat_tab = ""
            for _ in range(8):
                sleep(1.0)
                for target in targets():
                    tid = str(target.get("targetId") or "")
                    if tid and tid not in before and IM_URL_PART in str(target.get("url") or ""):
                        chat_tab = tid
                if not chat_tab and IM_URL_PART in str(_json(run_js(target_id, PAGE_STATE_JS)).get("url") or ""):
                    chat_tab = target_id                       # the chat opened in the same tab
                if chat_tab:
                    break
            if chat_tab:
                sent, note = send_chat_greeting(chat_tab, greeting, run_js=run_js, click=click, type_into=type_into, sleep=sleep)
                result["greeting_sent"] = sent
                result["history_detail"] += f"；定制招呼语：{note}"
            else:
                result["history_detail"] += "；点“继续沟通”后没有打开聊天页，未发送定制招呼语"
        elif confirmed.get("modal") and confirmed.get("stayClickable"):
            click(target_id, f"{confirmed['stayX']},{confirmed['stayY']}")          # just close the dialog
        return finish(result)
    except Exception as exc:  # keep one bad page from killing the whole delivery queue
        return finish({"success": False, "error": "apply_exception", "history_detail": f"投递异常：{type(exc).__name__}", "skip_backoff": True})
