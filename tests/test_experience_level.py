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


def test_the_tags_start_equally_likely_and_follow_the_quality_of_the_searches(tmp_path):
    import random

    from bosshunter import experience_bandit as bandit
    from bosshunter.db import get_db

    conn = get_db(tmp_path / "t.db")
    odds = bandit.weights(conn, "liepin", ["1", "0$1"])
    assert odds["1"] == pytest.approx(0.5) and odds["0$1"] == pytest.approx(0.5)              # no data: equal odds
    bandit.record(conn, "liepin", "1", seen=100, new=60, active=50)                           # 应届生: new and active jobs
    bandit.record(conn, "liepin", "0$1", seen=100, new=10, active=1)                          # 一年以内: mostly repeats
    odds = bandit.weights(conn, "liepin", ["1", "0$1"])
    assert odds["1"] > 0.8 and odds["0$1"] >= bandit.FLOOR - 1e-9                              # never below the floor
    picks = [bandit.choose(conn, "liepin", ["1", "0$1"], random.Random(seed)) for seed in range(300)]
    assert 0.7 < picks.count("1") / 300 < 0.97 and "0$1" in picks                             # mostly 应届生, still tries the other
    assert bandit.choose(conn, "liepin", ["only"]) == "only"
    conn.close()


def test_liepin_alternates_only_when_nothing_is_set_explicitly():
    from bosshunter.experience_level import liepin_variants

    assert liepin_variants({"_experience_level": "fresh"}) == ["1", "0$1"]
    assert liepin_variants({"_experience_level": "5-10"}) == []
    assert liepin_variants({"_experience_level": "fresh"}, {"work_year": "2"}) == []
    own = {"_experience_level": "fresh", "platforms": {"liepin": {"search": {"filters": {"work_year": "1"}}}}}
    assert liepin_variants(own) == []


def test_zhilian_alternates_between_no_requirement_and_under_a_year():
    from bosshunter.experience_level import experience_variants

    assert experience_variants("zhilian", {"_experience_level": "fresh"}) == ("work_experience", ["-1", "0001"])
    assert experience_variants("zhilian", {"_experience_level": "1-3"}) == ("work_experience", ["0001", "0103"])
    assert experience_variants("zhilian", {"_experience_level": "5-10"}) == ("work_experience", [])
    assert experience_variants("zhilian", {"_experience_level": "fresh"}, {"work_experience": "0001"})[1] == []
    own = {"_experience_level": "fresh", "platforms": {"zhilian": {"search": {"filters": {"work_experience": ""}}}}}
    assert experience_variants("zhilian", own)[1] == []
    assert experience_variants("liepin", {"_experience_level": "fresh"}) == ("work_year", ["1", "0$1"])
    assert experience_variants("51job", {"_experience_level": "fresh"}) == ("", [])


def test_zhilian_checkpoints_are_per_experience_tag():
    from bosshunter.collection.models import PlatformCollectionRequest
    from bosshunter.collection.platforms.zhilian import ZhilianCollector

    plain = PlatformCollectionRequest("zhilian", ["AI"], ["上海"], {"上海": "538"})
    tagged = PlatformCollectionRequest("zhilian", ["AI"], ["上海"], {"上海": "538"}, filters={"work_experience": "0001"})
    assert ZhilianCollector._ckey(plain, "AI") == "AI"
    assert ZhilianCollector._ckey(tagged, "AI") == "AI|0001"
