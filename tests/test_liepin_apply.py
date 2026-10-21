import json
from types import SimpleNamespace

from bosshunter.collection.capabilities import platform_supports
from bosshunter.executor.liepin_apply import apply_liepin

JOB = {"id": "liepin:1", "url": "https://www.liepin.com/job/1.shtml"}
CONFIG = {"browse_before_greet": False}


def _state(**values):
    base = {"url": "https://www.liepin.com/job/1.shtml", "captcha": False, "closed": False, "success": False,
            "applyLabel": "投简历", "applyX": 1096, "applyY": 123, "applyClickable": True,
            "modal": False, "picked": False, "confirmX": 0, "confirmY": 0, "confirmClickable": False}
    base.update(values)
    return json.dumps(base)


DIALOG = _state(modal=True, picked=True, confirmX=950, confirmY=511, confirmClickable=True)
SUCCESS = _state(success=True, url="https://c.liepin.com/job/apply/success?job_id=1")


def _apply(states, success_tab=False):
    clicks, closed = [], []
    queue = list(states)
    browser = SimpleNamespace(
        new_tab=lambda url, background=False: "tab1",
        close_tab=lambda tid: closed.append(tid) or True,
        evaluate=lambda tid, js: queue.pop(0) if len(queue) > 1 else queue[0],
        click_at=lambda tid, xy: clicks.append(xy) or True,
        get_page_targets=lambda: ([{"targetId": "ok", "url": "https://c.liepin.com/job/apply/success?job_id=1"}] if success_tab and clicks else []),
        wait_for_load=lambda tid, timeout=15: True,
    )
    result, _ = apply_liepin(JOB, CONFIG, sleep=lambda _s: None, browser=browser)
    return result, clicks, closed


def test_liepin_delivery_is_enabled_after_the_verified_real_application():
    assert platform_supports("liepin", "deliver")


def test_two_clicks_then_success_page_counts_as_sent_and_nothing_else_is_clicked():
    result, clicks, closed = _apply([_state(), _state(), DIALOG, SUCCESS])
    assert result["success"] and clicks == ["1096,123", "950,511"]     # 投简历, then 立即投递 - never 一键发简历
    assert "tab1" in closed


def test_success_page_opened_in_a_new_tab_is_found_and_closed():
    result, clicks, closed = _apply([_state(), _state(), DIALOG, _state()], success_tab=True)
    assert result["success"] and len(clicks) == 2 and "ok" in closed and "tab1" in closed


def test_no_success_page_is_not_counted():
    result, clicks, _ = _apply([_state(), _state(), DIALOG])
    assert not result["success"] and result["error"] == "apply_not_confirmed" and len(clicks) == 2


def test_missing_dialog_or_missing_default_resume_never_submits():
    result, clicks, _ = _apply([_state()])
    assert result["error"] == "resume_dialog_missing" and len(clicks) == 1
    unpicked = _state(modal=True, picked=False, confirmX=950, confirmY=511, confirmClickable=True)
    result, clicks, _ = _apply([_state(), _state(), unpicked])
    assert result["error"] == "no_resume_selected" and len(clicks) == 1


def test_applied_closed_captcha_and_covered_button_cases():
    result, clicks, _ = _apply([_state(applyLabel="已投递")])
    assert result["success"] and result.get("already_applied") and clicks == []
    for state, error in [
        (_state(captcha=True), "captcha"),
        (_state(closed=True, applyLabel=""), "job_page_unavailable"),
        (_state(applyLabel="聊一聊"), "no_apply_button"),
        (_state(applyClickable=False), "apply_button_not_clickable"),
    ]:
        result, clicks, closed = _apply([state])
        assert result["error"] == error and clicks == [] and "tab1" in closed


def test_a_job_that_already_reads_continue_chat_is_not_applied_twice():
    result, clicks, closed = _apply([_state(chatLabel="继续聊")])
    assert result["error"] == "already_contacted" and clicks == [] and "tab1" in closed


# ---- greeting in the chat after a confirmed application -------------------------------------------------
from bosshunter.executor.liepin_apply import send_chat_greeting  # noqa: E402

GREETING = "您好，我已投递贵司的 FAE 岗位，做过 Python 多智能体和 RAG 项目，期待沟通。"


def _chat(**values):
    base = {"chatLabel": "继续聊", "chatX": 1272, "chatY": 123, "chatOk": True, "inputReady": True, "inputX": 700, "inputY": 600,
            "inputOk": True, "sendReady": True, "sendX": 1100, "sendY": 650, "sendOk": True, "typed": "", "body": "聊天记录"}
    base.update(values)
    return json.dumps(base)


def _greet(states, typed_texts=None):
    clicks, typed, closed = [], [], []
    queue = list(states)
    sent, note = send_chat_greeting(
        JOB["url"], GREETING,
        open_tab=lambda url, background=False: "chat-tab", close=lambda tid: closed.append(tid),
        run_js=lambda tid, js: queue.pop(0) if len(queue) > 1 else queue[0],
        click=lambda tid, xy: clicks.append(xy), type_into=lambda tid, text: typed.append(text),
        wait_load=lambda tid, timeout=15: True, sleep=lambda _s: None,
    )
    return sent, note, clicks, typed, closed


def test_greeting_is_typed_into_the_chat_and_confirmed_in_the_transcript():
    snippet = "".join(GREETING.split())[:14]
    states = [_chat(), _chat(), _chat(typed=GREETING), _chat(body="聊天记录 " + GREETING)]
    sent, note, clicks, typed, closed = _greet(states)
    assert sent and typed == [GREETING] and clicks == ["1272,123", "700,600", "1100,650"] and closed == ["chat-tab"]
    assert snippet in "".join(GREETING.split())


def test_greeting_is_not_sent_twice_or_without_a_visible_chat():
    sent, note, clicks, typed, _ = _greet([_chat(body="聊天记录 " + GREETING)])
    assert sent and clicks == [] and typed == []
    sent, note, clicks, typed, _ = _greet([_chat(chatLabel="聊一聊")])
    assert not sent and clicks == [] and "继续聊" in note
    sent, note, clicks, typed, _ = _greet([_chat(), _chat(inputReady=False, inputOk=False)])
    assert not sent and typed == []


def test_unconfirmed_greeting_is_reported_not_claimed():
    sent, note, _, typed, _ = _greet([_chat(), _chat(), _chat(typed=GREETING), _chat(body="聊天记录")])
    assert not sent and "没有看到" in note
    sent, note, clicks, typed, _ = _greet([_chat(), _chat(), _chat(typed="")])
    assert not sent and "没有写进输入框" in note and "1100,650" not in clicks


def test_apply_sends_the_greeting_only_when_enabled():
    greeting_states = [_chat(), _chat(), _chat(typed=GREETING), _chat(body=GREETING)]

    def run(enabled):
        clicks, typed = [], []
        queue = [_state(), _state(), DIALOG, SUCCESS]
        chat_queue = list(greeting_states)
        tabs = iter(["tab1", "chat-tab"])
        current = {}

        def evaluate(tid, js):
            if "textarea.ant-im-input" in js:
                return chat_queue.pop(0) if len(chat_queue) > 1 else chat_queue[0]
            return queue.pop(0) if len(queue) > 1 else queue[0]

        browser = SimpleNamespace(
            new_tab=lambda url, background=False: next(tabs), close_tab=lambda tid: True, evaluate=evaluate,
            click_at=lambda tid, xy: clicks.append(xy) or True, get_page_targets=lambda: [],
            wait_for_load=lambda tid, timeout=15: True, type_text=lambda tid, text: typed.append(text) or True,
        )
        result, _ = apply_liepin(JOB, {**CONFIG, "_send_greeting_after_apply": enabled}, sleep=lambda _s: None,
                                 browser=browser, greeting=GREETING)
        return result, typed

    off, typed_off = run(False)
    assert off["success"] and typed_off == [] and "greeting_sent" not in off
    on, typed_on = run(True)
    assert on["success"] and on["greeting_sent"] and typed_on == [GREETING] and "招呼语已发送" in on["history_detail"]


def _chat_only(enabled, greeting=GREETING, chat_states=None):
    clicks, typed = [], []
    page = _state(applyLabel="", chatLabel="聊一聊")
    chat_queue = list(chat_states or [_chat(chatLabel="聊一聊"), _chat(chatLabel="聊一聊"), _chat(typed=GREETING), _chat(body=GREETING)])
    tabs = iter(["tab1", "chat-tab"])

    def evaluate(tid, js):
        if "textarea.ant-im-input" in js:
            return chat_queue.pop(0) if len(chat_queue) > 1 else chat_queue[0]
        return page

    browser = SimpleNamespace(
        new_tab=lambda url, background=False: next(tabs), close_tab=lambda tid: True, evaluate=evaluate,
        click_at=lambda tid, xy: clicks.append(xy) or True, get_page_targets=lambda: [],
        wait_for_load=lambda tid, timeout=15: True, type_text=lambda tid, text: typed.append(text) or True,
    )
    result, _ = apply_liepin(JOB, {**CONFIG, "_send_greeting_after_apply": enabled}, sleep=lambda _s: None,
                             browser=browser, greeting=greeting)
    return result, clicks, typed


def test_chat_only_job_is_delivered_by_sending_the_greeting():
    result, clicks, typed = _chat_only(True)
    assert result["success"] and result["chat_only"] and result["greeting_sent"] and typed == [GREETING]
    assert "1272,123" in clicks and "1100,650" in clicks                  # 聊一聊, then 发送 - no 投简历 anywhere


def test_chat_only_job_is_greeted_without_any_switch_but_needs_a_greeting_and_a_confirmed_send():
    result, clicks, typed = _chat_only(False)                      # the switch only concerns the message after an application
    assert result["success"] and result["chat_only"] and typed == [GREETING]
    result, clicks, typed = _chat_only(False, "")
    assert not result["success"] and result["error"] == "chat_only_needs_greeting" and clicks == [] and typed == []
    result, _, _ = _chat_only(True, chat_states=[_chat(chatLabel="聊一聊"), _chat(chatLabel="聊一聊"), _chat(typed=GREETING), _chat(body="聊天记录")])
    assert not result["success"] and result["error"] == "chat_greeting_not_sent"


# ---- which attachment resume is used: chosen by tag matching, handed in as a chooser ----------------------
RESUMES = [
    {"name": "ai应用工程师（微调）", "checked": True, "x": 540, "y": 259, "ok": True},
    {"name": "ai产品经理", "checked": False, "x": 540, "y": 324, "ok": True},
    {"name": "agent应用工程师", "checked": False, "x": 540, "y": 389, "ok": True},
]


def _apply_with_resumes(chooser, resumes=RESUMES, *, greeting="", flag=False, after_click_checked=None):
    clicks, typed = [], []
    dialog = json.loads(DIALOG)
    dialog["resumes"] = resumes
    states = [_state(), _state(), json.dumps(dialog)]
    if after_click_checked is not None:
        after = json.loads(DIALOG)
        after["resumes"] = after_click_checked
        states.append(json.dumps(after))
    states.append(SUCCESS)
    queue, chat_queue = list(states), [_chat(chatLabel="聊一聊"), _chat(chatLabel="聊一聊"), _chat(typed=GREETING), _chat(body=GREETING)]
    tabs = iter(["tab1", "chat-tab"])

    def evaluate(tid, js):
        if "textarea.ant-im-input" in js:
            return chat_queue.pop(0) if len(chat_queue) > 1 else chat_queue[0]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    browser = SimpleNamespace(
        new_tab=lambda url, background=False: next(tabs), close_tab=lambda tid: True, evaluate=evaluate,
        click_at=lambda tid, xy: clicks.append(xy) or True, get_page_targets=lambda: [],
        wait_for_load=lambda tid, timeout=15: True, type_text=lambda tid, text: typed.append(text) or True,
    )
    config = {**CONFIG, "_send_greeting_after_apply": flag, "_resume_chooser": chooser}
    result, _ = apply_liepin(JOB, config, sleep=lambda _s: None, browser=browser, greeting=greeting)
    return result, clicks, typed


def _checked(resumes, name):
    return [{**r, "checked": r["name"] == name} for r in resumes]


def test_the_chosen_resume_is_selected_before_applying_and_the_reason_is_recorded():
    seen = {}

    def chooser(job, names, default=""):
        seen["names"] = names
        return "agent应用工程师", "标签匹配选用「agent应用工程师」"

    result, clicks, _ = _apply_with_resumes(chooser, after_click_checked=_checked(RESUMES, "agent应用工程师"))
    assert result["success"] and clicks == ["1096,123", "540,389", "950,511"]         # 投简历, the right resume, 立即投递
    assert seen["names"] == ["ai应用工程师（微调）", "ai产品经理", "agent应用工程师"]
    assert "标签匹配选用" in result["history_detail"]


def test_the_preselected_resume_is_not_clicked_again_when_it_is_the_choice():
    result, clicks, _ = _apply_with_resumes(lambda job, names, default="": ("ai应用工程师（微调）", "选用微调"))
    assert result["success"] and clicks == ["1096,123", "950,511"]


def test_failed_resume_switch_does_not_apply():
    result, clicks, _ = _apply_with_resumes(lambda job, names, default="": ("agent应用工程师", "x"), after_click_checked=RESUMES)
    assert not result["success"] and result["error"] == "resume_select_failed" and "950,511" not in clicks


def test_no_matching_resume_means_no_application_and_degrades_to_a_greeting():
    nothing = lambda job, names, default="": (None, "没有简历的标签与岗位匹配到 0.20")                # noqa: E731
    result, clicks, typed = _apply_with_resumes(nothing, greeting=GREETING)                    # no switch needed
    assert result["success"] and result["degraded"] and typed == [GREETING] and "950,511" not in clicks
    assert "降级为打招呼" in result["history_detail"]
    result, clicks, typed = _apply_with_resumes(nothing)
    assert not result["success"] and result["error"] == "resume_not_found" and typed == [] and "950,511" not in clicks
