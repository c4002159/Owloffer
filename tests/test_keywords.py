import json
import sqlite3

from bosshunter.ai import keywords as kw


RAW = json.dumps({
    "directions": [{"name": "Agent", "evidence": "open_deep_research", "keywords": ["Agent工程师"]}],
    "core": ["Agent工程师", "RAG 工程师", "agent工程师"],
    "expand": ["实施工程师", "Agent工程师"],
    "explore": ["AI产品经理"] + [f"探索词{i}" for i in range(10)],
    "excludes": ["电销", " "],
}, ensure_ascii=False)


def test_parse_plan_dedupes_across_tiers_and_caps_explore():
    plan = kw.parse_plan(f"好的，方案如下：\n```json\n{RAW}\n```")
    assert plan["core"] == ["Agent工程师", "RAG 工程师"]
    assert plan["expand"] == ["实施工程师"]
    assert len(plan["explore"]) == kw.TIER_LIMITS["explore"]
    assert plan["excludes"] == ["电销"]
    assert plan["directions"][0]["name"] == "Agent"


def test_parse_plan_rejects_garbage():
    assert kw.parse_plan("") is None
    assert kw.parse_plan("not json") is None
    assert kw.parse_plan('{"core": [], "expand": [], "explore": []}') is None


def test_select_keywords_caps_explore():
    plan = kw.parse_plan(RAW)
    chosen = kw.select_keywords(plan, ("core", "explore"), explore_limit=2)
    assert chosen[:2] == plan["core"][:2]
    assert len(chosen) == len(plan["core"]) + 2


def test_keyword_model_overrides_model_only_in_copy():
    config = {"ai": {"model": "cheap", "keyword_model": "strong"}}
    cfg = kw._keyword_config(config)
    assert cfg["ai"]["model"] == "strong" and config["ai"]["model"] == "cheap"


def test_skill_file_is_loadable_and_mentions_tiers():
    skill = kw.load_skill({})
    assert "core" in skill and "explore" in skill and "FDE" in skill


def test_keyword_yield_counts_qualified_jobs():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE jobs (source_keyword TEXT, score INTEGER, deleted_at TEXT)")
    conn.executemany(
        "INSERT INTO jobs VALUES (?, ?, NULL)",
        [("Agent", 80), ("Agent", 40), ("IT管理", 30)],
    )
    rows = kw.keyword_yield(conn, 60)
    assert rows[0] == {"keyword": "Agent", "collected": 2, "qualified": 1, "rate": 0.5}
    assert rows[1]["qualified"] == 0
