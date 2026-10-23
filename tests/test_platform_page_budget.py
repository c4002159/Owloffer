import tempfile
from pathlib import Path
from threading import Event

from bosshunter.collection.models import PlatformCollectionResult
from bosshunter.collection.orchestrator import CollectionOrchestrator
from bosshunter.collection.registry import CollectorRegistry
from bosshunter.db import get_db
from bosshunter.platform_safety import PlatformStop, platform_search_page_limit
from bosshunter.db import count_platform_access_today


class _Pager:
    """Opens list pages one after another the way the real collectors do: announces each page, stops when told to."""

    def __init__(self, platform, events, pages):
        self.platform = platform
        self.events = events
        self.pages = pages

    def collect(self, _request, hooks):
        self.events.append(f"start:{self.platform}")
        for page in range(1, self.pages + 1):
            if hooks.stop_event is not None and hooks.stop_event.is_set():
                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "已停止")
            hooks.on_event(phase="loading_list", city="上海", keyword="AI", page=page)
            self.events.append(f"page:{self.platform}:{page}")
        return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "无更多结果")


def _options(platform):
    return {
        "platform_order": [platform], "auto_score": False,
        "platforms": {platform: {"keywords": ["AI"], "cities": ["上海"], "city_codes": {"上海": "1"}, "max_pages": 30, "sort": "default"}},
    }


def _config(limit):
    return {"collection": {"platform_limits": {"liepin": {"daily_search_page_limit": limit}}}}


def _run(config, db_path, events, pages=10, platform="liepin"):
    registry = CollectorRegistry({platform: lambda: _Pager(platform, events, pages)})
    return CollectionOrchestrator(config, db_path=db_path, registry=registry).run(_options(platform))


def test_a_platform_stops_when_its_daily_pages_are_used_up_and_other_work_is_kept():
    with tempfile.TemporaryDirectory() as tmp:
        events = []
        db_path = Path(tmp) / "c.db"
        result = _run(_config(3), db_path, events)
        state = result["platforms"]["liepin"]
        assert state["reason_code"] == "daily_search_page_limit" and state["status"] == "completed_with_shortage"
        assert len([e for e in events if e.startswith("page:")]) <= 4            # at most the page that tripped the budget
        db = get_db(db_path)
        try:
            assert count_platform_access_today(db, platform="liepin", stage="collection", action="search_page") == 3
        finally:
            db.close()


def test_the_next_run_on_the_same_day_does_not_even_start_the_platform():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "c.db"
        _run(_config(3), db_path, [])
        events = []
        result = _run(_config(3), db_path, events)
        assert events == []                                                      # no browser work at all
        assert result["platforms"]["liepin"]["reason_code"] == "daily_search_page_limit"


def test_a_page_counts_once_however_many_events_it_sends():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "c.db"

        class Chatty(_Pager):
            def collect(self, _request, hooks):
                for page in (1, 2):
                    for phase in ("loading_list", "pacing", "loading_detail", "saving"):
                        hooks.on_event(phase=phase, city="上海", keyword="AI", page=page)
                return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "完成")

        registry = CollectorRegistry({"liepin": lambda: Chatty("liepin", [], 2)})
        CollectionOrchestrator(_config(50), db_path=db_path, registry=registry).run(_options("liepin"))
        db = get_db(db_path)
        try:
            assert count_platform_access_today(db, platform="liepin", stage="collection", action="search_page") == 2
        finally:
            db.close()


def test_zero_means_no_budget_and_boss_keeps_its_own_setting():
    assert platform_search_page_limit(_config(0), "liepin") is None
    assert platform_search_page_limit({}, "liepin") == 300 and platform_search_page_limit({}, "zhilian_campus") == 200
    assert platform_search_page_limit(_config(7), "liepin") == 7
    assert platform_search_page_limit({"collection": {"platform_limits": {"liepin": {"daily_search_page_limit": "x"}}}}, "liepin") == 300
    assert platform_search_page_limit({}, "boss") is None
    with tempfile.TemporaryDirectory() as tmp:
        events = []
        _run(_config(0), Path(tmp) / "c.db", events, pages=12)
        assert len([e for e in events if e.startswith("page:")]) == 12


def test_a_whole_task_stop_is_not_mistaken_for_the_daily_limit():
    with tempfile.TemporaryDirectory() as tmp:
        stop = Event()
        stop.set()
        config = {**_config(3), "_workbench_stop_event": stop}
        result = _run(config, Path(tmp) / "c.db", [])
        assert result["status"] == "stopped"


def test_platform_stop_follows_the_whole_task_stop_and_can_be_set_alone():
    whole = Event()
    own = PlatformStop(whole)
    assert not own.is_set()
    own.set()
    assert own.is_set() and not whole.is_set()
    other = PlatformStop(whole)
    whole.set()
    assert other.is_set() and other.wait(0.05) is True
