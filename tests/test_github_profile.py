import json

from bosshunter.github_profile import load_profile, save_profile, summarize_profile, tech_stack

PROFILE = {
    "username": "demo",
    "synced_at": "2026-10-20T00:00:00+00:00",
    "repos": [
        {
            "name": "short-link",
            "description": "短链服务",
            "topics": ["backend"],
            "primary_language": "Java",
            "languages": {"Java": 900, "HTML": 100},
            "updated_at": "",
            "readme_excerpt": "Spring Boot 短链\n服务",
        }
    ],
}


def test_tech_stack_collects_languages_and_topics():
    assert {"java", "html", "backend"} <= tech_stack(PROFILE)


def test_summary_mentions_repo_language_share_and_readme():
    text = summarize_profile(PROFILE)
    assert "short-link" in text and "Java90%" in text and "Spring Boot 短链 服务" in text


def test_save_and_load_roundtrip(tmp_path):
    path = save_profile(PROFILE, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["username"] == "demo"
    assert load_profile(tmp_path)["repos"][0]["name"] == "short-link"
    assert load_profile(tmp_path / "missing") is None
