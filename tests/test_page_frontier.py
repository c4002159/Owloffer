import tempfile
from pathlib import Path
from unittest import TestCase

from bosshunter import scan_frontier as sf
from bosshunter.collection.models import JobCandidate, PlatformCollectionResult
from bosshunter.collection.orchestrator import CollectionOrchestrator
from bosshunter.collection.registry import CollectorRegistry
from bosshunter.db import get_db

from tests.test_collection_orchestrator import _options


class FrontierMathTests(TestCase):
    def test_the_refresh_rate_is_the_share_of_postings_not_yet_in_the_database(self):
        self.assertEqual(sf.refresh_rate(0, 0), None)
        self.assertEqual(sf.refresh_rate(20, 15), 0.25)

    def test_the_head_grows_while_it_is_fresh_and_shrinks_when_it_is_known(self):
        self.assertEqual(sf.next_head_pages(3, 0.6), 5)
        self.assertEqual(sf.next_head_pages(10, 0.9), 10)
        self.assertEqual(sf.next_head_pages(3, 0.05), 2)
        self.assertEqual(sf.next_head_pages(1, 0.0), 1)
        self.assertEqual(sf.next_head_pages(3, 0.2), 3)
        self.assertEqual(sf.next_head_pages(3, None), 3)

    def test_deep_reading_starts_after_the_head_or_at_the_remembered_page_depending_on_how_fresh_it_is(self):
        self.assertEqual(sf.deep_start(0, 3, None), 4)
        self.assertEqual(sf.deep_start(23, 3, 0.0), 24)            # mostly known: after it
        self.assertEqual(sf.deep_start(23, 3, 0.8), 23)            # mostly new: the list moved, from that page
        self.assertEqual(sf.deep_start(2, 3, None), 4)


class ScanRunTests(TestCase):
    """Several days of one keyword against a list whose pages hold 4 postings each."""

    def setUp(self):
        self.asked: list[tuple[int, int]] = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "data" / "c.db"
        self.moved_pages: set[int] = set()             # pages whose postings changed since the last day
        self.last_page = 200
        outer = self

        class Site:
            platform = "liepin"

            def collect(self, request, hooks):
                outer.asked.append((request.first_page, request.max_pages))
                for page in range(request.first_page, min(request.max_pages, outer.last_page) + 1):
                    for index in range(4):
                        stamp = f"d{outer.day}" if page in outer.moved_pages else "d0"
                        candidate = JobCandidate(platform="liepin", source_job_id=f"{request.keywords[0]}-{page}-{index}-{stamp}",
                                                 title=f"Agent工程师{page}", company=f"公司{page}号{index}{stamp}", city="上海",
                                                 jd="负责Agent开发", url=f"https://x.test/{page}/{index}/{stamp}", source_keyword=request.keywords[0])
                        if not hooks.on_list_candidate(candidate):
                            continue
                        hooks.on_candidate(candidate)
                return PlatformCollectionResult("liepin", "completed", "search_exhausted", "ok")

        self.registry = CollectorRegistry({"liepin": Site})
        self.day = 1

    def _day(self, number):
        self.day = number
        self.asked.clear()
        db = get_db(self.db_path)
        try:                                           # a new day: yesterday's "done today" marks and page checkpoints are gone
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
                if "combo" in name or "page_progress" in name or name == "keyword_progress":
                    db.execute(f"DELETE FROM {name}")
            db.commit()
        finally:
            db.close()
        options = _options(order=["liepin"])
        options["platforms"]["liepin"].update(cities=["上海"], keywords=["Agent"], max_pages=20)
        config = {"automation": {"keyword_budget": True, "page_frontier": True}}
        CollectionOrchestrator(config, db_path=self.db_path, registry=self.registry).run(options)
        return list(self.asked)

    def _state(self):
        db = get_db(self.db_path)
        try:
            return sf.get(db, "liepin", "Agent", "上海")
        finally:
            db.close()

    def test_the_first_day_reads_the_head_and_a_window_after_it(self):
        self.assertEqual(self._day(1), [(1, 3), (4, 23)])
        state = self._state()
        self.assertEqual(state["frontier"], 23)
        self.assertEqual(state["head_pages"], 5)           # everything was new: the head is worth more pages

    def test_a_known_remembered_page_makes_the_next_day_read_on_after_it_and_the_head_shrinks_when_known(self):
        self._day(1)
        asked = self._day(2)
        self.assertEqual(asked[0], (1, 5))                 # the head, as long as day 1 left it
        self.assertEqual(asked[1], (23, 23))               # probe of the remembered page: all known
        self.assertEqual(asked[2], (24, 43))               # so on after it
        self.assertEqual(self._state()["frontier"], 43)
        self.assertEqual(self._state()["head_pages"], 4)   # the head was all known

    def test_a_remembered_page_that_is_new_again_restarts_the_deep_window_there(self):
        self._day(1)
        self.moved_pages = {23}
        asked = self._day(2)
        self.assertEqual(asked[1], (23, 23))
        self.assertEqual(asked[2][0], 23)                  # mostly new: read from that very page
        self.assertEqual(self._state()["frontier"], 42)

    def test_the_end_of_the_list_wraps_the_memory_around(self):
        self.last_page = 30
        self._day(1)
        self.assertEqual(self._state()["frontier"], 23)
        self._day(2)                                       # 24–43 reaches the end (30), frontier still advances
        self.assertEqual(self._state()["frontier"], 43)
        asked = self._day(3)                               # probe of page 43: the list ends before it
        self.assertEqual(asked[1], (43, 43))
        self.assertLessEqual(asked[2][0], 10)             # deep reading starts afresh right after the head
