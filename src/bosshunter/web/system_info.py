"""系统页所需的本机信息：时区、数据库、备份。"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

EXPECTED_TZ = "Asia/Shanghai"
EXPECTED_OFFSET_MINUTES = 480


def _local_timezone() -> tuple[str, int]:
	now = datetime.now().astimezone()
	offset = now.utcoffset()
	minutes = int(offset.total_seconds() // 60) if offset else 0
	name = now.tzname() or ""
	try:
		import time as _time
		import os

		name = os.environ.get("TZ") or (Path("/etc/timezone").read_text().strip() if Path("/etc/timezone").exists() else "") or name or _time.tzname[0]
	except Exception:
		pass
	return name, minutes


def _fmt_offset(minutes: int) -> str:
	sign = "+" if minutes >= 0 else "-"
	hours, mins = divmod(abs(minutes), 60)
	return f"UTC{sign}{hours:02d}:{mins:02d}"


def system_info(base_dir: Path, data_dir: Path, config_path: Path, version: str) -> dict:
	name, minutes = _local_timezone()
	db_path = data_dir / "bosshunter.db"
	mismatch = minutes != EXPECTED_OFFSET_MINUTES
	return {
		"version": version,
		"timezone": {
			"name": name,
			"offset": _fmt_offset(minutes),
			"expected": EXPECTED_TZ,
			"mismatch": mismatch,
			"warning": (
				f"本机时区是 {_fmt_offset(minutes)}，与上海时间（UTC+08:00）不一致。"
				"发送时间窗口和采集时间窗口按本机时间计算，请确认是否符合预期。"
			) if mismatch else None,
			"local_now": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
		},
		"paths": {
			"base_dir": str(base_dir),
			"config": str(config_path),
			"database": str(db_path),
		},
		"database": {
			"exists": db_path.exists(),
			"size_bytes": db_path.stat().st_size if db_path.exists() else 0,
		},
	}


def make_backup(db_path: Path) -> Path:
	"""用 SQLite 在线备份生成一份一致的数据库副本，返回临时文件路径。"""
	if not db_path.exists():
		raise FileNotFoundError("数据库还不存在")
	tmp = Path(tempfile.mkdtemp(prefix="offerowl-backup-")) / f"bosshunter-backup-{datetime.now():%Y%m%d-%H%M%S}.db"
	src = sqlite3.connect(str(db_path))
	try:
		dst = sqlite3.connect(str(tmp))
		try:
			src.backup(dst)
		finally:
			dst.close()
	finally:
		src.close()
	return tmp


def recent_task_logs(runner_status: dict, limit: int = 20) -> list[dict]:
	"""最近的后台任务及其日志（仅本次服务运行期间，存于内存）。"""
	tasks = list(runner_status.get("tasks") or [])
	return list(reversed(tasks[-limit:]))
