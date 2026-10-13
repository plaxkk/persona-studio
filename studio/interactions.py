"""Bounded interaction selection. Scores describe review rules, not X ranking."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

WEIGHTS = {"topic": 30, "contribution": 30, "discussion": 20, "learning": 10, "timing": 10}
TOPICS = {
    "developer": ("开发", "代码", "编程", "github", "codex", "agent", "api", "模型", "开源", "工程", "产品", "react", "python", "llm", "ai"),
    "travel": ("旅行", "旅游", "入境", "签证", "中国游", "china travel", "visa", "travel", "itinerary", "高铁", "景点", "支付"),
}


def local_day(store):
    return datetime.now(ZoneInfo(store.get("timezone", "Asia/Shanghai"))).date().isoformat()


def topic_for(text):
    text = text.lower()
    def present(term):
        return bool(re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text)) if term.isascii() else term in text
    counts = {topic: sum(present(term) for term in terms) for topic, terms in TOPICS.items()}
    return max(counts, key=counts.get) if any(counts.values()) else "other"


def scene_for(post, context):
    if post["kind"] in ("reply", "mention", "quote") or context.get("source") in ("replies", "mentions", "notifications", "quotes"):
        return "direct"
    if re.search(r"\?|？|怎么|如何|why|how|anyone|有人", post["text"], re.I):
        return "question"
    return "discussion"


def context_state(context):
    if context.get("manual_context_note"):
        return "ready", "人工查看原帖并补充了上下文"
    if context.get("reader") != "browser":
        if context.get("parent_id") and not context.get("parent", {}).get("text"):
            return "pending", "缺少会话上文"
        if context.get("quote", {}).get("id") and not context.get("quote", {}).get("text"):
            return "pending", "缺少引用内容"
        return "ready", "已读取完整正文与现有引用数据"
    if not context.get("detail_checked"):
        return "pending", "尚未读取原帖详情"
    if context.get("truncated"):
        return "pending", "正文可能被截断"
    if context.get("parent_needed") and not context.get("parent", {}).get("text"):
        return "pending", "缺少会话上文"
    if context.get("quote_needed") and not context.get("quote", {}).get("text"):
        return "pending", "缺少引用内容"
    if context.get("media_pending"):
        return "pending", "媒体内容尚未确认"
    return "ready", "原帖与必要上下文已读取"


def score_post(post, skills, review=None):
    context = post["context"]
    status, issue = context_state(context)
    topic = topic_for(post["text"])
    scene = scene_for(post, context)
    if status != "ready":
        return {"context_status": status, "context_issue": issue, "topic": topic,
                "scene": scene, "score": {}, "score_total": None}
    text = (post["text"] + " " + context.get("browser_context", "")).lower()
    practiced = [s for s in skills if s["level"] == "practiced" and s["confirmed"] and s["evidence"].strip() and s["name"].lower() in text]
    manually_supported = bool(review and review["contribution"].strip() and review["evidence"].strip())
    learning = [s for s in skills if s["level"] in ("learning", "want") and s["name"].lower() in text]
    published = context.get("published_at")
    age = int(time.time()) - published if isinstance(published, int) and published > 0 else None
    if age is not None and age < -300: age = None
    if age is not None: age = max(0, age)
    score = {
        "topic": {"value": 30 if topic != "other" else 0, "reason": "属于账号的开发/旅行主题" if topic != "other" else "尚未识别为账号主题"},
        "contribution": {"value": 30 if manually_supported else 15 if practiced else 0, "reason": "人工记录了拟补充内容与出处" if manually_supported else "有已确认的相关实践：" + "、".join(s["name"] for s in practiced[:3]) + "；具体可补充内容待确认" if practiced else "未找到已确认且有项目出处的相关实践，暂不声称能提供经验"},
        "discussion": {"value": 20 if scene in ("direct", "question") else 10, "reason": "直接提问或互动，可具体回应" if scene in ("direct", "question") else "可交流观点，但尚无明确问题"},
        "learning": {"value": 10 if learning else 0, "reason": "与正在学/想学的方向相关：" + "、".join(s["name"] for s in learning[:3]) if learning else "未关联已登记的学习方向"},
        "timing": {"value": 10 if age is not None and age <= 86400 else 5 if age is not None and age <= 259200 else 0, "reason": "24 小时内" if age is not None and age <= 86400 else "72 小时内" if age is not None and age <= 259200 else "发布时间较久" if age is not None else "发布时间未知"},
    }
    # Unknown publish time must not quietly become a zero in an apparent total.
    # A practiced skill on an unrelated topic cannot establish that this
    # particular conversation has something useful we can add.
    evidence_known = manually_supported or bool(practiced)
    total = sum(item["value"] for item in score.values()) if age is not None and evidence_known else None
    scenario = "已有读者的直接互动" if scene == "direct" else "同阶段互助" if learning else "经验交流" if practiced else "一般讨论"
    return {"context_status": status, "context_issue": issue, "topic": topic,
            "scene": scene, "scenario": scenario, "score": score, "score_total": total}


def enrich_rows(store, rows):
    skills = store.rows("SELECT * FROM interaction_skills ORDER BY id")
    ids = [row["id"] for row in rows]
    review_map = {}
    draft_map = {}
    if ids:
        marks = ",".join("?" for _ in ids)
        review_map = {r["post_id"]: r for r in store.rows(
            f"SELECT * FROM interaction_reviews WHERE post_id IN ({marks})", ids)}
        for draft in store.rows(f"SELECT * FROM drafts WHERE post_id IN ({marks}) AND status='draft' ORDER BY updated DESC", ids):
            draft_map.setdefault(draft["post_id"], draft)
    for row in rows:
        row["context"] = json.loads(row["context"])
        row["draft"] = draft_map.get(row["id"])
        review = review_map.get(row["id"])
        computed = score_post(row, skills, review)
        if review:
            row["review"] = {**computed,
                "contribution": review["contribution"], "evidence": review["evidence"],
                "decision": review["decision"], "decision_reason": review["decision_reason"]}
        else:
            row["review"] = {**computed, "contribution": "", "evidence": "", "decision": "", "decision_reason": ""}
        parent = row["context"].get("parent_id")
        if parent and not row["context"].get("parent"):
            found = store.rows("SELECT text,author,url FROM posts WHERE id=?", (parent,))
            row["context"]["parent"] = found[0] if found else {"unavailable": True}
    return rows


def candidate_ids(store, limit=30):
    # Direct questions and corrections outrank passive timeline posts. The
    # collected set may be smaller than the target; never manufacture filler.
    now = int(time.time())
    rows = store.rows(
        "SELECT * FROM posts WHERE kind!='own' AND status='new' AND created>=? ORDER BY created DESC LIMIT 300",
        (now - 3 * 86400,))
    for row in rows:
        row["context"] = json.loads(row["context"])
        row["review"] = {"scene": scene_for(row, row["context"]),
                         "topic": topic_for(row["text"]),
                         "context_status": context_state(row["context"])[0]}
    rows.sort(key=lambda p: (
        0 if p["review"]["scene"] == "direct" and re.search(r"报错|故障|无法|出错|纠正|不对|bug|broken|error", p["text"], re.I) else
        1 if p["review"]["scene"] == "direct" else 2,
        0 if p["review"]["topic"] != "other" else 1,
        0 if p["review"]["context_status"] == "ready" else 1,
        -(p["context"].get("published_at") or p["created"]),
    ))
    return [p["id"] for p in rows[:limit]]


def reply_block(store, post_id, c=None):
    def rows(sql, args=()):
        return [dict(row) for row in c.execute(sql, args)] if c is not None else store.rows(sql, args)
    found = rows("SELECT * FROM posts WHERE id=? AND kind!='own'", (post_id,))
    if not found: return "post_missing"
    post = found[0]
    if post["status"] in ("ignored", "skipped", "handled"): return "risk_skipped"
    context = json.loads(post["context"])
    if context_state(context)[0] != "ready": return "context_incomplete"
    if rows("SELECT id FROM drafts WHERE post_id=?", (post_id,)):
        return "reply_exists"
    parent = context.get("parent") or {}
    continuing = bool(parent.get("author", "").lower() == str(store.get("x_username", "")).lower() and parent.get("text"))
    if continuing: return ""
    zone = ZoneInfo(store.get("timezone", "Asia/Shanghai"))
    now = datetime.now(zone)
    day_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if rows("SELECT count(*) AS n FROM drafts WHERE kind='reply' AND created>=?", (day_start,))[0]["n"] >= 20:
        return "reply_daily_limit"
    if rows("SELECT count(*) AS n FROM drafts d JOIN posts p ON p.id=d.post_id WHERE d.kind='reply' AND d.created>=? AND lower(p.author)=lower(?)", (day_start, post["author"]))[0]["n"] >= 2:
        return "reply_author_limit"
    return ""
