"""Ask the model for directions and words the search does not cover yet, with the market's numbers in front of it.

The first plan (``keywords.py``) is written once from the resume. This is the second source: it is given what the collection has
learned since (which words pass, which words are common but never got scored, how the current keywords perform) and is asked for
adjacent directions and different ways of naming the same work. Every proposed word goes through the same vague-word check as the
plan; nothing is added to the search by this module: the words become suggestions that the user accepts in the pop-up.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from bosshunter.ai import keyword_lint
from bosshunter.ai.credentials import call_anthropic_text, get_ai_api_key

STORE_NAME = "keyword_expansion.json"
TTL_DAYS = 7
MAX_WORDS = 12
MAX_RESUME_CHARS = 5000


def store_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / STORE_NAME


def resume_text(base_dir: Path, config: dict) -> str:
    """The resumes the user keeps (the library, then the main one), each shortened: directions come from all of them."""
    parts: list[str] = []
    seen: set[str] = set()
    paths = sorted((Path(base_dir) / "data" / "resumes").glob("*.md")) if (Path(base_dir) / "data" / "resumes").is_dir() else []
    main = (config.get("profile") or {}).get("resume_path")
    for path in [Path(main)] if main else []:
        paths.insert(0, path)
    for path in paths:
        try:
            key = str(path.resolve())
            if key in seen or not path.is_file():
                continue
            seen.add(key)
            parts.append(f"### 简历《{path.stem}》\n{path.read_text(encoding='utf-8', errors='replace')[:MAX_RESUME_CHARS]}")
        except OSError:
            continue
        if len(parts) >= 4:
            break
    return "\n\n".join(parts)


def build_prompt(resume: str, keywords: list[dict], mined: dict, config: dict) -> str:
    profile = config.get("profile") or {}
    cities = "、".join(str(city) for city in profile.get("target_cities") or []) or "未填写"
    current = "\n".join(f"- {item['keyword']}（{item['tier']}，近 14 天 {item.get('n', 0)} 个岗位，通过 {item.get('pass_rate', 0):.0%}）" for item in keywords) or "（还没有）"
    lift = "\n".join(f"- {item['term']}（{'岗位描述' if item.get('source') == 'jd' else '标题'}）：{item['scored']} 个评分过的岗位里通过 {item['passed']} 个（{item['rate']:.0%}，是平均的 {item['lift']} 倍）" for item in mined.get("lift", [])) or "（没有）"
    frequent = "\n".join(f"- {item['term']}：在 {item['seen']} 个岗位标题里出现，但几乎没被评分过" for item in mined.get("frequent", [])) or "（没有）"
    return (
        "你是求职搜索策略师。下面是候选人的简历，以及招聘平台搜索一段时间后得到的数据。请提出**现有关键词没有覆盖的新方向和新说法**，"
        "让搜索更全面又不跑偏。\n\n"
        f"## 候选人设置\n目标城市：{cities}\n\n## 简历\n{resume or '（没有简历文本）'}\n\n"
        f"## 现有关键词及近期表现\n{current}\n\n"
        f"## 市场数据一：通过评分明显高于平均、但现有关键词没有覆盖的词\n{lift}\n\n"
        f"## 市场数据二：在岗位标题里很常见、但几乎没被评分过的词（可能是被相关性预筛挡掉的方向，请判断是否和候选人相关）\n{frequent}\n\n"
        f"{keyword_lint.prompt_section()}\n\n"
        "## 要求\n"
        "- 先找 2–4 个**新方向**或新的叫法（同义岗位名、中英文写法、相邻岗位、技术栈或行业场景命名），每个方向给 1–4 个搜索词。\n"
        "- 只给与简历能力相符、现有关键词没有的词；不要重复现有关键词，也不要只是加标点或空格的变体。\n"
        "- 每个词 2–12 个字，是招聘平台岗位标题里常见的写法；不写公司名、薪资、城市。\n"
        "- 对每个方向说明理由（引用简历内容或上面的数据，40 字内）。\n"
        f"- 最多 {MAX_WORDS} 个词，宁可少而准。\n\n"
        '## 输出（严格 JSON，不要多余文字）\n{"directions": [{"name": "方向名", "reason": "理由", "keywords": ["词1", "词2"]}]}\n'
    )


def parse(text: str, existing: list[str]) -> list[dict]:
    """Suggestions from the model's answer: cleaned of vague words, not already searched, limited."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return []
    known = {re.sub(r"\s+", "", word).lower() for word in existing}
    suggestions: list[dict] = []
    for direction in raw.get("directions") or []:
        if not isinstance(direction, dict):
            continue
        name = str(direction.get("name") or "").strip()[:30]
        reason = str(direction.get("reason") or "").strip()[:120]
        words, _changes = keyword_lint.clean_words([str(word).strip() for word in direction.get("keywords") or [] if str(word).strip()])
        for word in words:
            key = re.sub(r"\s+", "", word).lower()
            if 2 <= len(word) <= 24 and key not in known:
                known.add(key)
                suggestions.append({"keyword": word, "direction": name, "reason": reason, "source": "model"})
    return suggestions[:MAX_WORDS]


def load(base_dir: Path, now: datetime | None = None) -> list[dict]:
    """The stored suggestions that have not expired."""
    try:
        data = json.loads(store_path(base_dir).read_text(encoding="utf-8-sig"))
        generated = datetime.fromisoformat(data["generated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if (now or datetime.now()) - generated > timedelta(days=TTL_DAYS):
        return []
    return [item for item in data.get("suggestions") or [] if isinstance(item, dict) and item.get("keyword")]


def forget(base_dir: Path, keywords: list[str]) -> None:
    """Drop suggestions that were accepted (or are now searched): they are no longer suggestions."""
    path = store_path(base_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return
    gone = {re.sub(r"\s+", "", word).lower() for word in keywords}
    data["suggestions"] = [item for item in data.get("suggestions") or [] if re.sub(r"\s+", "", str(item.get("keyword"))).lower() not in gone]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def expand(config: dict, conn: sqlite3.Connection, base_dir: Path, words: list[str], tiers: dict, scores: dict, mined: dict) -> list[dict] | None:
    """Ask the model and store its suggestions. ``None`` when there is no AI connection or the call failed."""
    if not get_ai_api_key(config):
        return None
    keywords = [
        {"keyword": word, "tier": tier, "n": scores.get(word, {}).get("n", 0), "pass_rate": scores.get(word, {}).get("pass_rate", 0.0)}
        for tier in ("core", "expand", "explore", "paused") for word in tiers.get(tier, [])
    ]
    prompt = build_prompt(resume_text(base_dir, config), keywords, mined, config)
    ai_cfg = config.get("ai") or {}
    cfg = json.loads(json.dumps(config))
    model = str(ai_cfg.get("keyword_model") or "").strip()
    if model:
        cfg.setdefault("ai", {})["model"] = model
    text = call_anthropic_text(prompt, cfg, int(ai_cfg.get("keyword_max_tokens", 4096)),
                               timeout=ai_cfg.get("keyword_timeout_seconds", ai_cfg.get("timeout_seconds", 180)), purpose="keywords")
    if not text:
        return None
    suggestions = parse(text, words)
    path = store_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"), "suggestions": suggestions}, ensure_ascii=False, indent=1), encoding="utf-8")
    return suggestions
