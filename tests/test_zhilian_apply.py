import json
from types import SimpleNamespace

from bosshunter.collection.capabilities import platform_supports
from bosshunter.executor.zhilian_apply import DELIVERED_TEXT, apply_zhilian

JOB = {"id": "zhilian:CC1J1", "source_platform": "zhilian", "url": "https://www.zhaopin.com/jobdetail/CC1J1.htm"}
CONFIG = {"browse_before_greet": False}
GREETING = "您好，我对这个岗位很感兴趣，期待进一步交流。"


def _page(**values):
    base = {"url": JOB["url"], "captcha": False, "closed": False, "applyLabel": "立即投递", "applyX": 1500, "applyY": 330,
            "applyClickable": True, "modal": False, "modalText": "", "stayX": 0, "stayY": 0, "stayClickable": False,
            "nextX": 0, "nextY": 0, "nextClickable": False}
    base.update(values)
    return json.dumps(base)


DIALOG = _page(modal=True, modalText=f"{DELIVERED_TEXT} 您好，我仔细阅读了您发布的这个职位", applyLabel="继续沟通",
               stayX=1019, stayY=494, stayClickable=True, nextX=1165, nextY=494, nextClickable=True)


def _chat(messages=(), typed="", ready=True):
    return json.dumps({"url": "https://i.zhaopin.com/im?sessionId=1", "inputReady": ready, "inputX": 900, "inputY": 580,
                       "inputOk": ready, "sendReady": ready, "sendX": 1300, "sendY": 580, "sendOk": ready,
                       "typed": typed, "messages": list(messages)})


class FakeBrowser:
    """A scripted page: ``pages`` are the answers of the job tab's state, ``chat`` those of the chat tab."""

    def __init__(self, pages, chat=None, open_chat=True):
        self.pages, self.chat, self.open_chat = list(pages), list(chat or []), open_chat
        self.clicks, self.closed, self.typed, self.opened_chat = [], [], [], False

    def evaluate(self, tid, js):
        queue = self.chat if tid == "chat" else self.pages
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def click_at(self, tid, xy):
        self.clicks.append((tid, xy))
        if tid == "tab1" and xy == "1165,494" and self.open_chat:
            self.opened_chat = True
        if tid == "chat" and xy == "1300,580" and self.typed:
            self.chat[:] = [_chat(messages=[self.typed[-1]])]        # sending puts the text into the conversation
        return True

    def get_page_targets(self):
        return [{"targetId": "chat", "url": "https://i.zhaopin.com/im?sessionId=1"}] if self.opened_chat else []

    def type_text(self, tid, text):
        self.typed.append(text)
        self.chat.insert(0, _chat(typed=text))          # the typed text shows in the input
        return True

    def namespace(self):
        return SimpleNamespace(
            new_tab=lambda url, background=False: "tab1", close_tab=lambda tid: self.closed.append(tid) or True,
            evaluate=self.evaluate, click_at=self.click_at, get_page_targets=self.get_page_targets,
            wait_for_load=lambda tid, timeout=15: True, type_text=self.type_text,
        )


def _apply(fake, greeting="", **config):
    result, _ = apply_zhilian(JOB, {**CONFIG, **config}, sleep=lambda _s: None, browser=fake.namespace(), greeting=greeting)
    return result


def test_delivery_stays_locked_until_a_real_application_has_been_verified():
    assert not platform_supports("zhilian", "deliver")          # flip this (capabilities.py) after the first verified send
    assert not platform_supports("zhilian_campus", "deliver")


def test_apply_click_then_the_platforms_own_confirmation_counts_as_sent():
    fake = FakeBrowser([_page(), _page(), DIALOG])
    result = _apply(fake)
    assert result["success"] and DELIVERED_TEXT in result["history_detail"]
    assert fake.clicks[0] == ("tab1", "1500,330")                 # 立即投递
    assert ("tab1", "1019,494") in fake.clicks and ("tab1", "1165,494") not in fake.clicks   # 留在此页, not 继续沟通
    assert "tab1" in fake.closed


def test_the_customized_greeting_is_sent_in_the_chat_after_continue_chatting():
    fake = FakeBrowser([_page(), _page(), DIALOG], chat=[_chat(), _chat()])
    result = _apply(fake, GREETING)
    assert result["success"] and result["greeting_sent"] is True
    assert fake.typed == [GREETING]
    assert ("tab1", "1165,494") in fake.clicks                      # 继续沟通 opened the chat
    assert ("chat", "1300,580") in fake.clicks                      # then 发送
    assert "chat" in fake.closed and "tab1" in fake.closed


def test_a_greeting_that_never_shows_in_the_chat_is_reported_not_counted_as_sent():
    class Silent(FakeBrowser):
        def type_text(self, tid, text):
            self.typed.append(text)
            self.chat[:] = [_chat(typed=text)]                       # stays in the input: the send did nothing
            return True

        def click_at(self, tid, xy):
            self.clicks.append((tid, xy))
            if tid == "tab1" and xy == "1165,494":
                self.opened_chat = True
            return True

    fake = Silent([_page(), _page(), DIALOG], chat=[_chat()])
    result = _apply(fake, GREETING)
    assert result["success"] and result["greeting_sent"] is False and "没有看到这条招呼语" in result["history_detail"]


def test_no_confirmation_is_not_counted_as_sent():
    fake = FakeBrowser([_page(), _page()])                          # nothing changes after the click
    result = _apply(fake)
    assert not result["success"] and result["error"] == "apply_not_confirmed"


def test_a_job_that_already_reads_continue_chatting_is_not_applied_to_twice():
    fake = FakeBrowser([_page(applyLabel="继续沟通")])
    result = _apply(fake)
    assert not result["success"] and result["error"] == "already_contacted" and fake.clicks == []


def test_captcha_closed_and_missing_button_never_click():
    for state, error in ((_page(captcha=True), "captcha"), (_page(closed=True, applyLabel=""), "job_page_unavailable"),
                         (_page(applyLabel="投递简历中"), "no_apply_button")):
        fake = FakeBrowser([state])
        result = _apply(fake)
        assert result["error"] == error and fake.clicks == []


def test_campus_postings_are_never_applied_to_here():
    fake = FakeBrowser([_page()])
    result, _ = apply_zhilian({**JOB, "source_platform": "zhilian_campus"}, CONFIG, sleep=lambda _s: None, browser=fake.namespace())
    assert result["error"] == "campus_is_manual" and fake.clicks == []


def test_a_covered_apply_button_is_not_clicked():
    fake = FakeBrowser([_page(applyClickable=False)])
    result = _apply(fake)
    assert result["error"] == "apply_button_not_clickable" and fake.clicks == []
