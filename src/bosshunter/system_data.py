"""Backups of the program's data, and the program's own log files.

A backup is one zip: the database (copied with SQLite's online backup, safe while the program runs), the settings file and the
resume files. API keys live in a separate credentials file and are not included; neither is the agent token.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

BACKUP_PATTERN = re.compile(r"^owloffer-backup-\d{8}-\d{6}(-[a-z-]+)?\.zip$")
LOG_PATTERN = re.compile(r"^[\w.-]+\.log$")
KEEP_DEFAULT = 14
TAIL_BYTES = 3 * 1024 * 1024
LEVELS = {"error": ("error", "exception", "traceback", "failed", "错误", "异常", "失败"), "warning": ("warn", "警告")}
PRIVATE_PROFILE_KEYS = ("deal_breakers", "jd_deal_breakers", "blocked_companies", "fixed_greeting", "greeting_preference", "resumes", "resume_path", "resume_output_dir")


def backups_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "backups"


# ------------------------------------------------------------------------------------------------------ backups
def create_backup(base_dir: Path, data_dir: Path, config_path: Path, resume_dir: Path, *, keep: int = KEEP_DEFAULT, tag: str = "") -> dict[str, Any]:
    folder = backups_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"owloffer-backup-{stamp}{('-' + tag) if tag else ''}.zip"
    target = folder / name
    db_path = Path(data_dir) / "bosshunter.db"
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "bosshunter.db"
        if db_path.is_file():
            source = sqlite3.connect(str(db_path))
            dest = sqlite3.connect(str(copy))
            try:
                source.backup(dest)
            finally:
                dest.close()
                source.close()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            if copy.is_file():
                archive.write(copy, "bosshunter.db")
            if Path(config_path).is_file():
                archive.write(config_path, "config.yaml")
            files = 0
            if Path(resume_dir).is_dir():
                for path in sorted(Path(resume_dir).rglob("*")):
                    if path.is_file():
                        archive.write(path, f"resumes/{path.relative_to(resume_dir).as_posix()}")
                        files += 1
            archive.writestr("manifest.json", json.dumps({"created": datetime.now().isoformat(timespec="seconds"), "resume_files": files, "tag": tag}, ensure_ascii=False))
    if keep > 0:
        prune_backups(data_dir, keep)
    return describe_backup(target)


def describe_backup(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "created": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")}


def list_backups(data_dir: Path) -> list[dict[str, Any]]:
    folder = backups_dir(data_dir)
    if not folder.is_dir():
        return []
    items = [describe_backup(path) for path in folder.iterdir() if path.is_file() and BACKUP_PATTERN.match(path.name)]
    return sorted(items, key=lambda item: item["created"], reverse=True)


def prune_backups(data_dir: Path, keep: int) -> int:
    """Delete the oldest backups beyond ``keep`` (safety backups made before a restore are kept apart from the count)."""
    removed = 0
    regular = [item for item in list_backups(data_dir) if "-pre-restore" not in item["name"]]
    for item in regular[keep:]:
        (backups_dir(data_dir) / item["name"]).unlink(missing_ok=True)
        removed += 1
    return removed


def backup_path(data_dir: Path, name: str) -> Path | None:
    """The file of a backup by name; None when the name is not one of ours (no path tricks)."""
    if not BACKUP_PATTERN.match(name or ""):
        return None
    path = backups_dir(data_dir) / name
    return path if path.is_file() else None


def delete_backup(data_dir: Path, name: str) -> bool:
    path = backup_path(data_dir, name)
    if path is None:
        return False
    path.unlink()
    return True


def restore_backup(base_dir: Path, data_dir: Path, config_path: Path, resume_dir: Path, name: str) -> dict[str, Any]:
    """Put a backup back. A safety backup of the present state is made first."""
    path = backup_path(data_dir, name)
    if path is None:
        raise FileNotFoundError("找不到这个备份")
    safety = create_backup(base_dir, data_dir, config_path, resume_dir, keep=0, tag="pre-restore")
    restored: list[str] = []
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as tmp:
        names = set(archive.namelist())
        if "bosshunter.db" in names:
            archive.extract("bosshunter.db", tmp)
            source = sqlite3.connect(str(Path(tmp) / "bosshunter.db"))
            dest = sqlite3.connect(str(Path(data_dir) / "bosshunter.db"))
            try:
                source.backup(dest)                         # into the live database, in place: nothing is replaced under the program
            finally:
                dest.close()
                source.close()
            restored.append("数据库")
        if "config.yaml" in names:
            archive.extract("config.yaml", tmp)
            shutil.copyfile(Path(tmp) / "config.yaml", config_path)
            restored.append("配置")
        resumes = [n for n in names if n.startswith("resumes/") and not n.endswith("/")]
        for member in resumes:
            relative = Path(member[len("resumes/"):])
            if ".." in relative.parts:
                continue                                    # never write outside the resume folder
            target = Path(resume_dir) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source_file, open(target, "wb") as target_file:
                shutil.copyfileobj(source_file, target_file)
        if resumes:
            restored.append(f"简历文件 {len(resumes)} 个")
    return {"restored": restored, "safety_backup": safety["name"]}


class BackupScheduler:
    """Makes one backup a day while enabled (checked every few minutes)."""

    def __init__(self, make_backup, settings, *, interval: float = 600.0):
        self.make_backup, self.settings, self.interval = make_backup, settings, interval
        self._stop = threading.Event()

    def due(self, last: float | None, now: float) -> bool:
        enabled, _keep = self.settings()
        return bool(enabled) and (last is None or now - last >= 24 * 3600)

    def run(self, last_backup_time) -> None:
        while not self._stop.is_set():
            try:
                if self.due(last_backup_time(), time.time()):
                    self.make_backup()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def start(self, last_backup_time) -> None:
        threading.Thread(target=self.run, args=(last_backup_time,), name="backup-scheduler", daemon=True).start()


# ------------------------------------------------------------------------------------------------------ logs
def log_files(base_dir: Path, data_dir: Path) -> list[dict[str, Any]]:
    """The program's log files (newest first): web*.log next to the project and in its folders, and data/logs/*.log."""
    found: dict[str, Path] = {}
    for folder in (Path(data_dir) / "logs", Path(base_dir), Path(base_dir).parent):
        if not folder.is_dir():
            continue
        for path in folder.glob("*.log"):
            if path.is_file() and LOG_PATTERN.match(path.name) and (folder.name == "logs" or path.name.startswith("web")):
                found.setdefault(path.name, path)
    items = [{"name": name, "size": path.stat().st_size, "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")} for name, path in found.items()]
    return sorted(items, key=lambda item: item["modified"], reverse=True)[:40]


def log_path(base_dir: Path, data_dir: Path, name: str) -> Path | None:
    if not LOG_PATTERN.match(name or ""):
        return None
    for folder in (Path(data_dir) / "logs", Path(base_dir), Path(base_dir).parent):
        path = folder / name
        if path.is_file() and (folder.name == "logs" or name.startswith("web")):
            return path
    return None


def tail_lines(path: Path, lines: int = 300, level: str = "", query: str = "") -> dict[str, Any]:
    """The last ``lines`` matching lines of a log (reads only its end, so big logs are fine)."""
    size = path.stat().st_size
    with open(path, "rb") as handle:
        handle.seek(max(0, size - TAIL_BYTES))
        raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    if size > TAIL_BYTES and all_lines:
        all_lines = all_lines[1:]                           # the first line is cut in the middle
    markers = LEVELS.get(level, ())
    needle = query.strip().lower()
    chosen = [line for line in all_lines if (not markers or any(m in line.lower() for m in markers)) and (not needle or needle in line.lower())]
    limit = max(1, min(int(lines), 2000))
    return {"lines": chosen[-limit:], "matched": len(chosen), "size": size, "truncated": size > TAIL_BYTES}


# ------------------------------------------------------------------------------------------------------ diagnostics
def redacted_config(config: dict[str, Any]) -> dict[str, Any]:
    """The settings without anything personal or secret: for sharing when asking for help."""
    clean = json.loads(json.dumps(config, ensure_ascii=False, default=str))
    profile = clean.get("profile")
    if isinstance(profile, dict):
        for key in PRIVATE_PROFILE_KEYS:
            if key in profile:
                profile[key] = "<已省略>"
    ai = clean.get("ai")
    if isinstance(ai, dict):
        for key in list(ai):
            if re.search(r"(key|token|secret|password)", key, re.I):
                ai[key] = "<已省略>"
    return clean


def diagnostics_zip(base_dir: Path, data_dir: Path, config: dict[str, Any], version: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("info.json", json.dumps({"version": version, "created": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=1))
        archive.writestr("config.redacted.json", json.dumps(redacted_config(config), ensure_ascii=False, indent=1))
        for item in log_files(base_dir, data_dir)[:3]:
            path = log_path(base_dir, data_dir, item["name"])
            if path is not None:
                archive.writestr(f"logs/{item['name']}", "\n".join(tail_lines(path, lines=2000)["lines"]))
    return buffer.getvalue()
