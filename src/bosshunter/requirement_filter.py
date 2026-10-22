"""Keep only the postings whose experience and education requirements fit the user's own filter settings.

The BOSS filter lists on the config page (工作经验, 学历要求) are the user's limit for every platform: the other sites cannot
all filter by them natively, so the collectors read the requirements from the posting (its labels and its JD text) and drop
what asks for more than the settings allow.
"""

from __future__ import annotations

import re
from typing import Any

# The most years of required experience each label of the 工作经验 list still accepts.
EXPERIENCE_CEILING = {
    "经验不限": 0.0, "不限": 0.0, "应届生": 0.0, "应届": 0.0, "在校生": 0.0, "在校/应届": 0.0,
    "1年以内": 0.99, "1年以下": 0.99,
    "1-3年": 1.0, "3-5年": 3.0, "5-10年": 5.0, "10年以上": 10.0,
}
UNLIMITED = 99.0
DEGREE_RANK = {"初中及以下": 0, "高中": 1, "中专/中技": 1, "中专": 1, "中技": 1, "大专": 2, "专科": 2, "本科": 3, "硕士": 4, "博士": 5}

_RANGE = re.compile(r"(\d{1,2})\s*(?:-|~|－|—|至|到)\s*(\d{1,2})\s*年")
_AT_LEAST = re.compile(r"(\d{1,2})\s*年(?:及)?(?:以上|起)|(?:不少于|至少|不低于|满)\s*(\d{1,2})\s*年")
_EXPERIENCE_WORDS = re.compile(r"经验|工作|开发|相关|从业|项目|行业|实战|经历")
_SENTENCES = re.compile(r"[\n。；;]")
_SOFT = re.compile(r"优先|加分|更佳|最好|尤佳|者优")
_FRESH = re.compile(r"经验不限|无需经验|不限经验|无经验|应届|在校|实习")


def limits(config: dict, platform: str = "") -> tuple[list[str], list[str]]:
    """The experience and degree labels the user allows: the platform's own filter lists when it has some, else BOSS's."""
    platforms = config.get("platforms") if isinstance(config.get("platforms"), dict) else {}

    def filters_of(name: str) -> dict:
        search = (platforms.get(name) or {}).get("search") if isinstance(platforms.get(name), dict) else {}
        found = (search or {}).get("filters") if isinstance(search, dict) else {}
        return found if isinstance(found, dict) else {}

    own, boss = filters_of(platform) if platform else {}, filters_of("boss")

    def pick(key: str) -> list[str]:
        for source in (own, boss):
            value = source.get(key)
            if isinstance(value, list) and value:
                return [str(item).strip() for item in value if str(item).strip()]
        return []

    return pick("experience"), pick("degree")


def _max_experience(labels: list[str]) -> float | None:
    """The most years the allowed labels accept; None when the user set no experience limit."""
    ceilings = [EXPERIENCE_CEILING[label] for label in labels if label in EXPERIENCE_CEILING]
    if not ceilings:
        return None
    return max(ceilings)


def _label_years(label: str) -> float | None:
    text = str(label or "")
    if match := _RANGE.search(text):
        return float(match.group(1))
    if match := _AT_LEAST.search(text):
        return float(match.group(1) or match.group(2))
    if re.search(r"1年以内|1年以下|一年以内", text) or _FRESH.search(text):
        return 0.0
    return None


def _jd_years(jd: str) -> float | None:
    found: list[float] = []
    for sentence in _SENTENCES.split(str(jd or "")):
        if not _EXPERIENCE_WORDS.search(sentence):
            continue
        for match in list(_AT_LEAST.finditer(sentence)) + list(_RANGE.finditer(sentence)):
            around = sentence[max(0, match.start() - 8): match.end() + 10]
            if _SOFT.search(around) or "公司" in sentence[max(0, match.start() - 6): match.start()]:
                continue
            years = match.group(1) or (match.group(2) if match.lastindex and match.lastindex >= 2 else None)
            if years:
                found.append(float(years))
    return min(found) if found else None


def required_years(label: str, jd: str) -> float | None:
    """The years of experience a posting asks for: the larger of what its label says (``1-3年``, ``经验不限``) and what the
    JD's sentences about experience say (labels are often left at 不限 while the text asks for years); ``None`` when
    nothing is stated. Wishes (优先, 加分) do not count."""
    stated = [years for years in (_label_years(label), _jd_years(jd)) if years is not None]
    return max(stated) if stated else None


def required_degree(label: str, jd: str) -> int | None:
    """The lowest degree rank a posting accepts, from its label; ``None`` when it states none (学历不限)."""
    text = str(label or "")
    if "学历不限" in text or not text.strip():
        return None
    ranks = [rank for name, rank in DEGREE_RANK.items() if name in text]
    return min(ranks) if ranks else None


def doctorate_only(jd: str) -> bool:
    return bool(re.search(r"博士(?:研究生)?(?:及以上|学历|以上)|(?:要求|学历|需)[^。\n]{0,6}博士", str(jd or ""))) and not re.search(r"硕士|本科|学历不限", str(jd or ""))


def requirement_reason(config: dict, platform: str, experience: str, education: str, jd: str = "") -> str:
    """Why the posting does not fit the user's experience and education settings; empty when it fits."""
    experience_labels, degree_labels = limits(config, platform)
    ceiling = _max_experience(experience_labels)
    label_text = f"{experience} {education}"
    if ceiling is not None:
        years = required_years(label_text, jd)
        if years is not None and years > ceiling:
            return f"要求 {years:g} 年以上经验，超出设置的工作经验（{'、'.join(experience_labels)}）"
    allowed = {DEGREE_RANK[name] for name in degree_labels if name in DEGREE_RANK}
    if allowed:
        # The label names the posting's own degree (as the BOSS filter does): 大专 postings are not what a 本科/硕士 list wants.
        rank = required_degree(label_text, jd)
        if rank is not None and rank not in allowed:
            return f"学历要求（{_degree_name(rank)}）不在设置的学历内（{'、'.join(degree_labels)}）"
        if max(allowed) < DEGREE_RANK["博士"] and doctorate_only(jd):
            return "要求博士学历，不在设置的学历内"
    return ""


def _degree_name(rank: int) -> str:
    for name in ("初中及以下", "高中", "大专", "本科", "硕士", "博士"):
        if DEGREE_RANK[name] == rank:
            return name
    return str(rank)
