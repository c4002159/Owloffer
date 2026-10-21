import json
from datetime import date

import pytest

from bosshunter.collection.platforms.job51 import native_filters_query
from bosshunter.collection.platforms.liepin import _work_year_code
from bosshunter.collection.platforms.zhilian import native_filter_params
from bosshunter.experience_level import heuristic_level, parse_level, resume_experience_level

TODAY = date(2026, 10, 21)


@pytest.mark.parametrize("answer,expected", [
    ('{"level": "fresh"}', "fresh"), ('```json\n{"level": "1-3"}\n```', "1-3"), ('{"level": "banana"}', ""), (None, ""), ("fresh", "fresh"),
])
def test_parse_level(answer, expected):
    assert parse_level(answer) == expected


def test_the_plain_readings_only_recognise_fresh_graduates():
    assert heuristic_level("2027届硕士，在读", TODAY) == "fresh"
    assert heuristic_level("2024届本科毕业，现任后端工程师", TODAY) == ""
    assert heuristic_level("应届毕业生", TODAY) == "fresh"


def test_the_model_decides_and_the_answer_is_kept_per_resume(tmp_path):
    asked = []

    def ask(config, prompt):
        asked.append(prompt)
        return '{"level": "under1"}'

    assert resume_experience_level({}, tmp_path, text="毕业半年，做过一份工作", ask=ask, today=TODAY) == "under1"
    assert resume_experience_level({}, tmp_path, text="毕业半年，做过一份工作", ask=ask, today=TODAY) == "under1"
    assert len(asked) == 1                                                          # the second time it came from the cache
    assert json.loads((tmp_path / "resume_experience.json").read_text(encoding="utf-8"))["level"] == "under1"
    assert resume_experience_level({}, tmp_path, text="另一份简历", ask=lambda c, p: '{"level": "5-10"}', today=TODAY) == "5-10"


def test_without_a_usable_answer_the_plain_reading_is_used(tmp_path):
    assert resume_experience_level({}, tmp_path, text="2027届在读硕士", ask=lambda c, p: None, today=TODAY) == "fresh"
    assert resume_experience_level({}, tmp_path, text="工程师，做了很多年", ask=lambda c, p: None, today=TODAY) == ""
    assert resume_experience_level({}, tmp_path, text="", today=TODAY) == ""


def _config(level):
    return {"_experience_level": level}


def test_the_platforms_follow_the_level_of_the_resume():
    assert native_filters_query(_config("fresh")) == "&workYear=01,06&jobType=01"                # 应届生 + 无需经验
    assert native_filters_query(_config("under1")) == "&workYear=01,06&jobType=01"
    assert native_filters_query(_config("5-10")) == "&jobType=01"                                  # left open
    assert native_filter_params(_config("fresh")) == {"et": "2", "we": "-1,0001"}                  # 经验不限 + 一年以下
    assert native_filter_params(_config("1-3")) == {"et": "2", "we": "-1,0001,0103"}
    assert _work_year_code(_config("fresh")) == "1"
    assert _work_year_code(_config("under1")) == "0$1"
    assert _work_year_code(_config("1-3")) == "1$3"


def test_explicit_settings_still_win_over_the_resume():
    own = {"_experience_level": "fresh", "platforms": {
        "51job": {"search": {"filters": {"work_year": "02"}}},
        "zhilian": {"search": {"filters": {"work_experience": ""}}},
        "liepin": {"search": {"filters": {"work_year": ""}}},
    }}
    assert native_filters_query(own) == "&workYear=02&jobType=01"
    assert native_filter_params(own) == {"et": "2"}
    assert _work_year_code(own) == ""


def test_the_boss_experience_setting_is_only_a_fallback_without_a_resume_level():
    boss = {"platforms": {"boss": {"search": {"filters": {"experience": ["应届生"]}}}}}
    assert native_filters_query(boss) == "&workYear=01,06&jobType=01"
    assert _work_year_code(boss) == "1"
    assert native_filters_query({**boss, "_experience_level": "5-10"}) == "&jobType=01"            # the resume says otherwise
