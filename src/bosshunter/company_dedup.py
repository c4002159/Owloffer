"""Treat name variants of one employer as the same company (华为 / 华为技术有限公司 / 上海华为…).

Rule: one job per company per day. Once a job from a company is approved today,
later jobs from the same company are filtered instead of being shown again.
"""

import re
import sqlite3
from datetime import datetime, timezone

_REGIONS = (
    "上海", "北京", "深圳", "广州", "杭州", "成都", "武汉", "南京", "苏州", "天津", "重庆", "西安", "长沙",
    "厦门", "合肥", "郑州", "青岛", "大连", "东莞", "佛山",
)
_LEGAL_SUFFIX = re.compile(r"(股份有限公司|有限责任公司|有限公司|股份公司|集团|控股)+$")
_NOISE = re.compile(r"[\s()（）·\-_/,，.。]")


def company_key(name: object) -> str:
    """Normalised core of a company name; '' when nothing usable is left."""
    key = _NOISE.sub("", str(name or "")).lower()
    key = _LEGAL_SUFFIX.sub("", key)
    for region in _REGIONS:
        if key.startswith(region) and len(key) >= len(region) + 2:
            key = key[len(region):]
            break
    return key


def same_company(a: object, b: object) -> bool:
    """True when the normalised names are equal or one starts with the other (at least 2 characters).

    Prefix matching groups 华为 / 华为技术 / 华为云计算 but keeps 招商银行 and 中国银行 apart.
    """
    key_a, key_b = company_key(a), company_key(b)
    if not key_a or not key_b:
        return False
    if key_a == key_b:
        return True
    short, long = sorted((key_a, key_b), key=len)
    return len(short) >= 2 and long.startswith(short)


def _local_date(timestamp: str):
    try:
        return datetime.fromisoformat(str(timestamp)).replace(tzinfo=timezone.utc).astimezone().date()
    except ValueError:
        return None


def approved_today(conn: sqlite3.Connection) -> list[dict]:
    """Jobs approved or sent today (local date): [{'id', 'company'}]."""
    today = datetime.now().date()
    rows = conn.execute(
        """
        SELECT h.job_id AS id, j.company AS company, h.created_at AS created_at
        FROM history h JOIN jobs j ON j.id = h.job_id
        WHERE h.action IN ('approved', 'sent') AND j.deleted_at IS NULL
          AND h.created_at >= datetime('now', '-2 day')
        """
    ).fetchall()
    seen: set[str] = set()
    result = []
    for row in rows:
        if _local_date(row["created_at"]) == today and row["id"] not in seen:
            seen.add(row["id"])
            result.append({"id": row["id"], "company": row["company"]})
    return result


def duplicate_of_approved_today(conn: sqlite3.Connection, job: dict) -> str | None:
    """Name of a company approved today that this job duplicates, else None."""
    for item in approved_today(conn):
        if item["id"] != job.get("id") and same_company(job.get("company"), item["company"]):
            return str(item["company"])
    return None
