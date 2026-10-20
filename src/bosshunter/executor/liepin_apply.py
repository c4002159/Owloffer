"""Apply to a Liepin posting from its detail page.

Flow (observed on the live site): "投简历" opens a "选择附件简历" dialog whose first resume is pre-selected;
its primary button "立即投递" submits and lands on ``c.liepin.com/job/apply/success`` ("投递成功").
That page pre-ticks similar jobs behind a "一键发简历" button, which is never touched: the tab is just closed.
A delivery only counts as sent once the success page (or text) is seen.
"""

import json
import random
import time
from typing import Any, Callable

from bosshunter.browser import click_at, close_tab, evaluate, get_page_targets, new_tab, type_text, wait_for_load
from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop

CAPTCHA_PATTERN = "滑动验证|安全验证|访问过于频繁|请完成安全验证|人机验证"
CLOSED_PATTERN = "职位不存在|职位已下线|该职位已关闭|页面不存在|职位已暂停"
SUCCESS_URL_PART = "/job/apply/success"

PAGE_STATE_JS = r"""
(() => {
  const body = document.body ? document.body.innerText : '';
  const point = (el) => {
    if (!el) return {x: 0, y: 0, clickable: false};
    el.scrollIntoView({block: 'center', inline: 'center'});
    const r = el.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
    const top = document.elementFromPoint(x, y);
    return {x, y, clickable: !!(top && (el === top || el.contains(top)) && x > 0 && y > 0 && x < innerWidth && y < innerHeight)};
  };
  const apply = document.querySelector('.apply-box a.btn-minor');
  const chat = document.querySelector('.apply-box a.btn-main');
  const modal = [...document.querySelectorAll('.ant-c-modal')].find((m) => m.offsetParent !== null && (m.innerText || '').includes('选择附件简历'));
  const confirm = modal ? [...modal.querySelectorAll('.ant-c-modal-footer button')].find((b) => (b.innerText || '').replace(/\s+/g, '') === '立即投递') : null;
  const picked = modal ? !!modal.querySelector('.ant-c-radio-checked') : false;
  const applyPoint = modal ? {x: 0, y: 0, clickable: false} : point(apply);
  const confirmPoint = point(confirm);
  return JSON.stringify({
    url: location.href, title: document.title || '',
    captcha: new RegExp('__CAPTCHA__').test(body.slice(0, 600)),
    closed: new RegExp('__CLOSED__').test(body.slice(0, 800)) && !apply,
    success: location.href.indexOf('__SUCCESS__') >= 0 || /^投递成功/.test(document.title || ''),
    applyLabel: apply ? (apply.innerText || '').replace(/\s+/g, '') : '',
    chatLabel: chat ? (chat.innerText || '').replace(/\s+/g, '') : '',
    applyX: applyPoint.x, applyY: applyPoint.y, applyClickable: applyPoint.clickable,
    modal: !!modal, picked, confirmX: confirmPoint.x, confirmY: confirmPoint.y, confirmClickable: confirmPoint.clickable,
  });
})()
""".replace("__CAPTCHA__", CAPTCHA_PATTERN).replace("__CLOSED__", CLOSED_PATTERN).replace("__SUCCESS__", SUCCESS_URL_PART)


CHAT_STATE_JS = r"""
(() => {
  const body = document.body ? document.body.innerText : '';
  const point = (el) => {
    if (!el) return {x: 0, y: 0, ok: false};
    const r = el.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
    const top = document.elementFromPoint(x, y);
    return {x, y, ok: !!(top && (el === top || el.contains(top)) && x > 0 && y > 0 && x < innerWidth && y < innerHeight)};
  };
  const chat = document.querySelector('.apply-box a.btn-main');
  if (chat && !document.querySelector('textarea.ant-im-input')) chat.scrollIntoView({block: 'center'});
  const chatPoint = point(chat);
  const input = [...document.querySelectorAll('textarea.ant-im-input')].find((e) => e.offsetParent !== null);
  const send = document.querySelector('.im-ui-basic-send-btn');
  const inputPoint = point(input), sendPoint = point(send);
  return JSON.stringify({
    chatLabel: chat ? (chat.innerText || '').replace(/\s+/g, '') : '', chatX: chatPoint.x, chatY: chatPoint.y, chatOk: chatPoint.ok,
    inputReady: !!input, inputX: inputPoint.x, inputY: inputPoint.y, inputOk: inputPoint.ok,
    sendReady: !!send, sendX: sendPoint.x, sendY: sendPoint.y, sendOk: sendPoint.ok,
    typed: input ? input.value : '', body: body.replace(/\s+/g, ' ').slice(0, 8000),   // the open chat sits above the conversation list
  });
})()
"""


def _chat_state(target_id: str, run_js: Callable[..., Any]) -> dict:
    raw = run_js(target_id, CHAT_STATE_JS)
    try:
        if isinstance(raw, dict) and "value" in raw:
            raw = raw["value"]
        return json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
    except (TypeError, ValueError):
        return {}


def _snippet(greeting: str) -> str:
    return "".join(str(greeting).split())[:14]


def send_chat_greeting(
    job_url: str,
    greeting: str,
    *,
    open_tab: Callable[..., Any],
    close: Callable[[str], Any],
    run_js: Callable[..., Any],
    click: Callable[[str, str], Any],
    type_into: Callable[[str, str], Any],
    wait_load: Callable[..., Any],
    sleep: Callable[[float], None],
    labels: tuple[str, ...] = ("继续聊",),
) -> tuple[bool, str]:
    """Send ``greeting`` in the chat opened by the job page's chat button. Returns (sent, detail).

    ``labels`` are the button texts that may open the chat: 继续聊 once a conversation exists (after applying),
    plus 聊一聊 for jobs that only offer chat.
    """
    text = " ".join(str(greeting or "").split())
    if not text:
        return False, "没有招呼语"
    tab = open_tab(job_url, background=False)
    if not tab:
        return False, "无法打开岗位页发送招呼语"
    try:
        wait_load(tab, timeout=15)
        sleep(2)
        state = _chat_state(tab, run_js)
        if not any(label in str(state.get("chatLabel") or "") for label in labels):
            return False, f"岗位页没有“{'/'.join(labels)}”入口，未发送招呼语"
        if _snippet(text) in "".join(str(state.get("body") or "").split()):
            return True, "聊天里已经有这条招呼语，未重复发送"
        if not state.get("chatOk"):
            return False, "聊天按钮被遮挡，未发送招呼语"
        click(tab, f"{state['chatX']},{state['chatY']}")
        for _ in range(8):
            sleep(1.0)
            state = _chat_state(tab, run_js)
            if state.get("inputReady") and state.get("sendReady"):
                break
        if not (state.get("inputOk") and state.get("sendOk")):
            return False, "聊天输入框没有出现，未发送招呼语"
        click(tab, f"{state['inputX']},{state['inputY']}")
        type_into(tab, text)
        sleep(0.8)
        typed = _chat_state(tab, run_js)
        if _snippet(text) not in "".join(str(typed.get("typed") or "").split()):
            return False, "招呼语没有写进输入框，未发送"
        click(tab, f"{typed['sendX']},{typed['sendY']}")
        for _ in range(6):
            sleep(1.0)
            if _snippet(text) in "".join(str(_chat_state(tab, run_js).get("body") or "").split()):
                return True, "招呼语已发送"
        return False, "点击发送后聊天记录里没有看到这条招呼语，请到猎聘核对"
    except Exception as exc:
        return False, f"发送招呼语异常：{type(exc).__name__}"
    finally:
        close(tab)


def _state(target_id: str, run_js: Callable[..., Any]) -> dict:
    raw = run_js(target_id, PAGE_STATE_JS)
    try:
        if isinstance(raw, dict) and "value" in raw:
            raw = raw["value"]
        return json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
    except (TypeError, ValueError):
        return {}


def apply_liepin(
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
        # Close whatever the apply opened (success page included); nothing on those pages is clicked.
        for target in targets():
            tid = str(target.get("targetId") or "")
            if tid and tid not in known and tid != target_id and "liepin" in str(target.get("url") or ""):
                close(tid)
        close(target_id)
        return result, None

    def success_somewhere() -> bool:
        if _state(target_id, run_js).get("success"):
            return True
        return any(
            SUCCESS_URL_PART in str(t.get("url") or "") and str(t.get("targetId") or "") not in known
            for t in targets()
        )

    try:
        wait_load(target_id, timeout=15)
        if throttle_config.get("browse_before_greet", True):
            sleep(random.uniform(float(throttle_config.get("browse_duration_min", 15)), float(throttle_config.get("browse_duration_max", 30))))
        if stop_requested():
            return finish({"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True})

        state = _state(target_id, run_js)
        if state.get("captcha"):
            return finish({"success": False, "error": "captcha", "history_detail": "猎聘出现验证，已停止"})
        if state.get("closed"):
            return finish({"success": False, "error": "job_page_unavailable", "history_detail": "岗位已关闭或下架", "skip_backoff": True})
        if "已投" in str(state.get("applyLabel") or ""):
            return finish({"success": True, "history_detail": "猎聘页面显示已投递（此前已投过）", "already_applied": True})
        if "继续聊" in str(state.get("chatLabel") or ""):
            # Applying opens a conversation, and the job page then reads 继续聊 while 投简历 stays; do not apply twice.
            return finish({"success": False, "error": "already_contacted", "skip_backoff": True,
                           "history_detail": "猎聘页面显示“继续聊”，说明已经投递或沟通过，未重复投递"})
        if state.get("applyLabel") != "投简历" and state.get("chatLabel") == "聊一聊":
            # Chat-only posting: there is nothing to apply with, the customized greeting IS the delivery.
            if not (greeting and throttle_config.get("_send_greeting_after_apply")):
                return finish({"success": False, "error": "chat_only_needs_greeting", "skip_backoff": True,
                               "history_detail": "这个岗位只有“聊一聊”，需要开启 automation.external_greeting_send 并有招呼语才能发送"})
            sent, note = send_chat_greeting(
                url, greeting, open_tab=open_tab, close=close, run_js=run_js, click=click,
                type_into=type_into, wait_load=wait_load, sleep=sleep, labels=("聊一聊", "继续聊"),
            )
            if sent:
                return finish({"success": True, "greeting_sent": True, "chat_only": True,
                               "history_detail": f"猎聘“聊一聊”：{note}"})
            return finish({"success": False, "error": "chat_greeting_not_sent", "skip_backoff": True,
                           "history_detail": f"猎聘“聊一聊”未完成：{note}"})
        if state.get("applyLabel") != "投简历":
            return finish({"success": False, "error": "no_apply_button", "history_detail": "没有找到“投简历”按钮", "skip_backoff": True})
        state = _state(target_id, run_js)          # re-measure after scrolling the button into view
        if not state.get("applyClickable"):
            return finish({"success": False, "error": "apply_button_not_clickable", "history_detail": "投简历按钮被遮挡或不在可视区域", "skip_backoff": True})
        click(target_id, f"{state['applyX']},{state['applyY']}")

        dialog: dict = {}
        for _ in range(8):                         # wait for the resume picker with a pre-selected resume
            sleep(1.0)
            dialog = _state(target_id, run_js)
            if dialog.get("modal") and dialog.get("confirmClickable"):
                break
        if not (dialog.get("modal") and dialog.get("confirmClickable")):
            return finish({"success": False, "error": "resume_dialog_missing", "skip_backoff": True,
                           "history_detail": "点“投简历”后没有出现选择简历的弹窗，已放弃"})
        if not dialog.get("picked"):
            return finish({"success": False, "error": "no_resume_selected", "skip_backoff": True,
                           "history_detail": "弹窗里没有默认选中的简历，已放弃（请先在猎聘设置默认简历）"})
        click(target_id, f"{dialog['confirmX']},{dialog['confirmY']}")

        for _ in range(10):
            sleep(1.0)
            if success_somewhere():
                done = finish({"success": True, "history_detail": "猎聘已跳转到“投递成功”页面"})
                if greeting and throttle_config.get("_send_greeting_after_apply"):
                    sent, note = send_chat_greeting(
                        url, greeting, open_tab=open_tab, close=close, run_js=run_js, click=click,
                        type_into=type_into, wait_load=wait_load, sleep=sleep,
                    )
                    done[0]["greeting_sent"] = sent
                    done[0]["history_detail"] += f"；招呼语：{note}"
                return done
        after = _state(target_id, run_js)
        if after.get("captcha"):
            return finish({"success": False, "error": "captcha", "history_detail": "猎聘出现验证，已停止"})
        return finish({
            "success": False, "error": "apply_not_confirmed", "skip_backoff": True,
            "history_detail": "点击“立即投递”后没有看到投递成功页，未确认成功，请到猎聘“投递记录”核对",
        })
    except Exception as exc:  # keep one bad page from killing the whole delivery queue
        return finish({"success": False, "error": "apply_exception", "history_detail": f"投递异常：{type(exc).__name__}", "skip_backoff": True})
