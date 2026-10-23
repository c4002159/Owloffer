import json
from types import SimpleNamespace

from bosshunter.executor.job51_apply import apply_51job

JOB = {"id": "51job:1", "url": "https://jobs.51job.com/shanghai/1.html"}
CONFIG = {"browse_before_greet": False}


def _state(label="", clickable=True, captcha=False, closed=False):
    return json.dumps({"label": label, "x": 900, "y": 160, "clickable": clickable, "captcha": captcha, "closed": closed})


def _browser(states, clicks, closed_tabs, extra_targets=()):
    """Fake browser: every evaluate() pops the next page state; the last one repeats."""
    queue = list(states)
    return SimpleNamespace(
        new_tab=lambda url, background=False: "tab1",
        close_tab=lambda tid: closed_tabs.append(tid) or True,
        evaluate=lambda tid, js: queue.pop(0) if len(queue) > 1 else queue[0],
        click_at=lambda tid, xy: clicks.append(xy) or True,
        navigate=lambda tid, url: True,
        get_page_targets=lambda: [{"targetId": "chat", "url": "https://i.51job.com/chat"}] if clicks else list(extra_targets),
        wait_for_load=lambda tid, timeout=15: True,
    )


def _apply(states):
    clicks, closed = [], []
    result, _ = apply_51job(JOB, CONFIG, sleep=lambda _s: None, browser=_browser(states, clicks, closed))
    return result, clicks, closed


def test_success_only_after_the_page_shows_already_applied():
    result, clicks, closed = _apply([_state("立即投递"), _state("立即投递"), _state("已投递")])
    assert result["success"] and clicks == ["900,160"]
    assert "tab1" in closed and "chat" in closed        # the WeChat-QR page opened by the click is closed too


def test_button_that_never_turns_into_applied_is_not_counted_as_sent():
    result, clicks, _ = _apply([_state("立即投递")])
    assert not result["success"] and result["error"] == "apply_not_confirmed" and len(clicks) == 1


def test_already_applied_job_is_not_clicked_again():
    result, clicks, _ = _apply([_state("已投递")])
    assert result["success"] and result.get("already_applied") and clicks == []


def test_covered_button_missing_button_closed_job_and_captcha_never_click():
    for states, error in [
        ([_state("立即投递", clickable=False)], "apply_button_not_clickable"),
        ([_state("")], "no_apply_button"),
        ([_state("", closed=True)], "job_page_unavailable"),
        ([_state("", captcha=True)], "captcha"),
    ]:
        result, clicks, closed = _apply(states)
        assert not result["success"] and result["error"] == error and clicks == [] and "tab1" in closed


def test_stop_request_prevents_the_click():
    clicks, closed = [], []
    result, _ = apply_51job(JOB, CONFIG, sleep=lambda _s: None, stop_requested=lambda: True,
                            browser=_browser([_state("立即投递")], clicks, closed))
    assert result["error"] == "stopped" and clicks == []


def test_a_failed_confirmation_keeps_what_the_click_opened_as_a_hint():
    from bosshunter.executor.job51_apply import POPUP_TEXT_JS

    clicks, closed = [], []
    browser = _browser([_state("立即投递")], clicks, closed)
    browser.evaluate = lambda tid, js: "请使用微信扫码继续" if js is POPUP_TEXT_JS else _state("立即投递")
    result, _ = apply_51job(JOB, CONFIG, sleep=lambda _s: None, browser=browser)
    assert not result["success"] and result["error"] == "apply_not_confirmed"
    assert "请使用微信扫码继续" in result["history_detail"]
