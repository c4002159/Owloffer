import json
from datetime import date, datetime, timedelta

from bosshunter import keyword_advisor as adv
from bosshunter import keyword_strategy as ks
from bosshunter import term_stats as ts
from bosshunter.ai import keyword_expansion as kx
from bosshunter.db import get_db


ANSWER = json.dumps({"directions": [
    {"name": "AIGC 应用", "reason": "简历里有 Agent 与 RAG 项目，AIGC 是同一类工作的另一种叫法", "keywords": ["AIGC应用工程师", "AIGC", "Agent开发", "算法工程师"]},
    {"name": "提示词与评测", "reason": "有模型评测经历", "keywords": ["提示词工程师"]},
]}, ensure_ascii=False)


def test_the_models_answer_becomes_clean_suggestions_without_what_is_already_searched():
    suggestions = kx.parse("好的：\n" + ANSWER, ["Agent开发工程师", "agent 开发"])
    words = [item["keyword"] for item in suggestions]
    assert "AIGC应用开发工程师" in words and "AIGC应用工程师" not in words              # "应用工程师" is rewritten like in the plan
    assert "算法工程师" not in words                                           # vague on its own: dropped by the same check as the plan
    assert "Agent开发" not in words                                            # "agent 开发" is searched already (spaces and case are ignored)
    assert all(item["source"] == "model" and item["reason"] for item in suggestions)
    assert kx.parse("not json", []) == [] and kx.parse('{"directions": [1, "x"]}', []) == []
    again = kx.parse(ANSWER, ["AIGC", "Agent开发"])                            # words already searched are not proposed again (spaces and case ignored)
    assert "AIGC" not in [item["keyword"] for item in again]


def test_suggestions_expire_after_a_week_and_accepted_ones_are_forgotten(tmp_path):
    (tmp_path / "data").mkdir()
    store = kx.store_path(tmp_path)
    store.write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                                 "suggestions": [{"keyword": "AIGC", "direction": "x", "reason": "y"}, {"keyword": "提示词工程师", "direction": "z", "reason": "w"}]}), encoding="utf-8")
    assert [item["keyword"] for item in kx.load(tmp_path)] == ["AIGC", "提示词工程师"]
    kx.forget(tmp_path, ["aigc"])
    assert [item["keyword"] for item in kx.load(tmp_path)] == ["提示词工程师"]
    assert kx.load(tmp_path, datetime.now() + timedelta(days=8)) == []
    assert kx.load(tmp_path / "nowhere") == []


def test_the_prompt_carries_the_resume_the_keywords_and_the_market_numbers_and_the_vague_word_rules():
    mined = {"lift": [{"term": "人工智能", "scored": 12, "passed": 9, "rate": 0.75, "lift": 2.3}], "frequent": [{"term": "多模态", "seen": 31}]}
    prompt = kx.build_prompt("简历正文", [{"keyword": "Agent工程师", "tier": "core", "n": 40, "pass_rate": 0.5}], mined, {"profile": {"target_cities": ["上海"]}})
    for text in ("简历正文", "Agent工程师", "人工智能", "9 个", "多模态", "不要生成含糊的关键词", "严格 JSON"):
        assert text in prompt


def test_the_resume_text_gathers_the_resumes_of_the_library(tmp_path):
    folder = tmp_path / "data" / "resumes"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text("做过 Agent 项目", encoding="utf-8")
    (folder / "b.md").write_text("做过 Java 后端", encoding="utf-8")
    text = kx.resume_text(tmp_path, {"profile": {"resume_path": str(folder / "a.md")}})
    assert "Agent 项目" in text and "Java 后端" in text and text.count("做过 Agent 项目") == 1        # the main one is not repeated


def _fill(db, title, passed, failed, day):
    for number in range(passed + failed):
        ts.note_seen(db, title, day)
        ts.note_outcome(db, f"{title}-{number}", title, "", number < passed, day)


def test_the_advice_offers_mined_words_and_the_models_words_and_an_accepted_word_is_tried_in_the_explore_tier(tmp_path):
    (tmp_path / "data").mkdir()
    db = get_db(tmp_path / "t.db")
    try:
        ks.ensure_tables(db)
        day = date.today()
        _fill(db, "人工智能工程师", 12, 3, day)
        _fill(db, "销售运营专员", 0, 40, day)
        kx.store_path(tmp_path).write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                                                       "suggestions": [{"keyword": "AIGC应用开发工程师", "direction": "AIGC 应用", "reason": "同类工作的另一种叫法"}]}), encoding="utf-8")
        config = {"search": {"keywords": ["Agent开发"]}}
        advice = adv.advise(db, config, None, base_dir=tmp_path)
        by_word = {item["keyword"]: item for item in advice["suggestions"] if item["kind"] == "add"}
        assert "人工智能" in by_word and by_word["人工智能"]["reason"].startswith("挖词：")
        assert "AIGC应用开发工程师" in by_word and by_word["AIGC应用开发工程师"]["reason"].startswith("模型建议（AIGC 应用）")
        assert any(reason["code"] == "mined_terms" for reason in advice["reasons"]) and advice["level3"] is True
        assert advice["mined"]["lift"][0]["term"] == "人工智能"
        done = adv.apply(db, config, tmp_path / "advice.json", [{"kind": "add", "keyword": "人工智能"}])
        assert "人工智能" in done["config"]["search"]["keywords"]
        assert ks.current_tiers(db, ["Agent开发", "人工智能"], None)["explore"] == ["人工智能"]        # a new word starts with a small share
    finally:
        db.close()


def test_a_short_english_word_is_shown_as_an_acronym():
    assert adv._display("aigc") == "AIGC" and adv._display("mcp") == "MCP" and adv._display("langchain") == "langchain" and adv._display("人工智能") == "人工智能"
