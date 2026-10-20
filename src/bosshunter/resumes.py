"""Several resumes: a default one plus direction-specific ones chosen per job.

``profile.resume_path`` stays the default resume (every existing code path keeps
working). ``profile.resumes`` lists extra resumes, each with a free-text
``direction``. A job uses the extra resume whose direction matches the job's
role category, otherwise the default one.
"""

from pathlib import Path

from bosshunter.review_queue import direction_for_keyword


def extra_resumes(config: dict) -> list[dict]:
    """Normalised ``profile.resumes`` entries: [{"path": str, "direction": str}]."""
    raw = (config.get("profile") or {}).get("resumes")
    entries = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            entries.append({"path": str(item["path"]).strip(), "direction": str(item.get("direction") or "").strip()})
    return entries


def _same(a: str, b: str) -> bool:
    return bool(a) and a.strip().lower() == b.strip().lower()


def resume_path_for_job(config: dict, job: dict | None, plan: dict | None = None) -> str:
    """Path of the resume to use for ``job``: matching direction resume, else the default."""
    default = str((config.get("profile") or {}).get("resume_path") or "./resume.md")
    if not job:
        return default
    category = str(job.get("category") or "").strip()
    if not category:
        category = direction_for_keyword(str(job.get("source_keyword") or ""), plan)
    title = str(job.get("title") or "").lower()
    for entry in extra_resumes(config):
        direction = entry["direction"]
        if not direction or not Path(entry["path"]).exists():
            continue
        if _same(direction, category) or direction.lower() in title:
            return entry["path"]
    return default


def read_resume_for_job(config: dict, job: dict | None, plan: dict | None = None) -> str:
    """Resume text for a job ('' when the file is missing or unreadable)."""
    path = Path(resume_path_for_job(config, job, plan))
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        return ""
