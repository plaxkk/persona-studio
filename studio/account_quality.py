"""账号质量采集与特征（06C-1）。

Notion《账号质量打标程序化方案》把整条流水线拆成
**采集层 → 特征层 → 规则层 → 模型层 → 标签层**，本模块只做前两层。

这一片的产出是一个问题的一个答案：**测得到吗**。所以这里刻意不产出任何分数、
不产出任何权重、也不把几个量合成一个"活人感"数字——合成属于 06C-2 的规则层，
而规则的输入必须是这里逐条记下的观测量。凡是这一条通道读不到的东西，特征行会带
着 `known=0` 和一句**为什么读不到**（`note`）落库，绝不写成 0：写 0 会让
"这个账号没有空壳互动者"和"我们看不到互动者的粉丝数"看起来一模一样。

四条读取（`READ_SET`）覆盖三个层面的信号：

- `profile` 主页头图区（DOM）→ A 账号静态特征：粉丝/关注比、账号年龄、完整度；
- `posts` 该账号近 90 天的原帖（搜索，**不带 min_faves**）→ B 内容与互动统计。
  06B 只读 ≥500 赞的爆款，那些数据用于账号质量是有偏的：一个账号的常态互动率
  不能靠只留下最火的那几条来算；
- `received` 别人回复给它的内容 → C 互动文本与互动者质量（方案里说这层
  "最有区分度"）：模板回复占比、意向评论密度、互动者账号画像；
- `replies` 它回复别人的内容 → 自己回复的平均质量（§4.11 也要求看"对牛弹琴"）。

明确读不到、并因此**永久带 `known=0`** 的三件事写在 `UNMEASURABLE` 里：互动时间
突发分数、互动者共现率、自己的回复是否接得上原文。前两个是第三方根本拿不到的
数据，第三个需要帖子详情页原文——不是本通道的缺陷，是这一层的边界，写出来才
不会在 06C-2 被当成"还没算"。
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import time
from datetime import date, timedelta

from . import account_flags, account_scoring, benchmarks

SAMPLE_WINDOW_DAYS = 90
# X serves 20 results per page and 06B caps one read at 1000; a quality read is
# not a census, so it stops far earlier and says so through `coverage_gap`.
SAMPLE_LIMIT = 60
# 方案要求"抓到的回复样本 < 30，就标 low_confidence"。原帖样本的门槛方案没写，
# 这里取 10：少于 10 条时中位数没有意义，连"发帖频率"都会被一条置顶帖带偏。
MIN_POST_SAMPLE = 10
MIN_REPLY_SAMPLE = 30
# 人工复核的抽样（方案：高互动 8 条 / 普通 8 条）。程序化特征用读到的全部样本，
# 这一组只标出"值得人去看"的那几条，供 06C-3 的人工复核用。
REVIEW_HIGH_ENGAGEMENT = 8
REVIEW_ORDINARY = 8
# 方案 §C 把"空简介、默认头像、发帖极少、注册很新"统称空壳互动者，并列进最小特征集的
# 第 5 项。前两个信号 X 一定给，后两个随 author_stats 时有时无，所以门槛也分开：
# **头像或简介缺**单独就够说明壳，而发帖少、注册新要**同时**成立才算行为像壳——
# 只有一条时它更可能是「新用户」而不是「壳」。这两个数是本机定的。
SHELL_STATUSES_MAX = 5
SHELL_AGE_DAYS = 180

PROFILE = "profile"
POSTS = "posts"
RECEIVED = "received"
REPLIES = "replies"
READ_SET = (PROFILE, POSTS, RECEIVED, REPLIES)
SAMPLE_KIND = {PROFILE: "profile", POSTS: "post", RECEIVED: "received", REPLIES: "reply"}
READ_LABEL = {PROFILE: "主页", POSTS: "它的原帖", RECEIVED: "收到的回复",
              REPLIES: "它回复别人"}


class AccountQualityError(ValueError):
    """The account or the request cannot be turned into a sampling plan."""


def clean_handle(handle: str) -> str:
    clean = str(handle or "").strip().lstrip("@").lower()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", clean):
        raise AccountQualityError("handle_missing")
    return clean


def window(store, *, days: int = SAMPLE_WINDOW_DAYS) -> tuple[str, str]:
    today = benchmarks.today(store)
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def _query(handle: str, *, since: str, until: str, kind: str) -> str:
    if not since or not until:
        raise AccountQualityError("window_missing")
    if kind == POSTS:
        body = f"from:{handle} -filter:replies -filter:retweets"
    elif kind == RECEIVED:
        body = f"to:{handle} -from:{handle}"
    elif kind == REPLIES:
        body = f"from:{handle} filter:replies -filter:retweets"
    else:
        raise AccountQualityError("kind_missing")
    return f"{body} since:{since} until:{until}"


def plan(store, handle: str) -> dict:
    """The whole read plan for one account, built in one place.

    The job and the route both need it, and a route that promised one window
    while the job read another is a difference nobody notices until two numbers
    disagree.
    """
    clean = clean_handle(handle)
    since, until = window(store)
    return {
        "handle": clean,
        "since": since,
        "until": until,
        "reads": {kind: _query(clean, since=since, until=until, kind=kind)
                  for kind in READ_SET if kind != PROFILE},
    }


def sample_kind(kind: str) -> str:
    return SAMPLE_KIND.get(kind, "")


# --- 采集层 ---------------------------------------------------------------

def store_samples(store, posts: list[dict], *, handle: str, kind: str,
                  gap_text: str = "") -> int:
    """Upsert one read's samples, keeping what an earlier read already knew.

    Counts legitimately grow between reads, so they are refreshed; `text` is not
    — a later read can come back with a truncated body or an empty stub, and
    overwriting a full post with it would lose the only copy.
    """
    kind = sample_kind(kind)
    if not kind:
        raise AccountQualityError("kind_missing")
    written = 0
    with store.db(True) as c:
        for post in posts:
            sample_id = str(post.get("post_id") or "")
            if not sample_id:
                continue
            extra = {
                "links": int(post.get("links") or 0),
                "has_media": bool(post.get("has_media")),
                "is_reply": bool(post.get("is_reply")),
                "reply_to": str(post.get("reply_to") or "")[:15],
                "author_stats": post.get("author_stats") or {},
            }
            c.execute(
                """
                INSERT INTO account_samples(
                    handle,sample_kind,sample_id,author,text,text_source,url,
                    posted_at,metrics_json,engagement_json,extra_json,
                    captured_at,source,coverage_gap)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(handle,sample_kind,sample_id) DO UPDATE SET
                    author=excluded.author,
                    text=CASE WHEN length(excluded.text)>length(account_samples.text)
                              THEN excluded.text ELSE account_samples.text END,
                    text_source=CASE WHEN length(excluded.text)>length(account_samples.text)
                              THEN excluded.text_source ELSE account_samples.text_source END,
                    url=excluded.url,
                    posted_at=COALESCE(excluded.posted_at,account_samples.posted_at),
                    metrics_json=excluded.metrics_json,
                    engagement_json=excluded.engagement_json,
                    extra_json=excluded.extra_json,
                    captured_at=excluded.captured_at,
                    source=excluded.source,coverage_gap=excluded.coverage_gap
                """,
                (
                    handle, kind, sample_id, post.get("author", ""),
                    post.get("text", ""), post.get("text_source", ""),
                    post.get("url", ""), post.get("posted_at"),
                    json.dumps(post.get("metrics", {}), sort_keys=True),
                    json.dumps({"unknown": post.get("unknown_metrics", [])},
                               sort_keys=True),
                    json.dumps(extra, sort_keys=True),
                    int(post.get("captured_at") or time.time()),
                    str(post.get("source") or "search"), gap_text,
                ),
            )
            written += 1
    return written


def store_profile(store, handle: str, record: dict, *, observed_at: int,
                  gap_text: str = "") -> None:
    """Keep the one profile sample per account, replacing the previous one.

    Unlike a post, a profile reading is a point-in-time statement about the
    account, and last month's bio is not evidence about this month's account.
    What replaces it is only what was actually read: a field the panel could not
    see this time is written as absent, not as the old value.
    """
    clean = clean_handle(handle)
    with store.db(True) as c:
        c.execute(
            """
            INSERT INTO account_samples(
                handle,sample_kind,sample_id,author,text,text_source,url,
                posted_at,metrics_json,engagement_json,extra_json,
                captured_at,source,coverage_gap)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(handle,sample_kind,sample_id) DO UPDATE SET
                author=excluded.author,text=excluded.text,
                metrics_json=excluded.metrics_json,
                engagement_json=excluded.engagement_json,
                extra_json=excluded.extra_json,
                captured_at=excluded.captured_at,source=excluded.source,
                coverage_gap=excluded.coverage_gap
            """,
            (clean, "profile", clean, clean,
             str(record.get("bio") or "")[:3000], "dom",
             f"https://x.com/{clean}", None,
             json.dumps({"followers": record.get("followers"),
                         "following": record.get("following")}, sort_keys=True),
             json.dumps({}, sort_keys=True),
             json.dumps(record, sort_keys=True, ensure_ascii=False),
             int(observed_at), "profile", gap_text),
        )


def samples(store, handle: str, kind: str) -> list[dict]:
    rows = store.rows(
        "SELECT * FROM account_samples WHERE handle=? AND sample_kind=? "
        "ORDER BY posted_at IS NULL, posted_at DESC", (clean_handle(handle), kind))
    for row in rows:
        for field in ("metrics_json", "engagement_json", "extra_json"):
            try:
                row[field] = json.loads(row[field])
            except (TypeError, ValueError):
                row[field] = {}
        row["unknown_metrics"] = [name for name
                                  in row["engagement_json"].get("unknown", [])
                                  if name in benchmarks.METRICS]
    return rows


def read_states(store, task_id: str) -> dict:
    """What each of the four reads actually did, read back from the queue."""
    states = {}
    for row in store.rows(
            "SELECT kind,status,result FROM account_reads WHERE task_id=?",
            (task_id,)):
        try:
            data = json.loads(row["result"])
        except (TypeError, ValueError):
            data = {}
        states[row["kind"]] = {
            "status": row["status"], "state": str(data.get("state") or ""),
            "complete": bool(data.get("complete")),
            "coverage_gap": str(data.get("coverage_gap") or ""),
        }
    return states


# --- 特征层 ---------------------------------------------------------------

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
MENTION_RE = re.compile(r"^(\s*@[A-Za-z0-9_]{1,15}[\s,:，：]*)+")
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF←-➿⬀-⯿️‍™©®]")
TRAILING_RE = re.compile(r"[！!。.~～,，、;；:：…·\-—\s]+$")

# 方案说模板词表"会演化"，要每周人工复核；所以这里给的是一张**可审计的固定表**，
# 加上一条结构性的弱代理，两条各自成列，不合并成一个"模板度"。
CLICHE_RE = re.compile(
    r"^(?:好|赞|太棒了?|学到了|学习了|感谢分享|谢谢分享|谢谢|支持|牛|牛逼|厉害|顶|"
    r"收藏了|不错|真好|来了|哈哈+|呵呵|沙发|路过|打卡|已阅|互关|互赞|关注了|前排|"
    r"占位|yyds|\+1|6+|first|make sense|agreed?|nice|cool|great)$", re.I)
INTENT_RE = re.compile(
    r"怎么买|哪里买|在哪买|多少钱|价格|收费|订阅|求链接|求教程|怎么用|怎么弄|怎么开|"
    r"能教|带带我|带带|私信|合作|报价|求带|如何|教教|"
    r"how much|price|buy|dm ?me|\blink\b|tutorial", re.I)


def normalize_reply(text: str) -> str:
    """Strip the parts of a reply that carry no opinion of their own.

    A reply that is only an @ and a link is not a short reply, it is a delivery
    vehicle; keeping those tokens would read as content.
    """
    body = URL_RE.sub(" ", str(text or ""))
    body = MENTION_RE.sub("", body)
    return re.sub(r"\s+", " ", body).strip()


def _is_emoji_only(text: str) -> bool:
    stripped = EMOJI_RE.sub("", text)
    return bool(text) and not re.sub(r"[\W_]+", "", stripped, flags=re.UNICODE)


def _is_cliche(text: str) -> bool:
    core = TRAILING_RE.sub("", normalize_reply(text))
    if not core:
        return True
    return bool(CLICHE_RE.match(core))


def _is_short_generic(text: str) -> bool:
    core = TRAILING_RE.sub("", normalize_reply(text))
    if not core:
        return False
    return (len(core) <= 6 and not re.search(r"[?？#\d]", core)
            and not _is_emoji_only(core))


def tokens(text: str) -> list[str]:
    """ASCII words plus single CJK characters.

    The studio's posts mix the two, and a whitespace split turns a Chinese
    sentence into one token that matches nothing.
    """
    return re.findall(r"[A-Za-z0-9_]+|[一-鿿]", str(text or "").lower())


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def _ratio(numerator: float, denominator: float) -> float | None:
    if not isinstance(denominator, (int, float)) or denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def joined_date(text: str) -> tuple[int, int] | None:
    """Read the join date X shows. It is month-precision at best.

    X renders "Joined December 2011" (or 加入于 2011年12月) and nothing finer, so
    the caller records the imprecision instead of pretending to a day.
    """
    body = str(text or "")
    year = re.search(r"(\d{4})\s*年?", body)
    if not year:
        return None
    value = int(year.group(1))
    if not 2000 <= value <= date.today().year:
        return None
    month = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", body)
    if month:
        number = int(month.group(2))
        return (value, number) if 1 <= number <= 12 else (value, 1)
    names = ("january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december")
    for index, name in enumerate(names, start=1):
        if re.search(name, body, re.I):
            return value, index
    short = re.search(r"\b([A-Za-z]{3})\b", body)
    if short and short.group(1).lower() in [name[:3] for name in names]:
        return value, [name[:3] for name in names].index(short.group(1).lower()) + 1
    return value, 1


def _engagement(metrics: dict) -> int:
    return sum(int(metrics.get(name) or 0)
               for name in ("likes", "replies", "retweets", "quotes"))


def _author_stats_known(stats: dict) -> bool:
    return any(isinstance(stats.get(name), int)
               for name in ("followers", "following", "statuses", "created_at"))


def review_set(posts: list[dict]) -> dict:
    """The 高互动 8 / 普通 8 / 带链接全部 selection the plan asks a human to look at.

    Deterministic: the highest-engagement rows first, then an even spread of the
    rest. A random pick would make two people reviewing the same account see
    different rows and call the difference a disagreement.
    """
    # `samples()` hands these rows straight over from `account_samples`, where
    # the parsed metrics live under `metrics_json`; reading any other key ranks
    # every row zero and turns "the most engaging posts" into "the first eight
    # rows in the table", which the panel then labels 高互动.
    ranked = sorted(posts, key=lambda post: -_engagement(post.get("metrics_json") or {}))
    high = ranked[:REVIEW_HIGH_ENGAGEMENT]
    chosen = {post["sample_id"] for post in high}
    rest = [post for post in ranked if post["sample_id"] not in chosen]
    ordinary = []
    if rest:
        step = max(1, len(rest) // REVIEW_ORDINARY)
        ordinary = rest[::step][:REVIEW_ORDINARY]
    # 方案 §三 第 2 步 的第三档是「带链接/卖货帖**全部**」，理由写在同一句里：
    # 「否则会被一条爆款带偏」。所以这一档不抽样、不截断——带货帖本来就少，
    # 全看才看得出带货是不是这个号的常态。
    links = [post for post in ranked if post["extra_json"].get("links")]
    return {"high_engagement": [post["sample_id"] for post in high],
            "ordinary": [post["sample_id"] for post in ordinary],
            "links": [post["sample_id"] for post in links]}


def _feature(name: str, label: str, layer: str, unit: str = "",
             value=None, note: str = "", sample_size: int | None = None) -> dict:
    return {"name": name, "label": label, "layer": layer, "unit": unit,
            "value": None if value is None else round(float(value), 6),
            "known": 0 if value is None else 1,
            "note": note, "sample_size": sample_size}


def features(store, handle: str) -> dict:
    """Compute one account's observable quantities from what was read.

    Nothing here is a score. A score is a claim about a judgement, and the
    judgement (which thresholds, which combination) is 06C-2's; what this
    returns are the quantities a rule would be written against.
    """
    clean = clean_handle(handle)
    profile = samples(store, clean, "profile")
    posts = [row for row in samples(store, clean, "post") if not row["extra_json"].get("is_reply")]
    received = samples(store, clean, "received")
    own = samples(store, clean, "reply")
    counts = {"profile": len(profile), "post": len(posts),
              "received": len(received), "reply": len(own)}
    rows: list[dict] = []
    rows.extend(_profile_features(profile, _exact_created_at(clean, posts, received, own)))
    rows.extend(_post_features(posts, profile, counts))
    rows.extend(_received_features(received, counts))
    rows.extend(_own_reply_features(own, counts))
    rows.extend(_unmeasurable(counts))
    return {"handle": clean, "counts": counts, "features": rows,
            "review": review_set(posts)}


def _exact_created_at(handle: str, *groups: list[dict]) -> int | None:
    """The account's registration time, taken from its own user object.

    The profile header only says "加入于 2011年12月". A search result carries the
    exact `created_at` of whoever wrote the post, so a page that happened to
    return the account's own posts pins the age to the day. Whichever value is
    found first is used; both come from X, neither is computed.
    """
    values = []
    for group in groups:
        for row in group:
            if str(row["author"]).lower() != handle:
                continue
            stats = row["extra_json"].get("author_stats") or {}
            value = stats.get("created_at")
            if isinstance(value, int) and value > 0:
                values.append(value)
    return min(values) if values else None


def _profile_features(profile: list[dict], exact_created_at: int | None = None) -> list[dict]:
    layer = "A 账号静态特征"
    if not profile:
        why = "主页没有读到，这一层暂时没有数据"
        return [_feature(name, label, layer, unit, None, why)
                for name, label, unit in (
                    ("followers", "粉丝数", "人"),
                    ("following", "关注数", "人"),
                    ("follower_following_ratio", "粉丝/关注比", ""),
                    ("account_age_days", "账号年龄", "天"),
                    ("profile_completeness", "主页完整度", ""),
                )]
    extra = profile[0]["extra_json"]
    followers = extra.get("followers")
    following = extra.get("following")
    followers = followers if isinstance(followers, int) and followers >= 0 else None
    following = following if isinstance(following, int) and following >= 0 else None
    rows = [
        _feature("followers", "粉丝数", layer, "人", followers,
                 "" if followers is not None else "主页上写成了 1.2K 这样的缩写，读不出准确数"),
        _feature("following", "关注数", layer, "人", following,
                 "" if following is not None else "主页没有给出可读的关注数"),
        _feature("follower_following_ratio", "粉丝/关注比", layer, "",
                 _ratio(followers, following) if (followers is not None and following)
                 else None,
                 "" if (followers is not None and following) else "粉丝数或关注数读不到，比不出来"),
    ]
    # The account's own user object carries an exact creation timestamp; the
    # profile header only carries month precision, so the exact one wins.
    age, note = None, ""
    if exact_created_at:
        age = (time.time() - exact_created_at) / 86400
    else:
        parsed = joined_date(str(extra.get("joined") or ""))
        if parsed:
            year, month = parsed
            age = (date.today() - date(year, month, 1)).days
            note = f"主页只给到「{year}年{month}月」，按该月 1 日计算"
    if age is None:
        note = "主页没有给出加入时间，读到的推文里也没有它自己的账号时间"
    rows.append(_feature("account_age_days", "账号年龄", layer, "天", age, note))
    present, observable = 0, 0
    # Only the fields the page actually showed are counted; a field the reader
    # could not find is left out of the ratio instead of counted as missing,
    # or a selector that stopped matching would read as an incomplete profile.
    observed_fields = (
        ("has_avatar", extra.get("has_avatar"), False),
        ("has_banner", extra.get("has_banner"), False),
        ("has_bio", extra.get("bio"), True),
        ("has_location", extra.get("location"), True),
        ("has_url", extra.get("url"), True),
    )
    for _name, value, is_text in observed_fields:
        if value is None:
            continue
        observable += 1
        present += 1 if (str(value).strip() if is_text else value) else 0
    rows.append(_feature(
        "profile_completeness", "主页完整度", layer, "",
        (present / observable) if observable else None,
        f"{present}/{observable} 项可见且已填写" if observable else "主页头图区的字段一个都没读到"))
    if isinstance(extra.get("verified"), bool):
        rows.append(_feature("verified", "蓝标认证", layer, "",
                             1 if extra["verified"] else 0))
    return rows


def _post_features(posts: list[dict], profile: list[dict], counts: dict) -> list[dict]:
    layer = "B 内容与互动统计"
    rows = [_feature("posts_sampled", "本次读到的原帖", layer, "条", len(posts),
                     sample_size=len(posts))]
    if not posts:
        why = "近 90 天的原帖一条都没读到"
        for name, label, unit in (
                ("posts_per_day", "发帖频率", "条/天"),
                ("impression_er_median", "曝光口径互动率中位数", ""),
                ("follower_er_median", "粉丝口径互动率中位数", ""),
                ("reply_like_ratio_median", "回复/点赞比中位数", ""),
                ("repost_like_ratio_median", "转推/点赞比中位数", ""),
                ("bookmark_like_ratio_median", "收藏/点赞比中位数", ""),
                ("engagement_cv", "互动的离散度", ""),
                ("post_gap_hours_median", "发帖间隔中位数", "小时"),
                ("max_posts_per_day", "单日最多发帖", "条"),
                ("link_share", "带链接帖占比", ""),
                ("media_share", "带图/视频帖占比", ""),
                ("text_cohesion", "主题一致性", "")):
            rows.append(_feature(name, label, layer, unit, None, why))
        return rows

    stamps = sorted(row["posted_at"] for row in posts if row["posted_at"])
    span_days = (max(1.0, (stamps[-1] - stamps[0]) / 86400) if len(stamps) > 1
                 else float(SAMPLE_WINDOW_DAYS))
    rows.append(_feature("posts_per_day", "发帖频率", layer, "条/天",
                         len(posts) / span_days, sample_size=len(posts)))

    followers = None
    if profile:
        value = profile[0]["extra_json"].get("followers")
        followers = value if isinstance(value, int) and value > 0 else None
    impression_er, follower_er, reply_ratio, repost_ratio = [], [], [], []
    bookmark_ratio, engagement = [], []
    for row in posts:
        metrics = row["metrics_json"]
        total = _engagement(metrics)
        engagement.append(total)
        views = metrics.get("views")
        if isinstance(views, int) and views > 0:
            impression_er.append(total / views)
        if followers:
            follower_er.append(total / followers)
        likes = metrics.get("likes")
        for name, target in (("replies", reply_ratio), ("retweets", repost_ratio),
                             ("bookmarks", bookmark_ratio)):
            value = metrics.get(name)
            if isinstance(value, int) and isinstance(likes, int) and likes > 0:
                target.append(value / likes)

    views_known = sum(1 for row in posts if isinstance(row["metrics_json"].get("views"), int))
    rows.append(_feature(
        "impression_er_median", "曝光口径互动率中位数", layer, "",
        _median(impression_er),
        "" if impression_er else
        (f"{len(posts)} 条里只有 {views_known} 条带浏览量，算不出比例" if not views_known
         else "读到的浏览量都是 0，无法做分母"),
        sample_size=len(impression_er)))
    rows.append(_feature(
        "follower_er_median", "粉丝口径互动率中位数", layer, "",
        _median(follower_er),
        "" if follower_er else "粉丝数没读到，这一项做不了分母",
        sample_size=len(follower_er)))
    for name, label, values in (
            ("reply_like_ratio_median", "回复/点赞比中位数", reply_ratio),
            ("repost_like_ratio_median", "转推/点赞比中位数", repost_ratio),
            ("bookmark_like_ratio_median", "收藏/点赞比中位数", bookmark_ratio)):
        rows.append(_feature(name, label, layer, "", _median(values),
                             "" if values else "没有一条同时给出这两个数字",
                             sample_size=len(values)))
    mean = statistics.fmean(engagement) if engagement else 0
    cv = (statistics.pstdev(engagement) / mean) if mean > 0 and len(engagement) > 1 else None
    rows.append(_feature(
        "engagement_cv", "互动的离散度", layer, "", cv,
        "" if cv is not None else "样本不足两条，算不出波动",
        sample_size=len(engagement)))

    gaps = [(stamps[index] - stamps[index - 1]) / 3600
            for index in range(1, len(stamps))]
    rows.append(_feature("post_gap_hours_median", "发帖间隔中位数", layer, "小时",
                         _median(gaps), "" if gaps else "只有一条有时间戳的帖子",
                         sample_size=len(gaps)))
    per_day: dict[str, int] = {}
    for stamp in stamps:
        per_day[date.fromtimestamp(stamp).isoformat()] = \
            per_day.get(date.fromtimestamp(stamp).isoformat(), 0) + 1
    rows.append(_feature("max_posts_per_day", "单日最多发帖", layer, "条",
                         max(per_day.values()) if per_day else None,
                         "" if per_day else "没有一条有时间戳的帖子",
                         sample_size=len(per_day)))
    rows.append(_feature("link_share", "带链接帖占比", layer, "",
                         round(sum(1 for row in posts if row["extra_json"].get("links")) / len(posts), 6),
                         sample_size=len(posts)))
    rows.append(_feature(
        "media_share", "带图/视频帖占比", layer, "",
        round(sum(1 for row in posts if row["extra_json"].get("has_media")) / len(posts), 6),
        sample_size=len(posts)))
    rows.append(_text_cohesion(posts))
    return rows


def _text_cohesion(posts: list[dict]) -> dict:
    """A lexical stand-in for 主题一致性, and labelled as one.

    Real topical consistency is a semantic question and needs the model layer.
    What is measurable without one is how much of each post's vocabulary is
    drawn from the account's own most common vocabulary; an account that writes
    about the same things keeps reusing the same words. The name says "词面"
    because a threshold written against this number is a threshold on wording.
    """
    texts = [row["text"] for row in posts if str(row["text"]).strip()]
    if len(texts) < MIN_POST_SAMPLE:
        return _feature("text_cohesion", "主题一致性", "B 内容与互动统计", "", None,
                        f"带正文的帖子只有 {len(texts)} 条，少于 {MIN_POST_SAMPLE} 条得出的比例不稳",
                        sample_size=len(texts))
    grams = [set(zip(parts, parts[1:])) for parts in (tokens(text) for text in texts)]
    grams = [gram for gram in grams if gram]
    if len(grams) < MIN_POST_SAMPLE:
        return _feature("text_cohesion", "主题一致性", "B 内容与互动统计", "", None,
                        "正文太短，连不到两个词，算不出用词重合",
                        sample_size=len(grams))
    frequency: dict[tuple, int] = {}
    for gram in grams:
        for item in gram:
            frequency[item] = frequency.get(item, 0) + 1
    top = {item for item, _ in sorted(frequency.items(), key=lambda pair: -pair[1])[:20]}
    coverage = [len(gram & top) / len(gram) for gram in grams]
    return _feature("text_cohesion", "主题一致性（词面代理）", "B 内容与互动统计", "",
                    _median(coverage),
                    "按「每帖有多少用词落在该账号最常用的 20 个词对里」计算，是词面代理，不是语义判断",
                    sample_size=len(grams))


def _received_features(received: list[dict], counts: dict) -> list[dict]:
    layer = "C 互动文本与互动者质量"
    rows = [_feature("received_replies_sampled", "本次读到的收到的回复", layer, "条",
                     len(received), sample_size=len(received))]
    if not received:
        why = "近 90 天没有读到别人回复它的内容"
        for name, label, unit in (
                ("distinct_engagers", "不重复的互动者", "人"),
                ("engager_repeat_rate", "互动者重复出现率", ""),
                ("reply_cliche_rate", "模板回复占比", ""),
                ("reply_short_generic_rate", "空泛短回复占比", ""),
                ("reply_intent_density", "意向评论密度", ""),
                ("engager_stats_known_share", "互动者账号字段可见率", ""),
                ("engager_median_followers", "互动者粉丝中位数", "人"),
                ("engager_median_statuses", "互动者发帖中位数", "条"),
                ("engager_median_age_days", "互动者账号年龄中位数", "天"),
                ("engager_empty_bio_rate", "互动者空简介占比", ""),
                ("engager_default_avatar_rate", "互动者默认头像占比", ""),
                ("empty_engager_rate", "空壳互动者占比", "")):
            rows.append(_feature(name, label, layer, unit, None, why))
        return rows
    authors = [row["author"] for row in received if row["author"]]
    distinct = len(set(authors))
    rows.append(_feature("distinct_engagers", "不重复的互动者", layer, "人",
                         distinct or None, "" if distinct else "回复里没有作者名",
                         sample_size=len(authors)))
    rows.append(_feature("engager_repeat_rate", "互动者重复出现率", layer, "",
                         (1 - distinct / len(authors)) if authors else None,
                         sample_size=len(authors)))
    cliche = sum(1 for row in received if _is_cliche(row["text"]))
    generic = sum(1 for row in received if _is_short_generic(row["text"]))
    intent = sum(1 for row in received
                 if INTENT_RE.search(normalize_reply(row["text"])))
    rows.append(_feature("reply_cliche_rate", "模板回复占比", layer, "",
                         cliche / len(received),
                         "命中固定套话表（好/赞/学到了/感谢分享…）、纯表情、或去掉 @ 和链接后没剩内容",
                         sample_size=len(received)))
    rows.append(_feature("reply_short_generic_rate", "空泛短回复占比", layer, "",
                         generic / len(received),
                         "去掉 @ 和链接后 ≤6 字、且不含问号、数字和话题标签；是弱代理，与上一条会重叠",
                         sample_size=len(received)))
    rows.append(_feature("reply_intent_density", "意向评论密度", layer, "",
                         intent / len(received),
                         "按一张固定词表命中（怎么买/多少钱/求教程/合作/价格…），词表本身就是判断口径",
                         sample_size=len(received)))
    stats = [row["extra_json"].get("author_stats") or {} for row in received]
    known = [item for item in stats if _author_stats_known(item)]
    rows.append(_feature(
        "engager_stats_known_share", "互动者账号字段可见率", layer, "",
        len(known) / len(stats) if stats else None,
        "" if known else "搜索结果没有带上互动者的账号字段（粉丝数/发帖数是 X 决定给不给的）",
        sample_size=len(stats)))
    followers = [item["followers"] for item in known
                 if isinstance(item.get("followers"), int)]
    statuses = [item["statuses"] for item in known
                if isinstance(item.get("statuses"), int)]
    observed = int(time.time())
    ages = [(observed - item["created_at"]) / 86400 for item in known
            if isinstance(item.get("created_at"), int) and item["created_at"] > 0]
    rows.append(_feature("engager_median_followers", "互动者粉丝中位数", layer, "人",
                         _median(followers),
                         "" if followers else "这一批互动者的粉丝数一条都没读到",
                         sample_size=len(followers)))
    rows.append(_feature("engager_median_statuses", "互动者发帖中位数", layer, "条",
                         _median(statuses),
                         "" if statuses else "这一批互动者的发帖数一条都没读到",
                         sample_size=len(statuses)))
    rows.append(_feature("engager_median_age_days", "互动者账号年龄中位数", layer, "天",
                         _median(ages),
                         "" if ages else "这一批互动者的注册时间一条都没读到",
                         sample_size=len(ages)))
    bios = [item["bio_length"] for item in known
            if isinstance(item.get("bio_length"), int)]
    rows.append(_feature("engager_empty_bio_rate", "互动者空简介占比", layer, "",
                         (sum(1 for value in bios if value == 0) / len(bios)) if bios else None,
                         "" if bios else "这一批互动者的简介字段一条都没读到",
                         sample_size=len(bios)))
    avatars = [item["default_avatar"] for item in known
               if isinstance(item.get("default_avatar"), bool)]
    rows.append(_feature("engager_default_avatar_rate", "互动者默认头像占比", layer, "",
                         (sum(1 for value in avatars if value) / len(avatars)) if avatars else None,
                         "" if avatars else "这一批互动者的头像字段一条都没读到",
                         sample_size=len(avatars)))
    # 方案 §五 的第一条一票否决要用这个数，而它是上面几项的**组合**而不是均值：
    # 头像或简介缺 → 看着就是壳；发帖极少 **且** 注册很新 → 行为像壳。分母只收
    # 头像与简介都看到了的人——没看到头像的人不能被判成空壳，这不是"不空壳"。
    shell = countable = 0
    for item in known:
        avatar, bio = item.get("default_avatar"), item.get("bio_length")
        if not isinstance(avatar, bool) or not isinstance(bio, int):
            continue
        countable += 1
        thin = isinstance(item.get("statuses"), int) and item["statuses"] <= SHELL_STATUSES_MAX
        created = item.get("created_at")
        young = (isinstance(created, int) and created > 0
                 and (observed - created) / 86400 < SHELL_AGE_DAYS)
        if avatar or bio == 0 or (thin and young):
            shell += 1
    rows.append(_feature(
        "empty_engager_rate", "空壳互动者占比", layer, "",
        (shell / countable) if countable else None,
        "" if countable else "这一批互动者的头像与简介字段一个都没读到，数不出谁是空壳",
        sample_size=countable or None))
    return rows


def _own_reply_features(own: list[dict], counts: dict) -> list[dict]:
    layer = "D 自己的回复"
    rows = [_feature("own_replies_sampled", "本次读到的自己的回复", layer, "条",
                     len(own), sample_size=len(own))]
    if not own:
        why = "近 90 天没有读到它回复别人的内容"
        for name, label, unit in (
                ("own_reply_length_median", "自己的回复长度中位数", "字"),
                ("own_reply_question_rate", "自己的回复里带提问的占比", ""),
                ("own_reply_link_rate", "自己的回复里带链接的占比", ""),
                ("own_reply_targets", "回复过的不重复对象", "人")):
            rows.append(_feature(name, label, layer, unit, None, why))
        return rows
    lengths = [len(normalize_reply(row["text"])) for row in own]
    rows.append(_feature("own_reply_length_median", "自己的回复长度中位数", layer, "字",
                         _median(lengths), sample_size=len(lengths)))
    rows.append(_feature(
        "own_reply_question_rate", "自己的回复里带提问的占比", layer, "",
        sum(1 for row in own if re.search(r"[?？]", row["text"])) / len(own),
        sample_size=len(own)))
    rows.append(_feature(
        "own_reply_link_rate", "自己的回复里带链接的占比", layer, "",
        sum(1 for row in own if row["extra_json"].get("links")) / len(own),
        sample_size=len(own)))
    targets = {row["extra_json"].get("reply_to") for row in own
               if row["extra_json"].get("reply_to")}
    rows.append(_feature("own_reply_targets", "回复过的不重复对象", layer, "人",
                         len(targets) or None,
                         "" if targets else "回复里没有带上被回复的人",
                         sample_size=len(own)))
    return rows


def _unmeasurable(counts: dict) -> list[dict]:
    """The three questions this layer cannot answer, and why.

    Recording them as rows rather than omitting them keeps 06C-2 from reading a
    missing row as "not computed yet". Two of the three are beyond any
    third-party read, so no later slice will fill them in either.
    """
    return [
        _feature("engagement_burst_score", "互动时间突发分数", "C 互动文本与互动者质量", "",
                 None, "第三方看不到单条推文的互动时间分布；要算突发只能靠自己反复快照同一批帖子，"
                       "这条通道目前没有做"),
        _feature("engager_overlap_rate", "互动者共现率", "C 互动文本与互动者质量", "",
                 None, "共现要看多个账号的互动者集合是否互相重叠，单个账号的一次读取给不出"),
        _feature("own_reply_context_match_rate", "自己的回复是否接得上原文", "D 自己的回复", "",
                 None, "搜索结果带的是回复本身，不带被回复帖子的正文，"
                       "「对牛弹琴」（§4.11 第 2 条）需要帖子详情页的正文，本通道拿不到"),
    ]


def feature_id(handle: str, observed_at: int) -> str:
    return hashlib.sha256(f"account_features:{handle}:{int(observed_at)}".encode()).hexdigest()[:32]


def confidence(counts: dict, reads: dict) -> str:
    """方案：回复样本 < 30 就标 low_confidence。这里把原帖样本一起算进来。

    `insufficient_data` 只在连一个问题都答不了时出现——主页没读到、帖子也没读到。
    """
    posts_ok = counts.get("post", 0) >= 1
    profile_ok = counts.get("profile", 0) >= 1
    if not posts_ok and not profile_ok:
        return "insufficient_data"
    if counts.get("post", 0) < MIN_POST_SAMPLE or counts.get("received", 0) < MIN_REPLY_SAMPLE:
        return "low_confidence"
    return "ok"


def record(store, handle: str, *, observed_at: int, task_id: str = "",
           reads: dict | None = None, failures: dict | None = None) -> dict:
    """Compute and store one account's features. Returns the summary row.

    `failures` names the reads that did not answer; they are part of the record
    the same way a gap inside a successful read is, because a feature that is
    missing for either reason has to say the same thing to whoever reads it.
    """
    clean = clean_handle(handle)
    computed = features(store, clean)
    counts = computed["counts"]
    reads = reads or {}
    failures = failures or {}
    quality = confidence(counts, reads)
    since, until = window(store)
    ident = feature_id(clean, observed_at)
    gaps = [state.get("coverage_gap", "") for state in reads.values()
            if state.get("coverage_gap")]
    gaps += [f"{READ_LABEL.get(kind, kind)}：{reason}"
             for kind, reason in sorted(failures.items())]
    gap_text = "；".join(dict.fromkeys(gaps))
    # 规则层拿的是刚算出来的这几行特征，不是回头再查一次库——存进特征表的值和拿去
    # 打分的值必须逐字相同，否则面板上「分数」和「特征」会各说各话。硬标签读的是同
    # 一份，只是它不看分数（方案 §三：硬标签绕过分数门槛）。
    scored = account_scoring.score(computed["features"], counts=counts,
                                   confidence=quality)
    flagged = account_flags.flags(computed["features"], counts=counts,
                                  confidence=quality)
    with store.db(True) as c:
        c.execute(
            """INSERT INTO account_features(
                   id,handle,observed_at,window_days,from_date,to_date,task_id,
                   sample_counts_json,coverage_gap,confidence,created)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   sample_counts_json=excluded.sample_counts_json,
                   coverage_gap=excluded.coverage_gap,confidence=excluded.confidence""",
            (ident, clean, int(observed_at), SAMPLE_WINDOW_DAYS, since, until,
             task_id, json.dumps({"counts": counts, "reads": reads,
                                  "failures": failures, "review": computed["review"]},
                                 ensure_ascii=False),
             gap_text, quality, int(time.time())))
        for row in computed["features"]:
            c.execute(
                """INSERT INTO account_feature_values(
                       feature_id,name,label,layer,unit,value,known,note,sample_size)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(feature_id,name) DO UPDATE SET
                       label=excluded.label,layer=excluded.layer,unit=excluded.unit,
                       value=excluded.value,known=excluded.known,note=excluded.note,
                       sample_size=excluded.sample_size""",
                (ident, row["name"], row["label"], row["layer"], row["unit"],
                 row["value"], row["known"], row["note"], row["sample_size"]))
        account_scoring.record(store, ident, clean, scored, c=c)
        account_flags.record(store, ident, clean, int(observed_at), flagged, c=c)
    return {"feature_id": ident, "handle": clean, "observed_at": int(observed_at),
            "confidence": quality, "counts": counts, "coverage_gap": gap_text,
            "failures": failures, "review": computed["review"],
            "score": scored, "flags": flagged,
            "known": sum(1 for row in computed["features"] if row["known"]),
            "features": len(computed["features"])}


def latest(store, handle: str) -> dict | None:
    """The newest feature run for an account, with its values in a stable order."""
    clean = clean_handle(handle)
    rows = store.rows(
        "SELECT * FROM account_features WHERE handle=? ORDER BY observed_at DESC LIMIT 1",
        (clean,))
    if not rows:
        return None
    run = rows[0]
    try:
        run["sample_counts_json"] = json.loads(run["sample_counts_json"])
    except (TypeError, ValueError):
        run["sample_counts_json"] = {}
    values = store.rows(
        "SELECT * FROM account_feature_values WHERE feature_id=? "
        "ORDER BY layer,rowid", (run["id"],))
    for row in values:
        row["known"] = bool(row["known"])
    run["values"] = values
    # 分数是这次特征运行的一部分，跟着 run 一起给出：面板画的是「这一次观察到的
    # 特征 + 由它推出的分」，两样东西都不该出现而另一样是上一轮的。
    run["score"] = account_scoring.for_feature(store, run["id"])
    # 标签和分数不一样：分数是那一刻的判断，从 account_scores 里读回来；标签是
    # 「这一次读到的特征是否命中某条硬规则」，而特征行本身是不可变的事实，所以按
    # 现在的规则版本重算一遍是**有定义的**，也才有「哪条看过没触发、哪条这一次没
    # 评」这本账。写进 account_flags 的那些行是给观察室用的命中流水，不在这里读。
    counts = (run.get("sample_counts_json") or {}).get("counts") or {}
    run["flags"] = account_flags.flags(run["values"], counts=counts,
                                       confidence=run["confidence"])
    return run


def sample_counts(store, handle: str) -> dict:
    """How many samples of each kind are stored, keyed by the *read* name."""
    clean = clean_handle(handle)
    counts = {kind: 0 for kind in READ_SET}
    reverse = {kind: read for read, kind in SAMPLE_KIND.items()}
    for row in store.rows(
            "SELECT sample_kind, count(*) AS n FROM account_samples "
            "WHERE handle=? GROUP BY sample_kind", (clean,)):
        counts[reverse.get(row["sample_kind"], row["sample_kind"])] = row["n"]
    return counts


def accounts(store) -> list[dict]:
    """Every account with a feature run or a sample, newest observation first.

    `confidence` is the newest run's, never the best one: an account whose latest
    read went badly must not keep the badge from an older, healthier read.
    """
    runs = {row["handle"]: row for row in store.rows(
        """SELECT f.handle, f.observed_at, f.confidence, f.coverage_gap,
                  (SELECT count(*) FROM account_feature_values v
                    WHERE v.feature_id=f.id) AS features,
                  (SELECT count(*) FROM account_feature_values v
                    WHERE v.feature_id=f.id AND v.known=1) AS known
             FROM account_features f
            WHERE f.rowid=(SELECT x.rowid FROM account_features x
                            WHERE x.handle=f.handle
                            ORDER BY x.observed_at DESC, x.id DESC LIMIT 1)""")}
    sampled = {row["handle"]: row for row in store.rows(
        "SELECT handle, count(*) AS samples, max(captured_at) AS captured_at "
        "FROM account_samples GROUP BY handle")}
    out = []
    for handle in set(runs) | set(sampled):
        run = runs.get(handle, {})
        has = sampled.get(handle, {})
        out.append({"handle": handle,
                    "observed_at": run.get("observed_at"),
                    "confidence": run.get("confidence"),
                    "coverage_gap": run.get("coverage_gap", ""),
                    "features": run.get("features", 0),
                    "known": run.get("known", 0),
                    "samples": has.get("samples", 0),
                    "captured_at": has.get("captured_at")})
    return sorted(out, key=lambda row: -(row["observed_at"]
                                        or row["captured_at"] or 0))
