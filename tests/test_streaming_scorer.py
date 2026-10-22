import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from bosshunter.collection.models import JobCandidate, PlatformCollectionResult
from bosshunter.collection.orchestrator import CollectionOrchestrator
from bosshunter.collection.registry import CollectorRegistry
from bosshunter.collection.streaming_scorer import StreamingScorer
from bosshunter.db import get_db

from tests.test_collection_orchestrator import _options


def _wait(condition, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


class StreamingScorerTests(TestCase):
    def test_ids_are_scored_in_batches_once_each_and_the_rest_when_collection_ends(self):
        batches = []
        scorer = StreamingScorer(lambda ids: batches.append(list(ids)) or 0, batch_size=8, max_wait=60)
        scorer.start()
        for number in range(20):
            scorer.submit(f"j{number}")
        scorer.submit("j3")                                                   # the same posting twice is still one
        stats = scorer.finish(5)
        self.assertEqual([len(batch) for batch in batches], [8, 8, 4])
        self.assertEqual(sorted(job for batch in batches for job in batch), sorted(f"j{n}" for n in range(20)))
        self.assertEqual((stats["submitted"], stats["batches"], stats["errors"], stats["left_over"]), (20, 3, 0, 0))

    def test_a_small_queue_is_not_held_back_forever(self):
        batches = []
        scorer = StreamingScorer(lambda ids: batches.append(list(ids)) or 0, batch_size=8, max_wait=0.1)
        scorer.start()
        for number in range(3):
            scorer.submit(f"j{number}")
        self.assertTrue(_wait(lambda: batches))                               # flushed by the wait, before finish()
        scorer.finish(5)
        self.assertEqual(batches, [["j0", "j1", "j2"]])

    def test_a_failing_batch_never_stops_the_collector_and_backs_off(self):
        calls = []

        def broken(ids):
            calls.append(list(ids))
            raise RuntimeError("AI is down")

        logs = []
        scorer = StreamingScorer(broken, batch_size=2, max_wait=60, backoff=60, log=logs.append)
        scorer.start()
        for number in range(6):
            scorer.submit(f"j{number}")                                       # never raises into the caller
        self.assertTrue(_wait(lambda: calls))
        stats = scorer.finish(5)
        self.assertEqual(len(calls), 1)                                       # backing off: it did not hammer the failing AI
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["left_over"], 4)                               # these stay pending for the final pass
        self.assertTrue(any("评分出错" in line for line in logs))

    def test_a_batch_that_was_not_fully_scored_makes_it_wait_before_trying_again(self):
        calls = []
        scorer = StreamingScorer(lambda ids: calls.append(list(ids)) or 1, batch_size=2, max_wait=60, backoff=60)   # one left: cap hit
        scorer.start()
        for number in range(6):
            scorer.submit(f"j{number}")
        self.assertTrue(_wait(lambda: calls))
        stats = scorer.finish(5)
        self.assertEqual((len(calls), stats["deferred_batches"]), (1, 1))

    def test_stopping_ends_the_worker_without_more_scoring(self):
        stop = threading.Event()
        calls = []
        scorer = StreamingScorer(lambda ids: calls.append(list(ids)) or 0, stop_event=stop, batch_size=2, max_wait=60)
        scorer.start()
        scorer.submit("a")
        scorer.submit("b")
        self.assertTrue(_wait(lambda: calls))
        stop.set()
        scorer.submit("c")
        scorer.submit("d")
        stats = scorer.finish(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(stats["left_over"], 2)


class StreamingWithCollectionTests(TestCase):
    """The real orchestrator and a real database: collecting and scoring write at the same time."""

    def _run(self, total=60, stream=True, fail_scoring=False):
        scored_batches: list[list[str]] = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "data" / "c.db"

        class Collector:
            platform = "liepin"

            def collect(self, request, hooks):
                for index in range(total):
                    candidate = JobCandidate(platform="liepin", source_job_id=f"j{index}", title=f"Agent工程师{index}", company=f"公司{index}号",
                                             city="上海", jd="负责Agent开发", url=f"https://x.test/{index}", source_keyword=request.keywords[0])
                    if not hooks.on_list_candidate(candidate) or not hooks.on_candidate(candidate):
                        break
                    if index % 5 == 0:
                        time.sleep(0.02)                                         # the collection is slower than the scoring
                return PlatformCollectionResult("liepin", "completed", "search_exhausted", "ok")

        def fake_score_jobs(config, *, scope, job_ids, limit, force_rescore, **_):
            if fail_scoring:
                raise RuntimeError("AI is down")
            scored_batches.append(list(job_ids))
            db = get_db(db_path)                                                 # its own connection, like the real scorer
            try:
                for job_id in job_ids:
                    db.execute("UPDATE jobs SET status = 'ready', score = 80 WHERE id = ? AND status = 'pending'", (job_id,))
                    db.commit()
            finally:
                db.close()
            return len(job_ids), 0

        options = _options(order=["liepin"])
        options["auto_score"] = True
        options["platforms"]["liepin"].update(cities=["上海"], keywords=["Agent"], max_pages=1)
        config = {"automation": {"stream_scoring": stream}}
        with patch("bosshunter.ai.scorer.score_jobs", fake_score_jobs):
            CollectionOrchestrator(config, db_path=db_path, registry=CollectorRegistry({"liepin": Collector})).run(options)
        return db_path, scored_batches

    def _state(self, db_path):
        db = get_db(db_path)
        try:
            statuses = [row["status"] for row in db.execute("SELECT status FROM jobs").fetchall()]
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            duplicates = db.execute("SELECT COUNT(*) - COUNT(DISTINCT id) FROM jobs").fetchone()[0]
        finally:
            db.close()
        return statuses, integrity, duplicates

    def test_postings_are_scored_while_collecting_and_the_database_stays_consistent(self):
        db_path, batches = self._run(total=60, stream=True)
        statuses, integrity, duplicates = self._state(db_path)
        self.assertEqual(len(statuses), 60)
        self.assertEqual(set(statuses), {"ready"})                               # every posting was scored exactly once
        self.assertEqual((integrity, duplicates), ("ok", 0))
        scored = [job for batch in batches for job in batch]
        self.assertEqual(len(scored), len(set(scored)))                          # no posting went to the scorer twice
        self.assertGreater(len(batches), 1)                                      # scoring went on in several small batches

    def test_without_streaming_scoring_runs_once_after_collecting(self):
        db_path, batches = self._run(total=20, stream=False)
        statuses, integrity, _ = self._state(db_path)
        self.assertEqual((len(batches), len(batches[0]), set(statuses), integrity), (1, 20, {"ready"}, "ok"))

    def test_when_streaming_scoring_fails_the_final_pass_still_scores_everything(self):
        calls = {"n": 0}
        db_path, batches = self._run(total=20, stream=True, fail_scoring=True)
        statuses, integrity, _ = self._state(db_path)
        # every score call failed (streaming and the final pass): the postings stay pending, none is lost or half written
        self.assertEqual((set(statuses), integrity, len(statuses)), ({"pending"}, "ok", 20))
        self.assertEqual(batches, [])
        del calls
