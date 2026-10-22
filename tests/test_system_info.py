import sqlite3
import tempfile
import unittest
from pathlib import Path

from bosshunter.web.system_info import make_backup, recent_task_logs, system_info


class SystemInfoTests(unittest.TestCase):
	def test_info_reports_paths_and_database_size(self):
		with tempfile.TemporaryDirectory() as tmp:
			base = Path(tmp)
			data = base / "data"
			data.mkdir()
			sqlite3.connect(data / "bosshunter.db").close()
			info = system_info(base, data, base / "config.yaml", "1.0")
			self.assertEqual(info["paths"]["database"], str(data / "bosshunter.db"))
			self.assertTrue(info["database"]["exists"])
			self.assertIn("mismatch", info["timezone"])

	def test_backup_is_consistent_copy(self):
		with tempfile.TemporaryDirectory() as tmp:
			db = Path(tmp) / "bosshunter.db"
			conn = sqlite3.connect(db)
			conn.execute("create table t (x integer)")
			conn.execute("insert into t values (42)")
			conn.commit()
			conn.close()
			backup = make_backup(db)
			rows = sqlite3.connect(backup).execute("select x from t").fetchall()
			self.assertEqual(rows, [(42,)])

	def test_backup_requires_database(self):
		with tempfile.TemporaryDirectory() as tmp:
			with self.assertRaises(FileNotFoundError):
				make_backup(Path(tmp) / "missing.db")

	def test_recent_logs_newest_first_and_limited(self):
		tasks = [{"id": str(i)} for i in range(30)]
		out = recent_task_logs({"tasks": tasks}, limit=5)
		self.assertEqual([t["id"] for t in out], ["29", "28", "27", "26", "25"])
