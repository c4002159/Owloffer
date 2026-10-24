from bosshunter.ai import keyword_lint as lint
from bosshunter.ai.keywords import build_prompt, parse_plan


def test_the_words_that_polluted_the_pool_are_caught():
    for word in ("AI应用工程师", "大模型应用工程师", "微服务后端", "交付工程师", "实施工程师", "算法工程师", "嵌入式开发"):
        assert lint.check(word) is not None, word


def test_a_qualifier_makes_a_word_clear():
    for word in ("Java后端", "大模型算法工程师", "AI交付工程师", "AI应用开发工程师", "后端开发", "Agent工程师", "RAG工程师", "AI产品经理", "FDE工程师", "嵌入式AI", "Spring Boot"):
        assert lint.check(word) is None, word


def test_rewritable_words_are_fixed_and_the_rest_dropped():
    kept, changes = lint.clean_words(["AI应用工程师", "Agent工程师", "算法工程师", "微服务后端", "大模型算法工程师"])
    assert kept == ["AI应用开发工程师", "Agent工程师", "大模型算法工程师"]
    assert {(c["word"], c["action"]) for c in changes} == {("AI应用工程师", "rewritten"), ("算法工程师", "dropped"), ("微服务后端", "dropped")}
    assert [c["to"] for c in changes if c["action"] == "rewritten"] == ["AI应用开发工程师"]


def test_a_plan_from_the_model_is_cleaned_and_reports_what_changed():
    text = '{"core": ["Agent工程师", "AI应用工程师"], "expand": ["算法工程师", "Java后端"], "explore": ["实施工程师"], "intern": []}'
    plan = parse_plan(text)
    assert plan["core"] == ["Agent工程师", "AI应用开发工程师"]
    assert plan["expand"] == ["Java后端"] and plan["explore"] == []
    assert {c["word"] for c in plan["lint"]} == {"AI应用工程师", "算法工程师", "实施工程师"}
    assert all(c["tier"] in ("core", "expand", "explore") for c in plan["lint"])


def test_the_model_is_told_the_rules_in_its_prompt():
    prompt = build_prompt("技能说明", "简历", {})
    assert "不要生成含糊的关键词" in prompt and "应用开发工程师" in prompt and "算法工程师" in prompt
    assert prompt.index("不要生成含糊的关键词") < prompt.index("候选人设置")
