import subprocess
from types import SimpleNamespace

from bosshunter import agent_connect as ac


def test_a_path_is_shown_without_the_folders_of_the_machine():
    assert ac.display_path(r"E:\DATA\someone\Desktop\proj\venv\Scripts\python.exe") == ".../venv/Scripts/python.exe"
    assert ac.display_path("claude.exe") == "claude.exe"
    assert ac.display_path("") == ""
    assert "someone" not in ac.display_path(r"C:\Users\someone\AppData\Local\Claude\claude.exe")


def test_registered_reads_the_exit_code_of_mcp_get_and_survives_a_missing_cli():
    assert ac.registered("claude", lambda command, **kw: SimpleNamespace(returncode=0)) is True
    assert ac.registered("claude", lambda command, **kw: SimpleNamespace(returncode=1)) is False

    def broken(command, **kw):
        raise OSError("no such file")

    assert ac.registered("claude", broken) is None


def test_connect_replaces_the_old_entry_and_puts_the_token_into_the_registration_only():
    seen = []

    def runner(command, **kw):
        seen.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ok, message = ac.connect("claude", "python", "http://127.0.0.1:8686", "bh_secret", runner)
    assert ok and "重启" in message
    assert seen[0][:3] == ["claude", "mcp", "remove"] and seen[1][:3] == ["claude", "mcp", "add"]
    assert "BOSSHUNTER_TOKEN=bh_secret" in seen[1] and "--scope" in seen[1] and seen[1][seen[1].index("--") + 1] == "python"
    assert "bh_secret" not in message


def test_a_failed_registration_says_why_and_a_missing_cli_does_not_raise():
    ok, message = ac.connect("claude", "python", "u", "t", lambda command, **kw: SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    assert not ok and "boom" in message

    def broken(command, **kw):
        raise subprocess.TimeoutExpired(command, 1)

    ok, message = ac.connect("claude", "python", "u", "t", broken)
    assert not ok and "TimeoutExpired" in message
