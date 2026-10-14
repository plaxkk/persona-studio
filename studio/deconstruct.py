"""Break a benchmark account's viral post down into something repeatable (06B-2).

§4.9.2 asks for the *general* logic of
https://github.com/JuneYaooo/social-account-doctor, not its 小红书 vocabulary.
The parts of that project that transfer, and their X equivalents, are:

    封面 A-E        -> X has no cover. What the scroller sees before deciding to
                       stop is the opening lines, so the opening is what gets
                       quoted verbatim and measured here.
    标题公式 1-10    -> the first sentence does a title's work on X.
    骨架 A/B/C       -> transfers as written: 场景+冲突+解决 / 人设+故事+产品 /
                       数据对比+结论.
    钩子 1-7 (前 3-5 秒) -> transfers as the opening sentence's hook type.
    六维拆解 (定位/选题/封面/标题/正文/节奏) 1-5 分 -> NOT reproduced. It scores
                       *me against the benchmark*, and there is only one side
                       here; the bands are also the reference's own 行业经验值,
                       so an X translation would manufacture a number we cannot
                       defend. What is measurable is this post against the
                       account's own other viral posts.
    「已验证公式」= 同一做法在 ≥2 条赢家里重复，且没有同样高频出现在 bottom
                    -> only the first half is reproducible. min_faves:500 means
                       every stored post is already a winner, so there is no
                       bottom to test against; every pattern here stays a 单条
                       观察 and the document says so instead of promoting it.
    P0/P1/P2        -> becomes 可复制 / 需自测 / 只记录不复制, because §4.8 says
                       不照搬对标账号的人设.

The split between halves is the whole design. The measured half is computed from
rows already in the database and is always produced. The interpreted half needs
a model, and when no model ran the document says so instead of filling in a
plausible-sounding analysis. Every interpretive claim must carry a quote that
appears in the stored text; a quote that cannot be found is dropped and the drop
is recorded, so a deconstruction can be audited back to the post it names.
"""

from __future__ import annotations

import json
import re
import statistics
import time

from .benchmarks import METRICS, deconstruction_id, posts

# A post keeps collecting likes for months, so a 3-day-old post and a 300-day-old
# post are not comparable even on the same account. Peers are kept within half
# this post's own lifetime (with a two-week floor so very young posts are not
# compared on a band of hours), and a comparison needs at least this many of them.
# The reference refuses to label anything a formula below 10 samples ("样本少于
# 10 条时只展示原始值，不给稳定公式标签"); the same rule applies here, and its
# number is lower because the cohort is already narrowed to one age band on one
# account, so 5 kept neighbours are a tighter set than 10 loose ones. Below this
# the document prints 比不了 rather than a median of almost nothing.
MIN_PEERS = 5
MIN_BAND_DAYS = 14.0

# The model may only speak about fragments it can quote from the stored text.
MAX_QUOTE_CHARS = 400
MAX_ITEM_CHARS = 400
MAX_ITEMS = 5

# A question or a number at the top is a fact about the text, not a claim about
# X's renderer, so these are measured rather than guessed.
_COUNT_CUE = re.compile(r"\d")
_COMPARE_CUE = re.compile(r"(?:比|从|到|提升|下降|增长|减少|翻|倍|涨|掉|以前|现在|之前|之后)")
_LIST_CUE = re.compile(r"(?:^|\n)\s*(?:\d+[.、)]|[-*·•])\s")
_LINK_CUE = re.compile(r"https?://\S+")


class DeconstructError(ValueError):
    """The post cannot be deconstructed as asked."""


def _num(value):
    """Render a count the way a person reads it. SQLite hands back REAL for an
    aggregate, and `25.0` reads as a different number than `25`."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _ratio(numerator, denominator):
    """A rate only exists when both of its numbers were published."""
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return None
    if isinstance(numerator, bool) or isinstance(denominator, bool):
        return None
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _sum(*values):
    """A total that is only a total when every part of it was published.

    `likes or 0` in a sum is how a withheld number turns into a real one.
    """
    if not all(isinstance(value, int) and not isinstance(value, bool)
               for value in values):
        return None
    return sum(values)


def age_days(post: dict) -> float | None:
    """Age at the moment the shown counts were read, not at read time today.

    The counts and the age have to describe the same instant: a snapshot taken
    a week ago is a week old, and dating it from now would inflate every rate.
    """
    posted = post.get("posted_at")
    captured = post.get("captured_at")
    if not posted or not captured:
        return None
    return round(max(0.0, (captured - posted) / 86400.0), 2)


def cohort(post: dict, peers: list[dict]) -> list[dict]:
    """The comparison set: same account, similar maturity, different post."""
    age = age_days(post)
    if age is None:
        return []
    band = max(MIN_BAND_DAYS, age * 0.5)
    picked = []
    for peer in peers:
        if peer.get("post_id") == post.get("post_id"):
            continue
        peer_age = age_days(peer)
        if peer_age is None:
            continue
        if abs(peer_age - age) <= band:
            picked.append(peer)
    return picked


def opening(text: str) -> dict:
    """What the scroller sees first, quoted rather than described."""
    body = (text or "").strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    first_line = lines[0] if lines else ""
    first_sentence = re.split(r"(?<=[。！？!?])", first_line)[0] if first_line else ""
    return {
        "first_line": first_line[:280],
        "first_sentence": first_sentence[:280],
        "first_line_chars": len(first_line),
        "body_chars": len(body),
        "line_count": len(lines),
        "starts_with_number": bool(_COUNT_CUE.match(first_line[:12])),
        "is_question": first_line.rstrip().endswith(("?", "？")),
        "has_comparison_cue": bool(_COMPARE_CUE.search(first_line)),
        "has_list": bool(_LIST_CUE.search(body)),
        "has_link": bool(_LINK_CUE.search(body)),
        "ends_with_question": body.rstrip().endswith(("?", "？")),
    }


def features(post: dict, peers: list[dict]) -> dict:
    """Everything that can be read off the stored rows without a model."""
    metrics = post.get("metrics_json") or {}
    unknown = [name for name in post.get("unknown_metrics") or [] if name in METRICS]
    views = metrics.get("views")
    likes = metrics.get("likes")
    group = cohort(post, peers)
    like_values = [p["metrics_json"]["likes"] for p in group
                   if isinstance((p.get("metrics_json") or {}).get("likes"), int)]
    median_likes = statistics.median(like_values) if len(like_values) >= MIN_PEERS else None
    return {
        "age_days": age_days(post),
        "text_source": post.get("text_source") or "",
        "kind": post.get("kind") or "",
        "metrics": {name: _num(metrics.get(name)) for name in METRICS},
        "unknown_metrics": unknown,
        "rates": {
            # Each rate is named for what it separates. 收藏 counts the reader
            # who intends to come back, 转发 the one who carries it elsewhere,
            # 讨论 the one who has something to say -- all against a like, which
            # is the cheapest of the four and so the natural denominator.
            "bookmark_per_like": _ratio(metrics.get("bookmarks"), likes),
            "repost_per_like": _ratio(metrics.get("retweets"), likes),
            "discussion_per_like": _ratio(
                _sum(metrics.get("replies"), metrics.get("quotes")), likes),
            "views_per_like": _ratio(views, likes),
        },
        "peers": len(group),
        "median_peer_likes": _num(median_likes),
        # A ratio against the account's own median is only reported with enough
        # peers behind it; `peers` travels with it so the panel can say why not.
        "times_median_likes": (
            round(likes / median_likes, 2)
            if median_likes and isinstance(likes, int) and median_likes > 0 else None),
        "opening": opening(post.get("text") or ""),
    }


def _fmt(value, suffix: str = "") -> str:
    """`看不到` for a number X did not publish; never a zero standing in for it."""
    if value is None:
        return "看不到"
    return f"{value:,}{suffix}"


def _pct(value) -> str:
    return "看不到" if value is None else f"{value * 100:.2f}%"


def _times(value) -> str:
    return "看不到" if value is None else f"{value:.2f}"


PROMPT = """请拆解下面这条推文为什么能传播，并只返回一个 JSON 对象，不要解释、不要代码块之外的内容。

JSON 字段：
{
  "thesis": "一句话说明这条推文能传播的核心机制，不超过 120 字",
  "hook": "首句钩子类型，从这些里选一个并补一句判断：提问/反差/数字/承诺/悬念前置/身份锁定/利他诱饵/无钩子",
  "skeleton": "正文骨架，从这些里选一个：场景+冲突+解决 / 人设+故事+产品 / 数据对比+结论 / 其他",
  "evidence": [{"claim": "你的一个判断", "quote": "推文原文中支持这个判断的原句"}],
  "copyable": ["我现在就能照做的具体动作，不超过 5 条"],
  "needs_test": ["看起来有用但我这条账号没验证过的做法，不超过 5 条"],
  "not_transferable": ["依赖对方人设、资源或领域，我照搬会不像我自己的做法，不超过 5 条"],
  "unknowns": ["这条推文的数据里你看不到、因此判断不了的东西"]
}

硬性要求：
1. evidence 里每条的 quote 必须是推文原文里**逐字存在**的一段，不要改写、不要翻译、不要拼接。
2. 判断必须落在这条推文的原文和给出的数字上。看不出原因就写进 unknowns，不要猜。
3. 给的数字里标着「看不到」的项，就是你不知道的项，不要当成 0，也不要据它推断。
4. copyable 只写动作，不要写「要写得更吸引人」这类没法执行的话。
5. 只输出 JSON。"""


def request_text(post: dict, feats: dict) -> str:
    """Only what is stored, and the withheld numbers named as withheld."""
    lines = [PROMPT, "", "推文原文（正文来源：" +
             ("note_tweet 全文" if feats["text_source"] == "note_tweet" else "页面可见正文")
             + "）：", post.get("text") or "", "", "这条推文已读到的数字："]
    lines.append(f"发布账号：@{post.get('handle', '')}｜类型：{feats['kind']}｜"
                 f"读到时已发布 {feats['age_days']} 天")
    for name, label in (("views", "浏览"), ("likes", "点赞"), ("replies", "回复"),
                        ("retweets", "转推"), ("quotes", "引用"), ("bookmarks", "收藏")):
        lines.append(f"- {label}：{_fmt(feats['metrics'][name])}")
    if feats["unknown_metrics"]:
        lines.append("- 上面标「看不到」的项是 X 没有给出这个数字，不是 0。")
    if feats["median_peer_likes"] is not None:
        lines.append(f"- 同一账号同年龄段（{feats['peers']} 条）的点赞中位数："
                     f"{feats['median_peer_likes']:,}，本条是它的 {feats['times_median_likes']} 倍。")
    else:
        lines.append("- 同年龄段的对比样本不足，本条没有和这个账号的其他爆款做倍数比较。")
    lines.append("")
    lines.append("注意：这条推文是搜索结果里 min_faves:500 筛出来的，所以你能看到的样本"
                 "全都是爆款，没有「没爆的推文」作对照。不要写出「因为它做到了 X 所以"
                 "它爆了」这种没有反例支撑的因果。")
    return "\n".join(lines)


def parse_model(text: str, source: str) -> tuple[dict, list[str]]:
    """Validate the model's JSON. Returns the usable part and what was dropped.

    A quote that does not literally appear in the post is thrown away rather
    than trusted: it is the one part of an interpretive claim that can be
    checked, so a claim that fails the check does not get to look measured.
    """
    notes: list[str] = []
    raw = (text or "").strip()
    for fence in ("```json", "```"):
        raw = raw.removeprefix(fence)
    raw = raw.removesuffix("```").strip()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}, ["模型没有返回可解析的 JSON，本次只有实测部分。"]
    if not isinstance(data, dict):
        return {}, ["模型返回的不是 JSON 对象，本次只有实测部分。"]

    def text_of(name: str) -> str:
        value = data.get(name)
        return value.strip()[:MAX_ITEM_CHARS] if isinstance(value, str) else ""

    def items_of(name: str) -> list[str]:
        value = data.get(name)
        if not isinstance(value, list):
            return []
        return [item.strip()[:MAX_ITEM_CHARS] for item in value
                if isinstance(item, str) and item.strip()][:MAX_ITEMS]

    kept: list[dict] = []
    dropped = 0
    for entry in data.get("evidence") or []:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        claim = str(entry.get("claim") or "").strip()[:MAX_ITEM_CHARS]
        quote = str(entry.get("quote") or "").strip()
        if not claim or not quote:
            dropped += 1
            continue
        if quote[:MAX_QUOTE_CHARS] not in source:
            dropped += 1
            continue
        kept.append({"claim": claim, "quote": quote[:MAX_QUOTE_CHARS]})
        if len(kept) >= MAX_ITEMS:
            break
    if dropped:
        notes.append(f"模型给出的 {dropped} 处引文在原文中找不到，已丢弃；"
                     "剩下的判断都能对应到原文。")
    return {
        "thesis": text_of("thesis"),
        "hook": text_of("hook"),
        "skeleton": text_of("skeleton"),
        "evidence": kept,
        "copyable": items_of("copyable"),
        "needs_test": items_of("needs_test"),
        "not_transferable": items_of("not_transferable"),
        "unknowns": items_of("unknowns"),
    }, notes


def _bullets(items: list[str], empty: str) -> str:
    if not items:
        return f"- {empty}"
    return "\n".join(f"- {item}" for item in items)


def render_doc(post: dict, feats: dict, model: dict, notes: list[str],
               *, engine: str = "", failure: str = "") -> str:
    """The deconstruction document. Measured and interpreted never blur."""
    opening_info = feats["opening"]
    parts = [
        f"# 爆款拆解 · @{post.get('handle', '')} 的 {post.get('post_id', '')}",
        "",
        f"- 帖子：[{post.get('url', '')}]({post.get('url', '')})",
        f"- 读取时间：{post.get('captured_at', 0)}（读到上面数字的时刻）｜"
        f"读到时已发布 {feats['age_days']} 天",
        f"- 正文来源：{'note_tweet 全文' if feats['text_source'] == 'note_tweet' else '页面可见正文'}"
        + ("（这条推文的长文正文取自 note_tweet；页面正文可能有截断）"
           if feats["text_source"] == "note_tweet" else ""),
        "",
        "## 原文（逐字引用）",
        "",
        (post.get("text") or "").strip(),
        "",
        "## 实测：这条推文自己",
        "",
        "| 指标 | 数值 |", "| --- | --- |",
    ]
    for name, label in (("views", "浏览"), ("likes", "点赞"), ("replies", "回复"),
                        ("retweets", "转推"), ("quotes", "引用"), ("bookmarks", "收藏")):
        parts.append(f"| {label} | {_fmt(feats['metrics'][name])} |")
    parts += [
        "",
        f"- 收藏 / 点赞：{_pct(feats['rates']['bookmark_per_like'])}"
        "（存下来以后再看的比例）",
        f"- 转推 / 点赞：{_pct(feats['rates']['repost_per_like'])}"
        "（愿意带出去给别人看的比例）",
        f"- 讨论 / 点赞：{_pct(feats['rates']['discussion_per_like'])}"
        "（回复 + 引用，别人要开口说的比例）",
        f"- 浏览 / 点赞：{_times(feats['rates']['views_per_like'])}"
        "（多少人看过才有一个赞）",
        "",
        "## 实测：这条推文相对这个账号的其他爆款",
        "",
    ]
    if feats["median_peer_likes"] is not None:
        parts.append(
            f"- 同年龄段（{feats['peers']} 条）的点赞中位数是 "
            f"{feats['median_peer_likes']:,}，本条是它的 **{feats['times_median_likes']} 倍**。")
        parts.append(
            "- 这是**单条观察**，不是公式：参考项目要求「同一做法至少在 2 条赢家里重复，"
            "且没有同样高频出现在 bottom 里」才算已验证，而这一层没有 bottom。")
    else:
        parts.append(
            f"- 同年龄段的对比样本只有 {feats['peers']} 条，不足 {MIN_PEERS} 条，"
            "所以**没有**做倍数比较。这不是「和平均一样」，是**比不了**。")
        parts.append("- 为什么按年龄分组：帖子发布后点赞会持续累加，一条 3 天的帖子和一条"
                     " 300 天的帖子放在一起比，比的是时间不是内容。")
    parts += [
        "",
        f"**这条不提供的东西**：这个账号没有「没爆的推文」样本（搜索用 `min_faves:` "
        f"筛过），所以这里**没有**任何一个账号内的非爆款基线，任何「因为它做到了 X "
        f"所以它爆了」的说法在这里都没有反例支撑。",
        "",
        "## 实测：开头（读者决定停不停的那一段）",
        "",
        "```",
        opening_info["first_line"] or "（没有正文）",
        "```",
        f"- 首句：{opening_info['first_sentence'] or '（无）'}",
        f"- 首行 {opening_info['first_line_chars']} 字；全文 {opening_info['body_chars']} 字、"
        f"{opening_info['line_count']} 段",
        f"- 首行以数字开头：{'是' if opening_info['starts_with_number'] else '否'}｜"
        f"首行是问句：{'是' if opening_info['is_question'] else '否'}｜"
        f"首行含对比说法：{'是' if opening_info['has_comparison_cue'] else '否'}",
        f"- 正文含列表：{'是' if opening_info['has_list'] else '否'}｜"
        f"含链接：{'是' if opening_info['has_link'] else '否'}｜"
        f"结尾是问句：{'是' if opening_info['ends_with_question'] else '否'}",
        "",
    ]
    marker = "以上都是从已存字段直接算出来的，不是判断。"
    if feats["unknown_metrics"]:
        names = {"views": "浏览", "likes": "点赞", "replies": "回复",
                 "retweets": "转推", "quotes": "引用", "bookmarks": "收藏"}
        missing = "、".join(names.get(name, name) for name in feats["unknown_metrics"])
        marker += f"其中 {missing} X 没有给出数字，记作「看不到」，不是 0。"
    parts.append(marker)

    parts += ["", "## 判断：这条推文为什么能传播（由模型阅读后给出）", ""]
    if not model.get("thesis") and not model.get("evidence"):
        parts.append("**本次没有生成判断部分。**" + (
            "没有配置可用的生成引擎。" if not engine else
            f"{failure}。" if failure else
            "模型没有返回可用内容。"))
        # The reason travels with the document in both branches: "no engine" and
        # "the engine answered with prose" are different problems to fix.
        parts += [f"- {line}" for line in notes]
        parts.append("上面实测的部分不受影响；这里不补写任何看起来像分析的内容。")
    else:
        parts += [
            f"**核心机制**：{model.get('thesis') or '模型没有给出'}", "",
            f"**首句钩子**：{model.get('hook') or '模型没有给出'}", "",
            f"**正文骨架**：{model.get('skeleton') or '模型没有给出'}",
            "",
        ]
        if model.get("evidence"):
            parts.append("**判断依据（每条都附原文引文）**")
            parts.append("")
            for entry in model["evidence"]:
                parts.append(f"- {entry['claim']}")
                parts.append(f"\t- 原文：“{entry['quote']}”")
            parts.append("")
        else:
            parts.append("模型没有给出能对上原文的依据，所以这一版没有依据列表。")
            parts.append("")
        for notes_line in notes:
            parts.append(f"- {notes_line}")
        parts.append("")

    parts += ["## 照做（分三类，不是一份待办清单）", ""]
    if not model:
        parts.append("本次没有生成动作建议。")
    else:
        parts.append("**P0 · 现在就能照做的动作**")
        parts.append(_bullets(model.get("copyable") or [], "模型没有给出可直接照做的动作。"))
        parts.append("")
        parts.append("**P1 · 看起来有用，但我这条账号没验证过**")
        parts.append(_bullets(model.get("needs_test") or [], "模型没有给出需要自测的做法。"))
        parts.append("")
        parts.append("**P2 · 只记录，不照搬**")
        parts.append(_bullets(model.get("not_transferable") or [],
                             "模型没有指出不适用于我的做法。"))
        parts.append("")
        parts.append("§4.8 要求不照搬对标账号的人设，所以依赖对方人设和资源的做法"
                     "放在 P2：记下来知道，不改成自己发。")
    parts += ["", "## 这份拆解不知道什么", ""]
    unknown_lines = list(model.get("unknowns") or []) if model else []
    for name in feats["unknown_metrics"]:
        label = {"views": "浏览", "likes": "点赞", "replies": "回复",
                 "retweets": "转推", "quotes": "引用", "bookmarks": "收藏"}.get(name, name)
        unknown_lines.append(f"{label} X 没有给出数字，记作「看不到」，不能据此推断。")
    unknown_lines.append("没有这个账号非爆款推文的样本，无法回答「为什么这条比没爆的那些好」。")
    if feats["age_days"] is not None and feats["age_days"] < 30:
        unknown_lines.append(
            f"读到时这条只发布了 {feats['age_days']} 天，数字还在涨，现在的高低不是终值。")
    else:
        unknown_lines.append("数字是读取那一刻的快照，之后还会变；重新读取会重算这份拆解。")
    if not model.get("thesis") and not model.get("evidence"):
        unknown_lines.append(
            "没有配置生成引擎，所以没有「为什么能传播」的判断。" if not engine
            else "引擎没有给出可用的判断，所以这一版没有「为什么能传播」的结论。")
    parts += [f"- {line}" for line in dict.fromkeys(unknown_lines)]
    parts += ["", f"生成引擎：{engine or '未使用'}｜拆解按帖子保存，重新拆解会覆盖为新的一版。"]
    return "\n".join(parts) + "\n"


def row(store, post_id: str) -> dict | None:
    """The post as the panel sees it, so both read the same decoded fields."""
    found = store.rows("SELECT handle FROM benchmark_posts WHERE post_id=?", (post_id,))
    if not found:
        return None
    for candidate in posts(store, found[0]["handle"]):
        if candidate["post_id"] == post_id:
            return candidate
    return None


def measure(store, post_id: str) -> tuple[dict, dict]:
    """The stored post and everything measurable about it."""
    post = row(store, post_id)
    if post is None:
        raise DeconstructError("post_missing")
    peers = []
    for peer in store.rows(
            "SELECT post_id,metrics_json,captured_at,posted_at FROM benchmark_posts "
            "WHERE handle=?", (post["handle"],)):
        try:
            peer["metrics_json"] = json.loads(peer["metrics_json"])
        except (TypeError, ValueError):
            peer["metrics_json"] = {}
        # A peer whose stored JSON is unreadable is not a peer with zero likes.
        if isinstance(peer["metrics_json"], dict):
            peers.append(peer)
    return post, features(post, peers)


def deconstruct(store, post_id: str, *, model: dict | None = None,
                engine: str = "", raw: str = "", failure: str = "",
                measured=None) -> dict:
    """Build and store one deconstruction. `model`/`raw` come from the engine.

    `measured` lets the caller hand over the features it already showed the
    engine, so the numbers the model reasoned about and the numbers in the
    stored document cannot drift apart if a read refreshes the counts in
    between.
    """
    if measured is None:
        post, feats = measure(store, post_id)
    else:
        post, feats = measured
    notes: list[str] = []
    if model is None:
        model, notes = parse_model(raw, post.get("text") or "")
    doc = render_doc(post, feats, model, notes, engine=engine, failure=failure)
    evidence = "\n".join(
        f"{entry['claim']} <= {entry['quote']}" for entry in model.get("evidence") or [])
    stored = {
        "ok": True,
        "post_id": post_id,
        "handle": post["handle"],
        "thesis": model.get("thesis") or "",
        "hook": model.get("hook") or "",
        "engine": engine,
        "features": feats,
        "notes": notes,
        "doc": doc,
    }
    with store.db(True) as c:
        c.execute(
            """
            -- The conflict target is `id`, which is a hash of the post id, so
            -- "one row per post" follows from the key rather than from a second
            -- constraint: a re-run refreshes the counts that moved instead of
            -- leaving a stale twin beside it.
            INSERT INTO viral_deconstructions(
                id,post_id,handle,thesis,hook,structure_json,evidence,
                design_doc,engine,created)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                handle=excluded.handle,thesis=excluded.thesis,hook=excluded.hook,
                structure_json=excluded.structure_json,evidence=excluded.evidence,
                design_doc=excluded.design_doc,engine=excluded.engine,
                created=excluded.created
            """,
            (deconstruction_id(post_id), post_id, post["handle"],
             stored["thesis"], stored["hook"],
             json.dumps({"measured": feats, "model": model}, ensure_ascii=False,
                        sort_keys=True),
             evidence, doc, engine, int(time.time())),
        )
    return stored


def list_for(store, handle: str) -> list[dict]:
    rows = store.rows(
        "SELECT id,post_id,handle,thesis,hook,engine,created,design_doc "
        "FROM viral_deconstructions WHERE handle=? ORDER BY created DESC",
        (handle.strip().lstrip("@"),))
    for found in rows:
        # The panel shows the doc; the list view only needs to know it exists.
        found["has_doc"] = bool(found.pop("design_doc"))
    return rows
