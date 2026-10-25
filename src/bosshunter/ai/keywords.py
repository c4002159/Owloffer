"""AI search-keyword planning: resume + GitHub evidence + skill file -> tiered keywords."""

import copy
import json
import re
import sqlite3
from pathlib import Path

from bosshunter.ai import keyword_lint
from bosshunter.ai.credentials import call_anthropic_text, get_ai_api_key

TIERS = ("core", "expand", "explore", "intern")
TIER_LIMITS = {"core": 8, "expand": 12, "explore": 6, "intern": 8}
SKILL_PATH = Path(__file__).resolve().parents[3] / "skills" / "keyword_expander.md"
PLAN_NAME = "keyword_plan.json"


def plan_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / PLAN_NAME


def load_skill(config: dict) -> str:
    """Read the skill file; ``ai.keyword_skill_path`` may point at a user-edited copy."""
    custom = (config.get("ai") or {}).get("keyword_skill_path")
    path = Path(custom) if custom else SKILL_PATH
    return path.read_text(encoding="utf-8")


def _keyword_config(config: dict) -> dict:
    """Config copy that calls ``ai.keyword_model`` when one is set."""
    cfg = copy.deepcopy(config)
    ai_cfg = cfg.setdefault("ai", {})
    model = str(ai_cfg.get("keyword_model") or "").strip()
    if model:
        ai_cfg["model"] = model
    return cfg


def build_prompt(skill: str, resume: str, config: dict) -> str:
    skill = skill + "\n\n" + keyword_lint.prompt_section()
    profile = config.get("profile") or {}
    cities = "、".join(str(c) for c in profile.get("target_cities") or []) or "未填写"
    # The settings template ships with "你的目标岗位1 / 目标岗位2 / 目标岗位3": those are not roles the candidate wrote.
    written = [str(r).strip() for r in profile.get("target_roles") or [] if str(r).strip() and not re.fullmatch(r"(?:你的)?目标岗位\s*\d*", str(r).strip())]
    roles = "、".join(written) or "未填写（请从简历推断）"
    return (
        f"{skill}\n\n---\n"
        f"## 候选人设置\n目标城市：{cities}\n候选人已写明的意向岗位：{roles}\n\n"
        f"## 候选人资料（简历与 GitHub 项目证据）\n{resume}\n"
    )


def parse_plan(text: str) -> dict | None:
    """Extract and normalise the JSON plan; returns None when it is unusable."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None

    plan: dict = {}
    seen: set[str] = set()
    lint: list[dict] = []
    for tier in TIERS:
        if tier == "intern":
            seen = set()  # internship words are a separate search, not deduplicated against the others
        words: list[str] = []
        proposed, changes = keyword_lint.clean_words([str(item).strip() for item in raw.get(tier) or []])
        lint.extend({**change, "tier": tier} for change in changes)
        for item in proposed:
            word = str(item).strip()
            key = re.sub(r"\s+", "", word).lower()
            if word and key not in seen and 2 <= len(word) <= 24:
                seen.add(key)
                words.append(word)
        plan[tier] = words[: TIER_LIMITS[tier]]
    if not any(plan[tier] for tier in TIERS if tier != "intern"):
        return None
    plan["lint"] = lint                               # vague words the model proposed and what was done with them
    plan["excludes"] = [str(x).strip() for x in raw.get("excludes") or [] if str(x).strip()]
    plan["directions"] = [
        {
            "name": str(d.get("name", "")).strip(),
            "evidence": str(d.get("evidence", "")).strip(),
            "keywords": [str(k).strip() for k in d.get("keywords") or [] if str(k).strip()],
        }
        for d in raw.get("directions") or []
        if isinstance(d, dict) and d.get("name")
    ]
    return plan


def generate_plan(config: dict, resume: str) -> dict | None:
    """Ask the keyword model for a tiered plan. Returns None on API or parse failure."""
    if not get_ai_api_key(config):
        return None
    cfg = _keyword_config(config)
    ai_cfg = cfg.get("ai") or {}
    prompt = build_prompt(load_skill(config), resume, config)
    text = call_anthropic_text(
        prompt,
        cfg,
        int(ai_cfg.get("keyword_max_tokens", 4096)),
        timeout=ai_cfg.get("keyword_timeout_seconds", ai_cfg.get("timeout_seconds", 180)),
        purpose="keywords",
    )
    return parse_plan(text or "")


def save_plan(plan: dict, base_dir: Path) -> Path:
    path = plan_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_plan(base_dir: Path) -> dict | None:
    path = plan_path(base_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def select_keywords(plan: dict, tiers: tuple[str, ...] = ("core",), explore_limit: int = 2) -> list[str]:
    """Pick the keywords to search now: chosen tiers in full, explore capped."""
    chosen: list[str] = []
    for tier in tiers:
        words = plan.get(tier, [])
        chosen.extend(words[:explore_limit] if tier == "explore" else words)
    return chosen


def keyword_yield(conn: sqlite3.Connection, threshold: int) -> list[dict]:
    """Per search keyword: jobs collected and how many scored at or above the threshold."""
    rows = conn.execute(
        """
        SELECT COALESCE(source_keyword, '') AS keyword,
               COUNT(*) AS collected,
               SUM(CASE WHEN score >= ? THEN 1 ELSE 0 END) AS qualified
        FROM jobs
        WHERE deleted_at IS NULL AND COALESCE(source_keyword, '') != ''
        GROUP BY keyword
        ORDER BY qualified DESC, collected DESC
        """,
        (threshold,),
    ).fetchall()
    return [
        {
            "keyword": row["keyword"],
            "collected": row["collected"],
            "qualified": row["qualified"] or 0,
            "rate": round((row["qualified"] or 0) / row["collected"], 2) if row["collected"] else 0.0,
        }
        for row in rows
    ]
