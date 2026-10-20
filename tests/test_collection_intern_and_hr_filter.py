import tempfile
from dataclasses import replace
from pathlib import Path

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionResult
from bosshunter.collection.orchestrator import CollectionOrchestrator
from bosshunter.collection.registry import CollectorRegistry
from bosshunter.db import get_db


def _candidate(source_id, title="Agent工程师", hr_active="在线"):
    return JobCandidate(
        platform="boss", source_job_id=source_id, title=title, company=f"公司{source_id}",
        city="上海", city_code="101020100", jd="负责岗位相关工作", url=f"/job_detail/{source_id}.html",
        hr_active=hr_active,
    )


class _RecordingCollector:
    """Returns different candidates for the full-time and the internship request."""

    def __init__(self, requests, fulltime, interns):
        self.requests = requests
        self.fulltime = fulltime
        self.interns = interns

    def collect(self, request, hooks: CollectorHooks):
        self.requests.append((list(request.keywords), dict(request.filters)))
        candidates = self.interns if request.filters.get("job_type") == ["实习"] else self.fulltime
        for candidate in candidates:
            if not hooks.on_list_candidate(candidate):
                continue
            if not hooks.on_candidate(candidate):
                return PlatformCollectionResult("boss", "completed", "callback_stopped", "达到目标")
        return PlatformCollectionResult("boss", "completed", "search_exhausted", "无更多结果")


def _options(intern_keywords, quotas):
    return {
        "platform_order": ["boss"],
        "auto_score": False,
        "platforms": {"boss": {
            "keywords": ["Agent工程师"], "cities": ["上海"], "city_codes": {"上海": "101020100"},
            "max_pages": 1, "sort": "default", "intern_keywords": intern_keywords,
        }},
        "quotas": quotas,
    }


def _run(config, options, fulltime, interns):
    requests = []
    registry = CollectorRegistry({"boss": lambda: _RecordingCollector(requests, fulltime, interns)})
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "jobs.db"
        result = CollectionOrchestrator(config, db_path=db_path, registry=registry).run(options)
        conn = get_db(db_path)
        titles = [row["title"] for row in conn.execute("SELECT title FROM jobs ORDER BY title")]
        conn.close()
    return result, requests, titles


def test_internship_pass_runs_with_intern_keywords_and_filter():
    fulltime = [_candidate("f1"), _candidate("f2")]
    interns = [_candidate("i1", title="AI开发实习生"), _candidate("i2", title="产品实习生")]
    options = _options(["AI开发实习生", "产品实习生"], {"fulltime": 120, "internship": 30})
    result, requests, titles = _run({"profile": {}}, options, fulltime, interns)
    assert requests == [
        (["Agent工程师"], {}),
        (["AI开发实习生", "产品实习生"], {"job_type": ["实习"]}),
    ]
    assert sorted(titles) == sorted(["Agent工程师", "Agent工程师", "AI开发实习生", "产品实习生"])
    assert len(result["collected_job_ids"]) == 4


def test_internship_pass_stops_near_twice_the_quota():
    interns = [_candidate(f"i{n}", title="AI开发实习生") for n in range(10)]
    options = _options(["AI开发实习生"], {"fulltime": 10, "internship": 2})
    result, _requests, titles = _run({"profile": {}}, options, [], interns)
    assert titles.count("AI开发实习生") == 4  # quota 2 -> collect up to 4


def test_no_internship_pass_without_quota_or_keywords():
    interns = [_candidate("i1", title="AI开发实习生")]
    for options in (
        _options(["AI开发实习生"], {"fulltime": 150, "internship": 0}),
        _options([], {"fulltime": 120, "internship": 30}),
    ):
        _result, requests, titles = _run({"profile": {}}, options, [_candidate("f1")], interns)
        assert len(requests) == 1 and titles == ["Agent工程师"]


def test_inactive_hr_never_enters_the_pool():
    config = {"profile": {"hr_active_min_level": 2}}
    fulltime = [
        _candidate("ok", hr_active="3日内活跃"),
        _candidate("stale", hr_active="2月内活跃"),
        _candidate("old", hr_active="半年前活跃"),
        _candidate("unknown", hr_active=""),
    ]
    options = _options([], {"fulltime": 150, "internship": 0})
    result, _requests, titles = _run(config, options, fulltime, [])
    assert len(result["collected_job_ids"]) == 2  # active and unknown-activity jobs only
    assert result["platforms"]["boss"]["filtered"] == 2
