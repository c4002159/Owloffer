import json
import subprocess
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from bosshunter import agent_duty as duty
from bosshunter.agent_access import AgentAccess


@pytest.fixture
def base(tmp_path):
    (tmp_path / "data").mkdir()
    return tmp_path


def _cli(tmp_path):
    path = tmp_path / "claude.exe"
    path.write_text("x")
    return str(path)


def test_the_switch_is_off_by_default_and_the_interval_has_a_floor(base):
    assert duty.settings(base)["enabled"] is False and duty.settings(base)["interval_minutes"] == 60
    assert duty.update(base, {"interval_minutes": 1})["interval_minutes"] == 10
    assert duty.due(base) is False
    duty.update(base, {"enabled": True})
    assert duty.due(base) is True                                       # on and never run: due now


def test_a_run_is_due_again_only_after_the_interval(base):
    duty.update(base, {"enabled": True, "interval_minutes": 30})
    now = datetime(2026, 10, 26, 12, 0)
    data = json.loads(duty.store_path(base).read_text(encoding="utf-8"))
    data["runs"] = [{"at": (now - timedelta(minutes=20)).isoformat(), "status": "ok", "summary": "x"}]
    duty.store_path(base).write_text(json.dumps(data), encoding="utf-8")
    assert duty.due(base, now) is False
    assert duty.due(base, now + timedelta(minutes=11)) is True


def test_the_agent_gets_only_the_programs_tools_and_a_token_that_is_not_the_users(base):
    command = duty.build_command("claude", "值班", duty.mcp_config("python", "http://127.0.0.1:8686", "bhd_x"))
    assert command[:3] == ["claude", "-p", "值班"] and "--strict-mcp-config" in command
    assert command[command.index("--allowedTools") + 1] == "mcp__bosshunter"
    disallowed = command[command.index("--disallowedTools") + 1:command.index("--max-turns")]
    assert {"Bash", "Edit", "Write"} <= set(disallowed)
    config = json.loads(command[command.index("--mcp-config") + 1])
    assert config["mcpServers"]["bosshunter"]["env"]["BOSSHUNTER_TOKEN"] == "bhd_x"


def test_ephemeral_tokens_work_for_a_while_and_leave_the_users_token_alone(tmp_path):
    access = AgentAccess(tmp_path / "access.json")
    own = access.generate_token()
    token = access.issue_ephemeral(20)
    assert access.verify(token) and access.verify(own) and not access.verify("bhd_other")
    access._ephemeral = {digest: 0.0 for digest in access._ephemeral}      # expired
    assert not access.verify(token) and access.verify(own)
    assert "bhd_" not in (tmp_path / "access.json").read_text(encoding="utf-8")        # never written to disk


def test_classify_tells_login_trouble_from_other_failures():
    assert duty.classify(1, "Not logged in · Please run /login")[0] == "login_required"
    assert duty.classify(1, "boom")[0] == "failed"
    assert duty.classify(0, "循环在运行")[0] == "ok"


def _run(base, tmp_path, runner, **kwargs):
    events = []
    duty.update(base, {"cli_path": _cli(tmp_path)})
    result = duty.run_once(base, python="python", base_url="http://x", issue_token=lambda minutes: "bhd_t",
                           emit=lambda *args, **kw: events.append((args, kw)), runner=runner, **kwargs)
    return result, events


def test_a_run_records_the_report_and_tells_the_message_center(base, tmp_path):
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout="循环在运行，今天投递 20 个。", stderr="")

    result, events = _run(base, tmp_path, runner)
    assert result["status"] == "ok" and "投递 20" in result["summary"]
    assert events and events[0][0][0] == "agent" and events[0][1]["level"] == "info"
    assert duty.state(base)["runs"][0]["summary"].startswith("循环在运行")
    assert seen["command"][1] == "-p"


def test_a_cli_that_is_not_logged_in_is_reported_as_such_and_a_check_leaves_no_record(base, tmp_path):
    result, events = _run(base, tmp_path, lambda command, **kw: SimpleNamespace(returncode=1, stdout="Not logged in · Please run /login", stderr=""))
    assert result["status"] == "login_required" and events[0][1]["level"] == "warning"
    checked, events = _run(base, tmp_path, lambda command, **kw: SimpleNamespace(returncode=0, stdout="好的", stderr=""), check_only=True)
    assert checked["status"] == "ok" and events == [] and len(duty.state(base)["runs"]) == 1


def test_no_cli_and_a_timeout_do_not_raise(base, tmp_path, monkeypatch):
    monkeypatch.setattr(duty, "find_cli", lambda configured="": None)
    result = duty.run_once(base, python="python", base_url="http://x", issue_token=lambda m: "t", emit=lambda *a, **k: None)
    assert result["status"] == "no_cli"
    monkeypatch.undo()

    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    result, _events = _run(base, tmp_path, runner)
    assert result["status"] == "failed" and "超过" in result["summary"]
