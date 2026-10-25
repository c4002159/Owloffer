from datetime import date

from bosshunter import reminders


def test_a_reminder_is_delivered_once_at_the_first_start_on_or_after_its_day(tmp_path):
    path = tmp_path / "reminders.json"
    reminders.add(path, "step2", "2026-10-27", "第二步", "按到达速度决定多久再看一次", report="depth")
    sent = []
    emit = lambda kind, message, **kw: sent.append((kind, message, kw))
    assert reminders.deliver_due(path, emit, {"depth": lambda: "报告内容"}, today=date(2026, 10, 26)) == []        # not yet
    assert sent == []
    texts = reminders.deliver_due(path, emit, {"depth": lambda: "报告内容"}, today=date(2026, 10, 28))            # a start after the day
    assert len(texts) == 1 and "第二步" in texts[0] and "报告内容" in texts[0]
    assert sent[0][0] == "todo" and sent[0][2]["level"] == "info"
    assert reminders.deliver_due(path, emit, {"depth": lambda: "报告内容"}, today=date(2026, 10, 29)) == []       # only once
    assert len(sent) == 1


def test_a_failing_report_does_not_stop_the_reminder_and_an_undelivered_one_is_tried_again(tmp_path):
    path = tmp_path / "reminders.json"
    reminders.add(path, "a", "2026-10-27", "标题", "正文", report="depth")

    def broken():
        raise RuntimeError("x")

    sent = []
    texts = reminders.deliver_due(path, lambda *a, **k: sent.append(a), {"depth": broken}, today=date(2026, 10, 27))
    assert len(texts) == 1 and "报告生成失败" in texts[0]
    reminders.add(path, "b", "2026-10-27", "另一条", "正文")

    def refuse(*_a, **_k):
        raise OSError("feed unavailable")

    assert reminders.deliver_due(path, refuse, None, today=date(2026, 10, 27)) == []
    assert [item["id"] for item in reminders.due(path, date(2026, 10, 27))] == ["b"]                       # still waiting for its delivery
    assert reminders._load(tmp_path / "missing.json") == []
