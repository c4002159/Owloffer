from datetime import datetime, timedelta

import pytest

from bosshunter.agent_access import AccessDenied, AgentAccess


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 22, 9, 0)

    def __call__(self):
        return self.t


def _access(tmp_path, clock=None):
    return AgentAccess(tmp_path / "access.json", now=clock or Clock())


def test_a_token_is_shown_once_stored_as_a_hash_and_checked(tmp_path):
    access = _access(tmp_path)
    assert not access.has_token() and not access.verify("anything")
    token = access.generate_token()
    assert access.verify(token) and not access.verify(token + "x") and not access.verify("")
    assert token not in (tmp_path / "access.json").read_text(encoding="utf-8")
    new = access.generate_token()
    assert not access.verify(token) and access.verify(new)           # a new token replaces the old one
    access.revoke_token()
    assert not access.verify(new)


def test_the_default_is_to_let_the_agent_act_and_reading_is_always_allowed(tmp_path):
    access = _access(tmp_path)
    assert access.mode == "auto"
    assert access.authorize("status") == "allow" and access.authorize("deliver") == "allow"
    with pytest.raises(AccessDenied):
        access.authorize("format_disk")


def test_ask_mode_turns_control_into_requests_and_readonly_refuses_it(tmp_path):
    access = _access(tmp_path)
    access.set_mode("ask")
    assert access.authorize("deliver") == "ask" and access.authorize("events") == "allow"
    access.set_mode("readonly")
    with pytest.raises(AccessDenied) as error:
        access.authorize("deliver")
    assert error.value.code == "read_only"
    assert access.authorize("status") == "allow"


def test_turning_the_permission_off_again_and_again_moves_the_mode_down(tmp_path):
    clock = Clock()
    access = _access(tmp_path, clock)
    for _ in range(3):
        access.set_enabled(False)
        access.set_enabled(True)
    assert access.mode == "ask"                                       # three objections: auto -> ask
    for _ in range(3):
        access.set_enabled(False)
        access.set_enabled(True)
    assert access.mode == "readonly"


def test_denied_requests_count_as_objections_and_old_objections_expire(tmp_path):
    clock = Clock()
    access = _access(tmp_path, clock)
    access.set_mode("ask")
    for _ in range(2):
        request = access.create_request("deliver", {"scope": "approved"}, "claude")
        access.decide(request["id"], approve=False)
    clock.t += timedelta(days=15)                                     # the first two no longer count
    request = access.create_request("deliver", {}, "codex")
    access.decide(request["id"], approve=False)
    assert access.mode == "ask"
    request = access.create_request("deliver", {}, "codex")
    assert access.decide(request["id"], approve=True)["status"] == "approved"
    assert access.mode == "ask" and access.pending_requests() == []


def test_a_mode_chosen_by_hand_starts_the_count_afresh_and_events_are_readable_in_order(tmp_path):
    access = _access(tmp_path)
    access.set_enabled(False)
    access.set_enabled(True)
    assert access.public_state()["objections"] == 1
    access.set_mode("auto")
    assert access.public_state()["objections"] == 0
    first = access.emit("loop", "每日循环结束")
    access.emit("incident", "BOSS 被限流", level="warning", platform="boss")
    news = access.events_since(first)
    assert [e["kind"] for e in news["events"]] == ["incident"] and news["latest"] >= first + 1
    assert access.events_since(0, limit=1)["events"][0]["id"] < news["latest"]


def test_the_state_survives_a_restart(tmp_path):
    clock = Clock()
    access = _access(tmp_path, clock)
    token = access.generate_token()
    access.set_mode("ask")
    again = _access(tmp_path, clock)
    assert again.verify(token) and again.mode == "ask"
