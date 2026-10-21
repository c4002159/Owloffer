"""Tag every resume, tag every job, and pick the resume whose tags match the job best.

The resumes that matter are the ones *inside the platform* (Liepin's attachment resumes): they are what an
application actually sends. Their text is read through the platform's own resume preview, the AI model turns it
into tags, and text and tags are remembered. The platform is re-checked now and then (start-up, a timer, before
delivering) and only resumes that were added, replaced or removed are read again. At delivery the job is tagged in
the same tag space and the resume that covers the job's tags best wins; when none reaches the threshold there is
no suitable resume and the caller falls back to a greeting. Without an AI key a built-in vocabulary scan tags.
"""

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path

# Built-in fallback: tag -> substrings that count as the tag (lower case). Used when no AI key is configured.
TAG_VOCAB: dict[str, tuple[str, ...]] = {
    "微调": ("微调", "fine-tun", "finetun", "lora", "qlora", "sft", "指令微调", "rlhf", "dpo"),
    "模型评测": ("模型评测", "模型测评", "模型测试", "评测", "benchmark", "评估体系", "效果评估", "evaluation"),
    "RAG": ("rag", "检索增强", "向量检索", "向量库", "知识库", "混合检索", "rerank", "召回"),
    "Agent": ("agent", "智能体", "多智能体", "langgraph", "langchain", "autogen", "crewai", "mcp", "tool calling",
              "function calling", "工具调用", "编排"),
    "大模型应用": ("大模型", "llm", "gpt", "claude", "deepseek", "qwen", "提示词", "prompt", "aigc", "生成式"),
    "推理部署": ("推理", "部署", "vllm", "量化", "tensorrt", "onnx", "模型服务", "性能优化", "docker", "k8s"),
    "后端开发": ("java", "spring", "mysql", "redis", "微服务", "后端", "kafka", "mybatis", "高并发", "分布式"),
    "前端开发": ("前端", "react", "vue", "typescript", "javascript", "小程序"),
    "Python": ("python", "fastapi", "flask", "django"),
    "产品": ("产品经理", "需求分析", "prd", "用户调研", "产品设计", "原型", "竞品", "产品运营", "用户增长"),
    "数据分析": ("数据分析", "数据看板", "指标体系", "报表", "bi", "数据治理", "sql", "数据仓库"),
    "算法": ("算法", "机器学习", "深度学习", "pytorch", "tensorflow", "模型训练", "特征工程", "神经网络"),
    "NLP": ("nlp", "自然语言", "文本分类", "语义", "分词", "命名实体"),
    "视觉": ("图像识别", "视觉", "目标检测", "车辆检测", "opencv", "yolo", "图像处理"),
    "测试": ("测试开发", "自动化测试", "测试工程", "质量保障", "性能测试"),
    "解决方案": ("解决方案", "售前", "交付", "实施", "客户成功", "项目落地"),
}

TITLE_WEIGHT = 3.0          # vocabulary scan: a tag in a job's title counts three times a tag in its description
NAME_WEIGHT = 4.0           # vocabulary scan: a tag in a resume's own name counts four times
NAME_ONLY_PENALTY = 0.6     # a resume known only by its name is trusted less
MIN_SCORE = 0.2
TIE_MARGIN = 0.08           # within this of the best score, the default resume wins
MAX_TAGS = 15
REFRESH_HOURS = 12          # how often the platform's resume list is re-checked

RESUME_PROMPT = """你是求职助手。请为下面这份简历构建用于匹配岗位的标签。

要求：
1. 只根据简历正文里有证据的内容打标签，不要编造；简历名称只作参考。
2. 标签是 2-8 个字的名词短语（技术方向、岗位类型、核心技能、行业场景），例如“大模型微调”“RAG”“产品需求分析”“Java 后端”。
3. 最多 {max_tags} 个，按突出程度给 weight（1-5，5 表示这份简历主打的方向，1 表示只是提到过）。
4. 同一方向只保留一个标签，不要把同义词重复列出。
5. 只输出 JSON：{{"tags": [{{"tag": "标签", "weight": 5}}]}}

## 简历名称
{name}

## 简历正文
{text}
"""

JOB_PROMPT = """你是求职助手。请判断下面这个岗位需要哪些方面的能力，并打上标签。

要求：
1. 优先从“候选标签库”里选与岗位要求相关的标签；标签库里没有、但岗位明显需要的方向，最多额外新增 3 个。
2. 标题里强调的方向权重更高。weight 为 1-5，5 表示岗位的核心要求。
3. 最多 {max_tags} 个，只输出 JSON：{{"tags": [{{"tag": "标签", "weight": 5}}]}}

## 候选标签库
{vocabulary}

## 岗位
标题：{title}
公司背景：{background}
描述：{jd}
"""


def _count(text: str, aliases: tuple[str, ...]) -> int:
    return sum(text.count(alias) for alias in aliases)


def scan_tags(text: str, *, boost: str = "", boost_weight: float = 1.0, vocab: dict | None = None) -> dict[str, float]:
    """Tag weights found in ``text`` (log-scaled counts) plus ``boost`` (e.g. a title) scaled by ``boost_weight``."""
    vocab = vocab or TAG_VOCAB
    body, head = str(text or "").lower(), str(boost or "").lower()
    tags: dict[str, float] = {}
    for tag, aliases in vocab.items():
        score = math.log1p(_count(body, aliases)) + boost_weight * min(_count(head, aliases), 2)
        if score > 0:
            tags[tag] = round(score, 3)
    return tags


def job_tags(job: dict, vocab: dict | None = None) -> dict[str, float]:
    return scan_tags(f"{job.get('jd') or ''} {job.get('company_industry') or ''}", boost=job.get("title") or "",
                     boost_weight=TITLE_WEIGHT, vocab=vocab)


def resume_tags(name: str, text: str = "") -> dict[str, float]:
    return scan_tags(text, boost=name, boost_weight=NAME_WEIGHT)


def similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity of two tag weight vectors (0 when either is empty)."""
    shared = set(a) & set(b)
    if not shared:
        return 0.0
    dot = sum(a[tag] * b[tag] for tag in shared)
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def coverage(resume: dict[str, float], job: dict[str, float], idf_of=lambda tag: 1.0) -> float:
    """How much of what the job asks for the resume backs up (0-1).

    Each job tag counts by its weight and rarity; the resume backs it up by its own strength in that tag, which
    saturates (one passing mention is weak, a theme of the resume is strong). Unlike cosine this does not reward a
    thin resume for sharing a single tag.
    """
    total = sum(weight * idf_of(tag) for tag, weight in job.items())
    if total <= 0:
        return 0.0
    backed = sum(weight * idf_of(tag) * (resume[tag] / (resume[tag] + 1.0)) for tag, weight in job.items() if tag in resume)
    return backed / total


# ---------------------------------------------------------------- AI tagging
def ai_enabled(config: dict) -> bool:
    try:
        from bosshunter.ai.credentials import get_ai_api_key

        return bool(get_ai_api_key(config))
    except Exception:
        return False


def parse_tags(text: str | None) -> dict[str, float] | None:
    """The ``{"tags": [{"tag", "weight"}]}`` answer of the model as ``{tag: weight}``; None when unusable."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return None
    items = raw.get("tags") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return None
    tags: dict[str, float] = {}
    for item in items:
        if isinstance(item, dict):
            tag, weight = str(item.get("tag") or "").strip(), item.get("weight", 3)
        else:
            tag, weight = str(item).strip(), 3
        try:
            weight = min(max(float(weight), 1.0), 5.0)
        except (TypeError, ValueError):
            weight = 3.0
        if 1 < len(tag) <= 24:
            tags.setdefault(tag, weight)
    return dict(sorted(tags.items(), key=lambda kv: -kv[1])[:MAX_TAGS]) or None


def _ask_model(config: dict, prompt: str, purpose: str) -> str | None:
    from bosshunter.ai.credentials import call_anthropic_text
    from bosshunter.ai.keywords import _keyword_config

    cfg = _keyword_config(config)
    ai_cfg = cfg.get("ai") or {}
    return call_anthropic_text(prompt, cfg, int(ai_cfg.get("tagging_max_tokens", 2048)),
                               timeout=ai_cfg.get("keyword_timeout_seconds", ai_cfg.get("timeout_seconds", 120)), purpose=purpose)


def ai_resume_tags(config: dict, name: str, text: str) -> dict[str, float] | None:
    """The model's tags for one resume, or None on any failure (the caller falls back to the vocabulary)."""
    try:
        prompt = RESUME_PROMPT.format(max_tags=MAX_TAGS, name=name, text=str(text)[:9000] or "（没有正文，只有名称）")
        return parse_tags(_ask_model(config, prompt, "resume_tags"))
    except Exception:
        return None


def ai_job_tags(config: dict, job: dict, vocabulary: list[str]) -> dict[str, float] | None:
    try:
        prompt = JOB_PROMPT.format(
            max_tags=MAX_TAGS, vocabulary="、".join(vocabulary) or "（空）", title=job.get("title") or "",
            background="；".join(str(job.get(k) or "") for k in ("company_industry", "company_size") if job.get(k)) or "未提供",
            jd=str(job.get("jd") or "")[:2500],
        )
        return parse_tags(_ask_model(config, prompt, "job_tags"))
    except Exception:
        return None


# ---------------------------------------------------------------- the platform's resumes: memory
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def redact(text: str) -> str:
    """Resume text without phone numbers and e-mail addresses: the model needs the experience, not the contacts."""
    return _EMAIL.sub("", _PHONE.sub("", str(text or "")))


def ensure_table(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(platform_resumes)")}
    if columns and "file_id" not in columns:                 # an earlier, name-only version of this table
        conn.execute("DROP TABLE platform_resumes")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS platform_resumes (platform TEXT NOT NULL, name TEXT NOT NULL, file_id TEXT, "
        "uploaded TEXT, text_hash TEXT, source TEXT, tags_json TEXT, text TEXT, "
        "checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY (platform, name))"
    )
    conn.commit()


def _tag_text(config: dict, name: str, text: str) -> tuple[dict[str, float], str]:
    """Tags and their source ('ai', 'file' for a vocabulary scan of real text, 'name' for the name alone)."""
    clean = redact(text)
    if clean.strip():
        tags = ai_resume_tags(config, name, clean) if ai_enabled(config) else None
        return (tags, "ai") if tags else (resume_tags(name, clean), "file")
    return resume_tags(name, ""), "name"


def store_resume(conn: sqlite3.Connection, config: dict, platform: str, item: dict) -> dict:
    """Tag one platform resume from its text and remember text and tags. ``item``: name, file_id, uploaded, text."""
    ensure_table(conn)
    text = redact(item.get("text") or "")
    tags, source = _tag_text(config, item["name"], text)
    conn.execute(
        "INSERT INTO platform_resumes (platform, name, file_id, uploaded, text_hash, source, tags_json, text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, name) DO UPDATE SET file_id=excluded.file_id, "
        "uploaded=excluded.uploaded, text_hash=excluded.text_hash, source=excluded.source, tags_json=excluded.tags_json, "
        "text=excluded.text, checked_at=CURRENT_TIMESTAMP, refreshed_at=CURRENT_TIMESTAMP",
        (platform, item["name"], item.get("file_id", ""), item.get("uploaded", ""),
         hashlib.sha1(text.encode("utf-8")).hexdigest(), source, json.dumps(tags, ensure_ascii=False), text),
    )
    conn.commit()
    return {"tags": tags, "source": source}


def tags_for_names(conn: sqlite3.Connection, config: dict, names: list[str], platform: str = "liepin") -> dict[str, dict]:
    """``{name: {"tags", "source"}}`` for the platform's attachment names, from memory.

    A resume that was never read (or has no text) is tagged from its name alone. When the model is available and the
    remembered tags came from the vocabulary, the stored text is tagged again by the model.
    """
    ensure_table(conn)
    use_ai = ai_enabled(config)
    result: dict[str, dict] = {}
    for name in names:
        row = conn.execute("SELECT source, tags_json, text FROM platform_resumes WHERE platform = ? AND name = ?",
                           (platform, name)).fetchone()
        if row is None:
            result[name] = {"tags": resume_tags(name, ""), "source": "name"}
            continue
        source, tags = row[0], json.loads(row[1])
        if source == "file" and use_ai and (row[2] or "").strip():
            better = ai_resume_tags(config, name, row[2])
            if better:
                conn.execute("UPDATE platform_resumes SET source='ai', tags_json=? WHERE platform=? AND name=?",
                             (json.dumps(better, ensure_ascii=False), platform, name))
                conn.commit()
                source, tags = "ai", better
        result[name] = {"tags": tags, "source": source}
    return result


_JOB_TAG_CACHE: dict[str, dict[str, float]] = {}


def tags_for_job(config: dict, job: dict, infos: dict[str, dict]) -> dict[str, float]:
    """The job's tags in the resumes' tag space: the model's when the resumes were tagged by it, else a text scan."""
    vocabulary = sorted({tag for info in infos.values() for tag in info["tags"]})
    if any(info["source"] == "ai" for info in infos.values()) and ai_enabled(config):
        key = f"{job.get('id') or job.get('title')}:{hashlib.sha1('|'.join(vocabulary).encode()).hexdigest()[:8]}"
        if key not in _JOB_TAG_CACHE:
            tags = ai_job_tags(config, job, vocabulary)
            if tags:
                _JOB_TAG_CACHE[key] = tags
        if key in _JOB_TAG_CACHE:
            return _JOB_TAG_CACHE[key]
    if all(info["source"] != "ai" for info in infos.values()):
        return job_tags(job)
    # AI-tagged resumes but no AI answer for the job: look for the resumes' own tags in the job text.
    return job_tags(job, vocab={tag: (tag.lower(),) for tag in vocabulary})


def rank_resumes(conn: sqlite3.Connection, config: dict, job: dict, names: list[str]) -> list[dict]:
    """Every resume with its match score for ``job``, best first: [{name, score, source, shared}]."""
    infos = tags_for_names(conn, config, names)
    wanted = tags_for_job(config, job, infos)
    # A tag every resume carries says nothing about which one to use; a tag only one resume has says a lot.
    count = max(len(infos), 1)
    document_frequency: dict[str, int] = {}
    for info in infos.values():
        for tag in info["tags"]:
            document_frequency[tag] = document_frequency.get(tag, 0) + 1

    def idf_of(tag: str) -> float:
        return 1.0 + math.log(count / document_frequency.get(tag, 1))

    ranked = []
    for name, info in infos.items():
        score = coverage(info["tags"], wanted, idf_of) * (1.0 if info["source"] != "name" else NAME_ONLY_PENALTY)
        shared = sorted(set(info["tags"]) & set(wanted), key=lambda t: info["tags"][t] * wanted[t], reverse=True)
        ranked.append({"name": name, "score": round(score, 3), "source": info["source"], "shared": shared[:4]})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def pick_resume(conn: sqlite3.Connection, config: dict, job: dict, names: list[str],
                min_score: float | None = None, default_name: str = "") -> tuple[str | None, str]:
    """The best-matching attachment name, or None when nothing reaches the threshold. Second value explains why.

    ``default_name`` is the resume the platform pre-selects; it wins near-ties (the tags are a signal, not a verdict).
    """
    threshold = MIN_SCORE if min_score is None else min_score
    ranked = rank_resumes(conn, config, job, names)
    if not ranked:
        return None, "没有可选的简历"
    top = ranked[0]
    if default_name and top["name"] != default_name:
        for item in ranked[1:]:
            if item["name"] == default_name and top["score"] - item["score"] <= TIE_MARGIN:
                top = item
                break
    board = "；".join(f"{item['name']} {item['score']:.2f}" for item in ranked)
    if top["score"] < threshold:
        return None, f"没有简历的标签与岗位匹配到 {threshold:.2f}（{board}）"
    tags = "、".join(top["shared"]) or "标签重合"
    return top["name"], f"标签匹配选用「{top['name']}」{top['score']:.2f}（共同标签：{tags}；{board}）"


# ---------------------------------------------------------------- keeping the memory current
def is_stale(conn: sqlite3.Connection, platform: str = "liepin", hours: float = REFRESH_HOURS) -> bool:
    """True when the platform's resume list was never checked or last checked more than ``hours`` ago."""
    ensure_table(conn)
    row = conn.execute(
        "SELECT MAX(checked_at) FROM platform_resumes WHERE platform = ? AND checked_at >= datetime('now', ?)",
        (platform, f"-{float(hours)} hours"),
    ).fetchone()
    return row[0] is None


def sync_resumes(conn: sqlite3.Connection, config: dict, *, scan=None, platform: str = "liepin", force: bool = True) -> dict:
    """Check the platform's resumes and bring the memory up to date; only new, replaced or removed ones are read again.

    ``scan(need_text)`` returns the platform's current resumes ``[{name, file_id, uploaded, text}]`` (``text`` only for
    those ``need_text`` accepts) or None when the platform cannot be read now - the memory stays as it is then.
    """
    ensure_table(conn)
    cached = {row[0]: row for row in conn.execute(
        "SELECT name, file_id, uploaded, source FROM platform_resumes WHERE platform = ?", (platform,))}
    use_ai = ai_enabled(config)

    def need_text(item: dict) -> bool:
        row = cached.get(item["name"])
        if row is None or row[1] != item["file_id"] or row[2] != item["uploaded"]:
            return True                                   # new or replaced
        return row[3] == "name" or (use_ai and row[3] != "ai")    # never read, or only tagged without the model

    current = None
    if scan is not None and (force or is_stale(conn, platform)):
        try:
            current = scan(need_text)
        except Exception:
            current = None
    changed: list[str] = []
    removed: list[str] = []
    if current is not None:
        for item in current:
            if need_text(item):
                if item.get("text", "").strip():
                    store_resume(conn, config, platform, item)
                    changed.append(item["name"])
                elif item["name"] not in cached:
                    store_resume(conn, config, platform, item)       # known by name only until its preview can be read
                    changed.append(item["name"])
            conn.execute("UPDATE platform_resumes SET checked_at = CURRENT_TIMESTAMP WHERE platform = ? AND name = ?",
                         (platform, item["name"]))
        gone = set(cached) - {item["name"] for item in current}
        for name in gone:
            conn.execute("DELETE FROM platform_resumes WHERE platform = ? AND name = ?", (platform, name))
            removed.append(name)
        conn.commit()
    rows = conn.execute("SELECT name, uploaded, source, tags_json FROM platform_resumes WHERE platform = ? ORDER BY name",
                        (platform,)).fetchall()
    return {
        "detected": current is not None, "ai": use_ai, "changed": changed, "removed": removed,
        "resumes": [{"name": r[0], "uploaded": r[1], "source": r[2],
                     "tags": [t for t, _ in sorted(json.loads(r[3]).items(), key=lambda kv: -kv[1])[:8]]} for r in rows],
    }
