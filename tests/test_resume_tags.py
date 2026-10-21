import sqlite3

import pytest

from bosshunter import resume_tags as rt


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


FINETUNE = "大模型微调：LoRA、SFT 微调与模型评测，评测体系；RAG 检索增强、向量检索；Agent 编排，Python，推理部署" * 2
PRODUCT = "产品经理，需求分析、PRD、用户调研、产品设计、竞品分析，数据分析，Agent 产品落地" * 2
RESUMES = [
    {"name": "ai应用工程师（微调）", "file_id": "a.pdf", "uploaded": "2026-10-18 19:47", "text": FINETUNE},
    {"name": "ai产品经理", "file_id": "b.pdf", "uploaded": "2026-10-18 19:47", "text": PRODUCT},
    {"name": "agent应用工程师", "file_id": "c.pdf", "uploaded": "2026-10-18 19:37", "text": ""},
]
NAMES = [item["name"] for item in RESUMES]


def _scan(items, asked=None):
    """A fake platform: returns the items with text only for the resumes ``need_text`` accepts."""
    def scan(need_text):
        out = []
        for item in items:
            wanted = need_text(item)
            if asked is not None and wanted:
                asked.append(item["name"])
            out.append({**item, "text": item["text"] if wanted else ""})
        return out
    return scan


def test_contacts_are_removed_before_a_resume_is_tagged_or_remembered(db):
    cleaned = rt.redact("冯某 a.b@163.com | 15300000000 教育背景")
    assert "a.b@163.com" not in cleaned and "15300000000" not in cleaned and "教育背景" in cleaned and "冯某" in cleaned
    rt.store_resume(db, {}, "liepin", {"name": "x", "file_id": "1", "uploaded": "t", "text": "联系 a@b.com 15300000000 Java Spring"})
    assert "@" not in db.execute("SELECT text FROM platform_resumes").fetchone()[0]


def test_tags_come_from_the_text_and_a_job_is_tagged_with_its_title_weighted_up():
    tags = rt.resume_tags("x", "LoRA 微调 微调 微调，RAG")
    assert tags["微调"] > tags["RAG"] > 0
    job = rt.job_tags({"title": "产品经理", "jd": "负责需求分析和 Agent 产品"})
    assert job["产品"] > job.get("Agent", 0) > 0 and rt.job_tags({"title": "", "jd": ""}) == {}


def test_first_sync_reads_every_preview_and_a_second_sync_reads_only_what_is_new(db):
    asked = []
    report = rt.sync_resumes(db, {}, scan=_scan(RESUMES, asked))
    assert report["detected"] and sorted(asked) == sorted(NAMES)
    by_name = {r["name"]: r for r in report["resumes"]}
    assert by_name["ai应用工程师（微调）"]["source"] == "file" and "微调" in by_name["ai应用工程师（微调）"]["tags"]
    assert by_name["agent应用工程师"]["source"] == "name"                  # its preview gave no text yet
    asked.clear()
    readable = [*RESUMES[:2], {**RESUMES[2], "text": "Agent 智能体 LangGraph MCP 工具调用"}]
    again = rt.sync_resumes(db, {}, scan=_scan(readable, asked))
    assert asked == ["agent应用工程师"] and again["changed"] == ["agent应用工程师"]    # only the unread one is read now


def test_a_replaced_resume_is_read_again_and_a_removed_one_is_forgotten(db):
    rt.sync_resumes(db, {}, scan=_scan(RESUMES))
    asked = []
    replaced = [{**RESUMES[0], "uploaded": "2026-10-25 09:00", "text": "Java Spring MySQL Redis 后端 微服务"}, RESUMES[1]]
    report = rt.sync_resumes(db, {}, scan=_scan(replaced, asked))
    assert asked == ["ai应用工程师（微调）"] and report["removed"] == ["agent应用工程师"]
    assert "后端开发" in {r["name"]: r for r in report["resumes"]}["ai应用工程师（微调）"]["tags"]
    assert [r["name"] for r in report["resumes"]] == ["ai产品经理", "ai应用工程师（微调）"]


def test_when_the_platform_cannot_be_read_the_memory_is_kept(db):
    rt.sync_resumes(db, {}, scan=_scan(RESUMES))
    down = rt.sync_resumes(db, {}, scan=lambda need: None)
    assert not down["detected"] and len(down["resumes"]) == 3 and down["removed"] == []
    assert not rt.sync_resumes(db, {}, scan=None)["detected"]


def test_the_resume_list_is_rechecked_only_when_it_is_stale(db):
    assert rt.is_stale(db)                                            # never checked
    rt.sync_resumes(db, {}, scan=_scan(RESUMES))
    assert not rt.is_stale(db, hours=12)
    calls = []
    rt.sync_resumes(db, {}, scan=lambda need: calls.append(1) or [], force=False)
    assert calls == []                                                # fresh: no browser visit
    db.execute("UPDATE platform_resumes SET checked_at = datetime('now', '-30 hours')")
    assert rt.is_stale(db, hours=12)


def test_an_older_name_only_table_is_replaced(db):
    db.execute("CREATE TABLE platform_resumes (platform TEXT NOT NULL, name TEXT NOT NULL, seen_at TIMESTAMP, PRIMARY KEY (platform, name))")
    assert rt.sync_resumes(db, {}, scan=_scan(RESUMES))["detected"]


def test_the_best_matching_remembered_resume_wins_by_content(db):
    rt.sync_resumes(db, {}, scan=_scan(RESUMES))
    finetune_job = {"title": "大模型微调工程师", "jd": "负责 LoRA 微调、模型评测与推理部署"}
    product_job = {"title": "AI产品经理", "jd": "负责需求分析、PRD、用户调研和产品设计"}
    assert rt.pick_resume(db, {}, finetune_job, NAMES)[0] == "ai应用工程师（微调）"
    name, note = rt.pick_resume(db, {}, product_job, NAMES)
    assert name == "ai产品经理" and "共同标签" in note
    assert rt.pick_resume(db, {}, {"title": "会计", "jd": "账务处理"}, NAMES)[0] is None
    assert rt.pick_resume(db, {}, product_job, [])[0] is None


def test_a_near_tie_goes_to_the_preselected_resume_but_a_clear_win_does_not(db, monkeypatch):
    def fake(scores):
        return lambda *a, **k: [{"name": n, "score": s, "source": "ai", "shared": ["Agent"]} for n, s in scores]

    monkeypatch.setattr(rt, "rank_resumes", fake([("ai产品经理", 0.50), ("ai应用工程师（微调）", 0.45)]))
    assert rt.pick_resume(db, {}, {}, NAMES, default_name="ai应用工程师（微调）")[0] == "ai应用工程师（微调）"
    monkeypatch.setattr(rt, "rank_resumes", fake([("ai产品经理", 0.60), ("ai应用工程师（微调）", 0.45)]))
    assert rt.pick_resume(db, {}, {}, NAMES, default_name="ai应用工程师（微调）")[0] == "ai产品经理"
    assert rt.pick_resume(db, {}, {}, NAMES, min_score=0.99)[0] is None


# ---- the model ----------------------------------------------------------------------------------------------
def test_model_answer_is_parsed_into_weighted_tags_and_junk_is_rejected():
    answer = 'ok {"tags": [{"tag": "大模型微调", "weight": 5}, {"tag": "RAG", "weight": 9}, {"tag": "x", "weight": 3}]}'
    assert rt.parse_tags(answer) == {"大模型微调": 5.0, "RAG": 5.0}
    assert rt.parse_tags('{"tags": ["Agent", "RAG"]}') == {"Agent": 3.0, "RAG": 3.0}
    assert rt.parse_tags("nothing") is None and rt.parse_tags('{"tags": "x"}') is None and rt.parse_tags(None) is None


def test_with_a_model_resumes_are_tagged_by_it_and_the_job_in_the_same_tag_space(db, monkeypatch):
    purposes, prompts = [], []

    def fake_model(config, prompt, purpose):
        purposes.append(purpose)
        prompts.append(prompt)
        if purpose == "resume_tags":
            return '{"tags": [{"tag": "大模型微调", "weight": 5}]}' if "LoRA" in prompt else '{"tags": [{"tag": "产品需求分析", "weight": 5}]}'
        return '{"tags": [{"tag": "大模型微调", "weight": 5}]}'

    monkeypatch.setattr(rt, "ai_enabled", lambda config: True)
    monkeypatch.setattr(rt, "_ask_model", fake_model)
    report = rt.sync_resumes(db, {}, scan=_scan(RESUMES))
    by_name = {r["name"]: r for r in report["resumes"]}
    assert by_name["ai应用工程师（微调）"]["source"] == "ai" and by_name["ai应用工程师（微调）"]["tags"] == ["大模型微调"]
    assert by_name["agent应用工程师"]["source"] == "name"                      # no preview text, no model call for it
    assert purposes.count("resume_tags") == 2
    name, note = rt.pick_resume(db, {}, {"id": "j1", "title": "微调工程师", "jd": "负责模型微调"}, NAMES)
    assert name == "ai应用工程师（微调）" and "大模型微调" in note and "job_tags" in purposes
    assert all("@" not in prompt for prompt in prompts)


def test_a_failing_model_falls_back_to_the_vocabulary_and_is_upgraded_later(db, monkeypatch):
    monkeypatch.setattr(rt, "ai_enabled", lambda config: True)
    monkeypatch.setattr(rt, "_ask_model", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    report = rt.sync_resumes(db, {}, scan=_scan(RESUMES))
    first = {r["name"]: r for r in report["resumes"]}["ai应用工程师（微调）"]
    assert first["source"] == "file" and "微调" in first["tags"]
    monkeypatch.setattr(rt, "_ask_model", lambda c, p, purpose: '{"tags": [{"tag": "大模型微调", "weight": 5}]}')
    assert rt.tags_for_names(db, {}, ["ai应用工程师（微调）"])["ai应用工程师（微调）"]["source"] == "ai"
