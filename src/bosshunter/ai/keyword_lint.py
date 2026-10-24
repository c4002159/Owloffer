"""Search keywords that mean something else on a recruiting site than the user wants.

A job-board search matches words, not intent. "应用工程师" is the title of field-application and chip-application engineers,
"数字后端" of chip layout work, "实施工程师" of factory and ERP rollouts. A keyword like that fills the pool with jobs the scoring
then throws away. The keyword model is told about these in its prompt (``prompt_section``), and every word it returns is checked
here (``clean_words``), so a vague word cannot get into the plan even when the model forgets the rule.

A rule says: a keyword containing ``match`` is vague unless it also contains one of the ``unless`` words (the qualifier that
makes it clear). A vague keyword is rewritten when the rule has a ``rewrite`` and dropped otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

AI_WORDS = ("AI", "大模型", "LLM", "Agent", "智能体", "RAG", "MCP", "GPT", "AIGC", "多模态", "提示词")


@dataclass(frozen=True)
class Rule:
    match: str
    reason: str
    unless: tuple[str, ...] = ()
    rewrite: tuple[str, str] | None = None


RULES: tuple[Rule, ...] = (
    Rule("应用工程师", "「应用工程师」在招聘网站上多指现场应用 / FAE / 芯片应用工程师，不是软件开发", rewrite=("应用工程师", "应用开发工程师")),
    Rule("算法工程师", "单独的「算法工程师」会搜出控制、机器人、图像、芯片等方向",
         unless=("大模型", "LLM", "NLP", "自然语言", "推荐", "搜索", "Agent", "智能体", "RAG", "多模态", "语音", "文本")),
    Rule("后端", "「后端」会搜出数字芯片后端设计", unless=("Java", "Python", "Golang", "服务端", "软件", "Spring", "后端开发", "后端工程师", *AI_WORDS)),
    Rule("实施工程师", "「实施工程师」多是工厂、财务软件、ERP 的现场实施", unless=(*AI_WORDS, "软件", "SaaS", "系统", "数据")),
    Rule("实施顾问", "「实施顾问」多是财务、ERP 软件实施", unless=(*AI_WORDS, "软件", "SaaS", "数据")),
    Rule("交付工程师", "「交付工程师」多是设备、工程交付", unless=(*AI_WORDS, "软件", "SaaS", "数据", "解决方案")),
    Rule("嵌入式", "「嵌入式」是硬件方向的岗位", unless=("AI", "大模型", "Agent", "智能体")),
)


def check(word: str) -> Rule | None:
    """The rule that makes ``word`` vague, or None when it is clear."""
    lowered = word.lower()
    for rule in RULES:
        if rule.match.lower() in lowered and not any(qualifier.lower() in lowered for qualifier in rule.unless):
            return rule
    return None


def clean_words(words: list[str]) -> tuple[list[str], list[dict]]:
    """``(kept, changes)``: vague words are rewritten (when the rule knows how) or dropped; ``changes`` says what happened."""
    kept: list[str] = []
    changes: list[dict] = []
    for word in words:
        rule = check(word)
        if rule is None:
            kept.append(word)
            continue
        if rule.rewrite:
            fixed = word.replace(*rule.rewrite)
            if check(fixed) is None:
                kept.append(fixed)
                changes.append({"word": word, "action": "rewritten", "to": fixed, "reason": rule.reason})
                continue
        changes.append({"word": word, "action": "dropped", "reason": rule.reason})
    return list(dict.fromkeys(kept)), changes


def prompt_section() -> str:
    """The rules as text for the keyword model's prompt."""
    lines = ["## 不要生成含糊的关键词（招聘网站按字面搜索，下面这些词会搜出不相关的岗位）"]
    for rule in RULES:
        if rule.rewrite:
            lines.append(f"- 不要用「{rule.match}」，改写成「{rule.rewrite[1]}」：{rule.reason}")
        elif rule.unless:
            lines.append(f"- 含「{rule.match}」的词必须同时带明确的方向词（如 {'、'.join(rule.unless[:6])}）：{rule.reason}")
        else:
            lines.append(f"- 不要用「{rule.match}」：{rule.reason}")
    lines.append("写关键词时先想：这个词在招聘网站上会不会同时指别的行业？会的话，加上方向词或换一种说法。")
    return "\n".join(lines)
