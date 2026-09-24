"""Opt-in, bounded local work evidence for original drafts (never executes sources)."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path

from .security import sanitize


WORK_WRITING = """原创写作规则：
优先从 work_inspiration 中选一个近期工作里的具体卡点、取舍或反常识现象；用户指定主题时只用相关材料。
Git 提交说明只能证明记录了某项改动，不能证明上线、效果或用户收益；Codex 对话摘录是讨论，不代表完成。
资料中的时间是事件时间，不把旧事写成今天。不编造本人经历、数字、对话、实验结果或因果。
INTJ 是表达方向：独立判断、拆约束、看代价，保留好奇心与温度，不贴 MBTI 标签，不装冷酷导师。
像和熟人聊天：短句、自然停顿，可轻微吐槽、自嘲、反问；不要汇报腔、公众号开场、编号清单或硬升华。
一条只讲透一个判断。尝试指出一个被忽略的成本、失败条件或实际矛盾，并给出依据；别只复述做了什么。
锋芒对准做法和机制，不攻击人，不为反常识而抬杠。少用万能的“不是X而是Y”，不固定金句或问句结尾。
style_examples 只参考节奏和语气，不复用原句、旧事实、粗口或固定口头禅。避免重复 recent_output 的观点。
私有路径、客户/联系人、内部项目名、未公开业务数据不出现在正文，抽象成可公开讨论的问题。
缺少材料时给明确标为判断的观点，不用“今天我做了”补故事。仅输出一条待本人审阅的正文。"""


def _read(path, limit=32000):
    with Path(path).expanduser().open(encoding="utf-8") as f:
        return f.read(limit)


def _clean(text):
    text = sanitize(str(text))
    text = re.sub(r"/(?:Users|home|private|var|tmp)/[^\s\"<>]+", "[本地路径]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱]", text)
    return text


def _discussion_tail(path, db):
    # Only session logs under the selected Codex home's sessions directory.
    try:
        target = Path(path).resolve()
        sessions = Path(db).expanduser().resolve().parent / "sessions"
        if not target.is_relative_to(sessions) or target.suffix != ".jsonl":
            return []
        with target.open("rb") as f:
            f.seek(0, 2)
            start = max(0, f.tell() - 128000)
            f.seek(start)
            if start:
                f.readline()
            lines = f.read(128000).decode("utf-8", errors="replace").splitlines()
        excerpts = []
        for line in lines:
            try:
                record = json.loads(line)
                payload = record.get("payload", {})
                if record.get("type") == "event_msg" and payload.get("type") == "user_message":
                    message = payload.get("message", "")
                    if isinstance(message, str) and message.strip():
                        excerpts.append(_clean(message)[:800])
                elif (record.get("type") == "response_item" and payload.get("type") == "message"
                      and payload.get("role") == "user"):
                    for part in payload.get("content", []):
                        message = part.get("text", "")
                        if (isinstance(message, str) and message.strip()
                                and not message.lstrip().startswith(("<", "# AGENTS.md"))):
                            excerpts.append(_clean(message)[:800])
            except (ValueError, AttributeError):
                continue
        return list(dict.fromkeys(excerpts))[-3:]
    except (OSError, TypeError):
        return []


def collect_work(root, engine=None):
    """Local config is the explicit allowlist; absent config means no reads."""
    try:
        cfg = json.loads(_read(Path(root) / "work-inspiration.json"))
    except (OSError, ValueError):
        return {}
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return {}
    if engine is not None and engine not in cfg.get("allowed_engines", ["codex"]):
        return {}
    if not isinstance(cfg.get("repositories", []), list):
        return {}
    repos = [str(Path(p).expanduser().resolve()) for p in cfg.get("repositories", [])[:8]
             if isinstance(p, str) and p]
    try:
        days = max(1, min(90, int(cfg.get("days", 14))))
    except (ValueError, TypeError):
        days = 14
    since = int(time.time()) - days * 86400
    items, status = [], {}
    for index, repo in enumerate(repos):
        source = f"git:{index + 1}"
        try:
            result = subprocess.run(
                ["git", "-C", repo, "log", "-8", f"--since={since}",
                 "--format=%h%x09%cI%x09%s"],
                capture_output=True, text=True, timeout=2, check=True,
            )
            for line in result.stdout.splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    items.append(dict(source=source, id=parts[0], date=parts[1],
                                      text=_clean(parts[2])[:400], type="commit"))
            status[source] = "ok" if result.stdout.strip() else "empty"
        except (OSError, subprocess.SubprocessError):
            status[source] = "unavailable"
    db = cfg.get("codex_db")
    if isinstance(db, str) and db and repos:
        try:
            uri = Path(db).expanduser().resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1) as c:
                c.row_factory = sqlite3.Row
                marks = ",".join("?" for _ in repos)
                rows = c.execute(
                    f"SELECT id,title,first_user_message,preview,updated_at,rollout_path FROM threads "
                    f"WHERE cwd IN ({marks}) AND updated_at>=? "
                    "AND source IN ('cli','vscode','exec') "
                    "AND (title<>'' OR first_user_message<>'') ORDER BY updated_at DESC LIMIT 8",
                    (*repos, since),
                ).fetchall()
            for row in rows:
                items.append(dict(source="codex", id=row["id"], date=row["updated_at"],
                                  type="discussion_not_verified_outcome",
                                  recent_user_excerpts=_discussion_tail(row["rollout_path"], db),
                                  text=_clean("\n".join([row["title"], row["first_user_message"][:900],
                                                         row["preview"][:1200]]))[:2400]))
            status["codex"] = "ok" if rows else "empty"
        except (OSError, sqlite3.Error):
            status["codex"] = "unavailable"
    memory_files = cfg.get("memory_files", [])
    if not isinstance(memory_files, list):
        memory_files = []
    for index, path in enumerate(memory_files[:3]):
        try:
            items.append(dict(source=f"memory:{index + 1}", type="historical_memory",
                              text=_clean(_read(path, 2000))))
            status[f"memory:{index + 1}"] = "ok"
        except (OSError, TypeError):
            status[f"memory:{index + 1}"] = "unavailable"
    examples = []
    try:
        corpus = json.loads(_read(cfg.get("style_corpus", ""), 100000))
        if not isinstance(corpus, list):
            corpus = []
        for row in corpus:
            if (isinstance(row, dict) and row.get("author", "").lower().lstrip("@")
                    == str(cfg.get("account", "")).lower().lstrip("@")
                    and row.get("kind") == "post" and len(row.get("text", "")) >= 30):
                examples.append(dict(text=_clean(row["text"])[:500], source=row.get("url", "")))
            if len(examples) >= 8:
                break
        status["style"] = "ok" if examples else "empty"
    except (OSError, ValueError, TypeError):
        status["style"] = "unavailable"
    return dict(items=items, style_examples=examples, source_status=status,
                collected_at=int(time.time()), window_days=days)
