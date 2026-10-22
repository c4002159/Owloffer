import json
import sqlite3
import time
import zipfile

import pytest

from bosshunter import system_data as sd


@pytest.fixture
def project(tmp_path):
    base = tmp_path / "project"
    data = base / "data"
    resumes = data / "resumes"
    resumes.mkdir(parents=True)
    conn = sqlite3.connect(str(data / "bosshunter.db"))
    conn.execute("CREATE TABLE jobs (id TEXT, title TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('1', 'Agent工程师')")
    conn.commit()
    conn.close()
    (base / "config.yaml").write_text("profile:\n  salary_min: 10\n", encoding="utf-8")
    (resumes / "main.md").write_text("# 简历", encoding="utf-8")
    return base, data, base / "config.yaml", resumes


def test_a_backup_holds_the_database_settings_and_resumes_and_old_ones_are_pruned(project):
    base, data, config, resumes = project
    first = sd.create_backup(base, data, config, resumes, keep=2)
    assert sd.BACKUP_PATTERN.match(first["name"])
    with zipfile.ZipFile(data / "backups" / first["name"]) as archive:
        assert {"bosshunter.db", "config.yaml", "resumes/main.md", "manifest.json"} <= set(archive.namelist())
    for index in range(3):
        time.sleep(1.1)                                              # file names carry the second
        sd.create_backup(base, data, config, resumes, keep=2)
    assert len(sd.list_backups(data)) == 2
    assert sd.backup_path(data, "../config.yaml") is None and sd.backup_path(data, "nope.zip") is None


def test_restoring_puts_everything_back_after_making_a_safety_backup(project):
    base, data, config, resumes = project
    name = sd.create_backup(base, data, config, resumes)["name"]
    conn = sqlite3.connect(str(data / "bosshunter.db"))
    conn.execute("DELETE FROM jobs")
    conn.commit()
    conn.close()
    config.write_text("profile:\n  salary_min: 99\n", encoding="utf-8")
    (resumes / "main.md").write_text("坏了", encoding="utf-8")
    time.sleep(1.1)
    result = sd.restore_backup(base, data, config, resumes, name)
    assert set(result["restored"]) >= {"数据库", "配置"} and "pre-restore" in result["safety_backup"]
    assert sqlite3.connect(str(data / "bosshunter.db")).execute("SELECT title FROM jobs").fetchall() == [("Agent工程师",)]
    assert "salary_min: 10" in config.read_text(encoding="utf-8") and (resumes / "main.md").read_text(encoding="utf-8") == "# 简历"
    with pytest.raises(FileNotFoundError):
        sd.restore_backup(base, data, config, resumes, "owloffer-backup-20200101-000000.zip")


def test_a_backup_is_due_once_a_day_only_while_enabled():
    scheduler = sd.BackupScheduler(lambda: None, lambda: (True, 14))
    assert scheduler.due(None, 1000.0) and not scheduler.due(1000.0, 1000.0 + 3600) and scheduler.due(1000.0, 1000.0 + 25 * 3600)
    assert not sd.BackupScheduler(lambda: None, lambda: (False, 14)).due(None, 1000.0)


def test_logs_are_listed_tailed_filtered_and_never_read_outside_their_places(project):
    base, data, config, resumes = project
    (base.parent / "web1.log").write_text("\n".join(f"line {i}" for i in range(50)) + "\nERROR 出错了\nok\n", encoding="utf-8")
    (base / "notes.log").write_text("not a program log", encoding="utf-8")
    (base / "secret.txt").write_text("x", encoding="utf-8")
    names = [item["name"] for item in sd.log_files(base, data)]
    assert names == ["web1.log"]
    path = sd.log_path(base, data, "web1.log")
    assert sd.tail_lines(path, lines=3)["lines"] == ["line 49", "ERROR 出错了", "ok"]
    assert sd.tail_lines(path, level="error")["lines"] == ["ERROR 出错了"]
    assert sd.tail_lines(path, query="LINE 7")["lines"] == ["line 7"]
    assert sd.log_path(base, data, "../config.yaml") is None and sd.log_path(base, data, "notes.log") is None


def test_the_diagnostic_package_has_logs_and_a_config_without_personal_or_secret_values(project):
    base, data, config, resumes = project
    (base.parent / "web2.log").write_text("hello\n", encoding="utf-8")
    cfg = {"profile": {"salary_min": 10, "blocked_companies": ["某公司"], "resume_path": "C:/me.md"}, "ai": {"api_key": "sk-1", "model": "m"}}
    import io

    data_bytes = sd.diagnostics_zip(base, data, cfg, "2.4.0")
    with zipfile.ZipFile(io.BytesIO(data_bytes)) as archive:
        text = archive.read("config.redacted.json").decode("utf-8")
        assert "sk-1" not in text and "某公司" not in text and "C:/me.md" not in text
        assert json.loads(text)["profile"]["salary_min"] == 10 and json.loads(text)["ai"]["model"] == "m"
        assert "logs/web2.log" in archive.namelist()


def test_the_system_routes_back_up_list_download_restore_and_show_logs(project):
    import io

    from bosshunter.web import server

    from bosshunter.db import get_db

    base, data, config, resumes = project
    (data / "bosshunter.db").unlink()                      # the program's own database, not the tiny one the other tests use
    get_db(data / "bosshunter.db").close()
    server.set_base_dir(base)
    (base.parent / "web9.log").write_text("a\nERROR b\n", encoding="utf-8")

    def call(path, method="GET", body=None):
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        captured = {}
        environ = {"REMOTE_ADDR": "127.0.0.1", "REQUEST_METHOD": method, "PATH_INFO": path.split("?")[0], "QUERY_STRING": path.partition("?")[2],
                   "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8686", "HTTP_HOST": "127.0.0.1:8686", "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
                   "wsgi.input": io.BytesIO(raw), "wsgi.errors": io.StringIO(), "wsgi.multithread": False, "wsgi.multiprocess": False, "wsgi.run_once": False}
        if body is not None:
            environ["CONTENT_LENGTH"] = str(len(raw))
            environ["CONTENT_TYPE"] = "application/json"
        chunks = b"".join(c if isinstance(c, bytes) else c.encode("utf-8") for c in server.app(environ, lambda s, h, e=None: captured.update(status=s)))
        return captured["status"], chunks

    status, body = call("/api/system/backups", "POST", {})
    name = json.loads(body)["name"]
    assert status.startswith("200") and json.loads(call("/api/system/backups")[1])["backups"][0]["name"] == name
    status, body = call("/api/system/backups/download?name=" + name)
    assert status.startswith("200") and body[:2] == b"PK"
    assert call("/api/system/backups/download?name=../x")[0].startswith("404")
    assert call("/api/system/backups/restore", "POST", {"name": name})[0].startswith("400")           # needs confirmed
    time.sleep(1.1)
    status, body = call("/api/system/backups/restore", "POST", {"name": name, "confirmed": True})
    assert status.startswith("200") and "pre-restore" in json.loads(body)["safety_backup"]
    assert json.loads(call("/api/system/logs")[1])["logs"][0]["name"] == "web9.log"
    assert json.loads(call("/api/system/logs/tail?name=web9.log&level=error")[1])["lines"] == ["ERROR b"]
    assert call("/api/system/logs/tail?name=../config.yaml")[0].startswith("404")
    status, body = call("/api/system/diagnostics")
    assert status.startswith("200") and body[:2] == b"PK"
    assert call("/api/system/backups?name=" + name, "DELETE")[0].startswith("200")
