"""Read a user's public GitHub repositories once and cache a tech-stack summary locally.

The summary is appended to the resume text during scoring so that vague JD
requirements ("会 Java 编程") can be judged against real project evidence.
Only the public GitHub API is used; no token is required or stored.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "BossHunter-profile-sync"}
README_CHARS = 1200
CACHE_NAME = "github_profile.json"


def cache_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / CACHE_NAME


def _client() -> httpx.Client:
    return httpx.Client(timeout=20, headers=HEADERS, trust_env=False)


def fetch_profile(username: str, *, client: httpx.Client | None = None) -> dict:
    """Fetch non-fork public repos with languages and a README excerpt."""
    owns_client = client is None
    client = client or _client()
    try:
        response = client.get(f"{API}/users/{username}/repos", params={"per_page": 100, "sort": "updated"})
        response.raise_for_status()
        repos = []
        for repo in response.json():
            if repo.get("fork"):
                continue
            name = repo["name"]
            languages: dict[str, int] = {}
            lang_resp = client.get(f"{API}/repos/{username}/{name}/languages")
            if lang_resp.status_code == 200:
                languages = lang_resp.json()
            readme = ""
            readme_resp = client.get(
                f"{API}/repos/{username}/{name}/readme",
                headers={**HEADERS, "Accept": "application/vnd.github.raw+json"},
            )
            if readme_resp.status_code == 200:
                readme = readme_resp.text.strip()[:README_CHARS]
            repos.append({
                "name": name,
                "description": repo.get("description") or "",
                "topics": repo.get("topics") or [],
                "primary_language": repo.get("language") or "",
                "languages": languages,
                "updated_at": repo.get("updated_at") or "",
                "readme_excerpt": readme,
            })
        return {
            "username": username,
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repos": repos,
        }
    finally:
        if owns_client:
            client.close()


def save_profile(profile: dict, base_dir: Path) -> Path:
    path = cache_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_profile(base_dir: Path) -> dict | None:
    path = cache_path(base_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tech_stack(profile: dict) -> set[str]:
    """Lower-cased languages and topics across all cached repos (for keyword pre-screening)."""
    stack: set[str] = set()
    for repo in profile.get("repos", []):
        stack.update(name.lower() for name in repo.get("languages", {}))
        if repo.get("primary_language"):
            stack.add(str(repo["primary_language"]).lower())
        stack.update(str(topic).lower() for topic in repo.get("topics", []))
    return stack


def summarize_profile(profile: dict) -> str:
    """Render the cached profile as a compact Markdown block for the scoring prompt."""
    lines = [f"## GitHub 项目证据（用户 {profile.get('username', '')}，公开仓库，同步于 {profile.get('synced_at', '')}）"]
    for repo in profile.get("repos", []):
        total = sum(repo.get("languages", {}).values()) or 1
        langs = "、".join(
            f"{name}{count * 100 // total}%" for name, count in sorted(repo.get("languages", {}).items(), key=lambda kv: -kv[1])[:4]
        ) or repo.get("primary_language") or "未识别"
        line = f"- {repo['name']}（{langs}）"
        if repo.get("description"):
            line += f"：{repo['description']}"
        lines.append(line)
        excerpt = " ".join(str(repo.get("readme_excerpt", "")).split())[:300]
        if excerpt:
            lines.append(f"  README摘要：{excerpt}")
    return "\n".join(lines)
