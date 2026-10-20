from bosshunter.resumes import extra_resumes, read_resume_for_job, resume_path_for_job

PLAN = {"directions": [
    {"name": "Agent/AI应用", "keywords": ["Agent工程师", "RAG工程师"]},
    {"name": "Java后端", "keywords": ["Java后端"]},
]}


def _config(tmp_path):
    default = tmp_path / "default.md"
    agent = tmp_path / "agent.md"
    java = tmp_path / "java.md"
    default.write_text("默认简历", encoding="utf-8")
    agent.write_text("Agent简历", encoding="utf-8")
    java.write_text("Java简历", encoding="utf-8")
    return {"profile": {
        "resume_path": str(default),
        "resumes": [
            {"path": str(agent), "direction": "Agent/AI应用"},
            {"path": str(java), "direction": "java"},
            {"path": str(tmp_path / "missing.md"), "direction": "产品"},
            {"path": "", "direction": "空"},
            "garbage",
        ],
    }}


def test_extra_resumes_drops_invalid_entries(tmp_path):
    assert [e["direction"] for e in extra_resumes(_config(tmp_path))] == ["Agent/AI应用", "java", "产品"]
    assert extra_resumes({}) == []


def test_job_gets_resume_for_its_keyword_direction(tmp_path):
    config = _config(tmp_path)
    job = {"title": "大模型工程师", "source_keyword": "rag工程师"}
    assert read_resume_for_job(config, job, PLAN) == "Agent简历"


def test_job_category_wins_over_keyword_lookup(tmp_path):
    config = _config(tmp_path)
    job = {"title": "x", "category": "agent/ai应用", "source_keyword": "完全不相关"}
    assert read_resume_for_job(config, job, PLAN) == "Agent简历"


def test_title_containing_direction_word_matches(tmp_path):
    config = _config(tmp_path)
    job = {"title": "资深 Java 开发", "source_keyword": ""}
    assert read_resume_for_job(config, job, None) == "Java简历"


def test_falls_back_to_default_when_nothing_matches_or_file_missing(tmp_path):
    config = _config(tmp_path)
    assert read_resume_for_job(config, {"title": "运营", "source_keyword": "运营"}, PLAN) == "默认简历"
    assert read_resume_for_job(config, {"title": "产品经理", "source_keyword": ""}, None) == "默认简历"
    assert read_resume_for_job(config, None) == "默认简历"
    assert resume_path_for_job({}, None) == "./resume.md"
