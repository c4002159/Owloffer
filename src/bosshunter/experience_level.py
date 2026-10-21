"""How much work experience the uploaded resume shows, so the platforms' own experience filter follows the resume.

The level is asked from the AI model once per resume (and year), kept in ``data/resume_experience.json``; without a model
a few plain readings of the text stand in (应届 / 在校 / 20xx届 / 毕业时间 in the future).
"""

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

LEVELS = ("fresh", "under1", "1-3", "3-5", "5-10", "10+")

PROMPT = """根据下面这份简历，判断候选人的工作经验档位。今天是 {today}。

档位只能是这六个之一：
- fresh：应届生或在校生（毕业时间在今年或以后，或刚毕业尚无正式工作，只有实习/项目也算）
- under1：已毕业，正式工作经验不足 1 年
- 1-3：1 到 3 年
- 3-5：3 到 5 年
- 5-10：5 到 10 年
- 10+：10 年以上

实习、课程项目、校内经历不计入正式工作年限。只输出 JSON：{{"level": "fresh"}}，不要解释。

## 简历
{resume}
"""


# A platform whose experience filter takes a single value gets several tags to alternate between.
LIEPIN_VARIANTS = {"fresh": ["1", "0$1"], "under1": ["0$1", "1"], "1-3": ["1$3", "0$1"]}


def liepin_variants(config: dict, filters: dict | None = None) -> list[str]:
    """The Liepin experience codes to alternate between ([] when one is set explicitly or the level has only one)."""
    if "work_year" in (filters or {}):
        return []
    platforms = config.get("platforms") if isinstance(config.get("platforms"), dict) else {}
    own = ((platforms.get("liepin") or {}).get("search") or {}).get("filters") or {}
    if "work_year" in own:
        return []
    return list(LIEPIN_VARIANTS.get(str(config.get("_experience_level") or ""), []))


def parse_level(answer: str | None) -> str:
    """The level in the model's answer ('' when there is none)."""
    if not answer:
        return ""
    match = re.search(r'"level"\s*:\s*"([^"]+)"', answer)
    level = (match.group(1) if match else answer).strip().strip('"')
    return level if level in LEVELS else ""


def heuristic_level(text: str, today: date | None = None) -> str:
    """'fresh' when the resume plainly says so; '' otherwise (no guess about years of work)."""
    today = today or date.today()
    if re.search(r"应届|在校|待毕业", text):
        return "fresh"
    years = [int(year) for year in re.findall(r"(20\d{2})\s*届", text)]
    if years and max(years) >= today.year:
        return "fresh"
    return ""


def _ask(config: dict, prompt: str) -> str | None:
    from bosshunter.ai.credentials import call_anthropic_text
    from bosshunter.ai.keywords import _keyword_config

    cfg = _keyword_config(config)
    ai_cfg = cfg.get("ai") or {}
    return call_anthropic_text(prompt, cfg, 256, timeout=ai_cfg.get("keyword_timeout_seconds", ai_cfg.get("timeout_seconds", 120)),
                               purpose="resume_experience")


def resume_experience_level(
    config: dict, cache_dir: Path, *, text: str | None = None, ask: Callable[[dict, str], str | None] | None = None,
    today: date | None = None,
) -> str:
    """The level of the main resume ('' when it cannot be told). ``text`` is read from the configured resume when omitted."""
    today = today or date.today()
    if text is None:
        try:
            from bosshunter.resumes import read_resume_for_job

            text = read_resume_for_job(config, None)
        except Exception:
            text = ""
    text = (text or "").strip()
    if not text:
        return ""
    key = hashlib.sha1(f"{today.year}|{text}".encode("utf-8")).hexdigest()
    cache_file = Path(cache_dir) / "resume_experience.json"
    cache: dict[str, Any] = {}
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if cache.get("key") == key and cache.get("level") in LEVELS:
        return str(cache["level"])
    level = ""
    try:
        level = parse_level((ask or _ask)(config, PROMPT.format(today=today.isoformat(), resume=text[:6000])))
    except Exception:
        level = ""
    level = level or heuristic_level(text, today)
    if level:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"key": key, "level": level}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return level
