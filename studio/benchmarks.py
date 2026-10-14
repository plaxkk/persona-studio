"""Read benchmark accounts' viral posts out of an X SearchTimeline response.

This module accepts business JSON that the authenticated browser has already
read. It performs no network requests and handles no browser credentials.

The search syntax is fixed by 产品需求 §4.9.1:

    from:<handle> min_faves:500 -filter:replies -filter:retweets

§4.9.1's parameter table lists ``min_retweets``/``min_replies``/``lang`` as
examples ("以上参数值是示例"), so they are deliberately *not* added: adding a
filter the requirement does not ask for would silently shrink coverage. The
``since:``/``until:`` dates are the exception — §4.9.1 says they must be the
year before today and today, so they are always sent.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

MIN_FAVES = 500
# §4.7.2 asks for 浏览量、回复数、点赞数、收藏数; the other two are kept because
# the response publishes them for free and 爆款拆解 needs 转发/引用 to separate
# a repost from a discussion.
METRICS = ("likes", "replies", "retweets", "quotes", "bookmarks", "views")
METRIC_SOURCE = {
    "likes": "favorite_count", "replies": "reply_count",
    "retweets": "retweet_count", "quotes": "quote_count",
    "bookmarks": "bookmark_count",
}
SEARCH_PRODUCT = "Latest"


class BenchmarkShapeError(ValueError):
    """The response does not have the observed SearchTimeline structure."""


def one_year_window(today: date) -> tuple[str, str]:
    """§4.9.1: since is one year before today, until is today."""
    return (today - timedelta(days=365)).isoformat(), today.isoformat()


def today(store) -> date:
    """Today where the studio lives. The window is relative to this date, so it
    is computed in one place rather than re-derived by each caller."""
    return datetime.now(ZoneInfo(store.get("timezone", "Asia/Shanghai"))).date()


def search_query(handle: str, *, since: str, until: str,
                 min_faves: int = MIN_FAVES) -> str:
    """Build the §4.9.1 query. §4.9.1 says the handle must not carry its @."""
    clean = handle.strip().lstrip("@")
    if not clean:
        raise BenchmarkShapeError("handle_missing")
    if not since or not until:
        raise BenchmarkShapeError("window_missing")
    return (f"from:{clean} min_faves:{int(min_faves)} "
            f"-filter:replies -filter:retweets since:{since} until:{until}")


def current_query(store, handle: str, *, min_faves: int = MIN_FAVES) -> str:
    """The §4.9.1 query for now: one year back, no replies, no retweets.

    The route quotes this query back to the panel and the job hands it to the
    connector, so it is built here once -- a route that promised one query while
    the job ran another is a difference nobody would notice until the numbers
    disagreed.
    """
    since, until = one_year_window(today(store))
    return search_query(handle, since=since, until=until, min_faves=min_faves)


def search_url(query: str) -> str:
    """The Latest tab, which is the one that honours since:/until: and pages."""
    return f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"


def echoed_query(payload: dict) -> str:
    """The query X says it answered, if the response carries it.

    An absent echo is not evidence of a mismatch, so it comes back empty and
    `query_matches` reads that as unproved rather than wrong.
    """
    try:
        seen = payload["data"]["search_by_raw_query"]["raw_query"]
    except (KeyError, TypeError):
        return ""
    return seen if isinstance(seen, str) else ""


def query_matches(seen: str, expected: str) -> bool:
    """Did the connector read the query we asked it to read?

    X echoes the query inside the response, so a page carrying a different one
    was served for a different question; storing its posts would put another
    search's numbers under this account's name. Compared as tokens because X is
    free to re-space the same query. A response with no echo at all is not a
    contradiction, so a schema change that drops the field cannot silently kill
    the read the way a hard gate would.
    """
    if not isinstance(seen, str) or not seen.strip():
        return True
    return set(seen.split()) == set(expected.split())


def _instructions(payload: dict) -> list:
    try:
        instructions = payload["data"]["search_by_raw_query"]["search_timeline"][
            "timeline"]["instructions"]
    except (KeyError, TypeError) as exc:
        raise BenchmarkShapeError("search_timeline_missing") from exc
    if not isinstance(instructions, list):
        raise BenchmarkShapeError("instructions_not_list")
    return instructions


def _entries(payload: dict) -> list[dict]:
    """Yield only the timeline's own items, plus the tail cursor.

    Reading the canonical path matters. A quoting post carries the *quoted*
    post's ``legacy`` as well, so a generic "find anything tweet-shaped" walk
    merges the quote's counts into the parent's: measured live, a post with 874
    likes was reported as having 0 because its quote's stub won the merge.
    """
    items = []
    for instruction in _instructions(payload):
        for entry in instruction.get("entries", []):
            if isinstance(entry.get("content"), dict):
                items.append(entry["content"])
    return items


def bottom_cursor(payload: dict) -> str | None:
    for content in _entries(payload):
        if content.get("cursorType") == "Bottom" and content.get("value"):
            return str(content["value"])
    return None


def _result(content: dict) -> dict | None:
    try:
        result = content["itemContent"]["tweet_results"]["result"]
    except (KeyError, TypeError):
        return None
    if not isinstance(result, dict):
        return None
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet")
    return result if isinstance(result, dict) else None


def _epoch_seconds(value: object) -> int | None:
    """X renders `Wed Apr 15 03:00:37 +0000 2026`. Unparseable stays NULL:
    a guessed timestamp would be indistinguishable from a real one later."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y").timestamp())
    except ValueError:
        return None


AUTHOR_STAT_SOURCE = {
    "followers": "followers_count",
    "following": "friends_count",
    "statuses": "statuses_count",
}


def author_stats(result: dict) -> dict:
    """The author's own account figures, as far as the response publishes them.

    §4.11's 空壳互动者 signals live on the engager's profile, and a search
    result carries the author's user object next to the tweet. Which of those
    fields X actually sends is not guaranteed, so each one is read on its own
    and an absent field stays None: a 0 written where the server sent nothing
    would be read later as a real count of zero followers.
    """
    core = ((result.get("core") or {}).get("user_results") or {}).get("result")
    if not isinstance(core, dict):
        return {}
    legacy = core.get("legacy")
    legacy = legacy if isinstance(legacy, dict) else {}
    stats: dict[str, object] = {"user_id": str(core.get("rest_id") or "") or None}
    for name, field in AUTHOR_STAT_SOURCE.items():
        value = legacy.get(field)
        stats[name] = (int(value) if isinstance(value, (int, float))
                       and not isinstance(value, bool) and value >= 0 else None)
    stats["created_at"] = _epoch_seconds(legacy.get("created_at"))
    description = legacy.get("description")
    stats["bio_length"] = len(description) if isinstance(description, str) else None
    default = legacy.get("default_profile_image")
    stats["default_avatar"] = default if isinstance(default, bool) else None
    verified = core.get("is_blue_verified")
    if not isinstance(verified, bool):
        verified = legacy.get("verified")
    stats["verified"] = verified if isinstance(verified, bool) else None
    return stats


def _link_count(legacy: dict) -> int:
    entities = legacy.get("entities")
    urls = entities.get("urls") if isinstance(entities, dict) else None
    return len([url for url in urls or [] if isinstance(url, dict)])


def _has_media(legacy: dict) -> bool:
    entities = legacy.get("entities")
    if isinstance(entities, dict) and entities.get("media"):
        return True
    return bool(legacy.get("extended_entities"))


def _text(result: dict) -> tuple[str, str]:
    """Long posts truncate `legacy.full_text`; the body lives in note_tweet.

    Measured on @lifesinger's page: 9 of 16 posts were truncated, one to 130
    characters where the real post ran to 283. 爆款拆解 reads the whole post, so
    the source is recorded alongside the text rather than assumed.
    """
    try:
        note = result["note_tweet"]["note_tweet_results"]["result"]["text"]
    except (KeyError, TypeError):
        note = None
    legacy = result.get("legacy") or {}
    full = legacy.get("full_text")
    if isinstance(note, str) and note.strip():
        return note, "note_tweet"
    return (full if isinstance(full, str) else ""), "full_text"


def _metrics(legacy: dict, result: dict) -> tuple[dict, list]:
    """Return the published counts, and the names of the ones X withheld.

    An absent metric is never written as 0. `views` is the case that actually
    varies: it is published as {"state": "EnabledWithCount", "count": "4138192"}
    and a state without a count means the number is not on offer at all.
    """
    metrics: dict[str, object] = {}
    unknown: list[str] = []
    for name, field in METRIC_SOURCE.items():
        value = legacy.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            metrics[name] = int(value)
        else:
            unknown.append(name)
    views = result.get("views")
    if isinstance(views, dict) and views.get("state") == "EnabledWithCount":
        count = views.get("count")
        try:
            metrics["views"] = int(str(count))
        except (TypeError, ValueError):
            unknown.append("views")
    else:
        unknown.append("views")
    return metrics, unknown


def parse_search_page(payload: dict, *, handle: str, observed_at: int,
                      min_faves: int = MIN_FAVES, raw_query: str = "") -> dict:
    """Parse one SearchTimeline page into post records plus explicit gaps.

    `exhausted` is reported rather than inferred: the Latest tab signals the end
    of a query by returning a page with cursors and no items, which is not the
    same as "this account has nothing more to show".
    """
    if not isinstance(payload, dict) or not handle:
        raise BenchmarkShapeError("search_request_invalid")
    clean = handle.strip().lstrip("@")
    posts, gaps = [], []
    for index, content in enumerate(_entries(payload)):
        if content.get("entryType") != "TimelineTimelineItem":
            continue
        result = _result(content)
        if result is None:
            gaps.append({"index": index, "post_id": None, "reason": "invalid_item"})
            continue
        post_id = str(result.get("rest_id") or "")
        legacy = result.get("legacy")
        if not post_id or not isinstance(legacy, dict):
            gaps.append({"index": index, "post_id": post_id or None,
                         "reason": "unavailable_tweet"})
            continue
        metrics, unknown = _metrics(legacy, result)
        text, text_source = _text(result)
        core = ((result.get("core") or {}).get("user_results") or {}).get("result") or {}
        author = (core.get("core") or {}).get("screen_name") or ""
        likes = metrics.get("likes")
        posts.append({
            "post_id": post_id,
            "handle": clean,
            "author": author,
            "text": text,
            "text_source": text_source,
            "url": f"https://x.com/{author or clean}/status/{post_id}",
            "posted_at": _epoch_seconds(legacy.get("created_at")),
            "kind": "quote" if result.get("quoted_status_result") else "post",
            "metrics": metrics,
            "unknown_metrics": unknown,
            # §4.7.2 is about 爆款, so virality is the requirement's own bar,
            # and an unreadable like count cannot be treated as passing it.
            "is_viral": bool(isinstance(likes, int) and likes >= min_faves),
            "captured_at": int(observed_at),
            # 06C reads the same page for account quality (§4.11), so the shape
            # of the post itself is parsed here too: a link-bearing post is a
            # different signal from a plain one, and a reply is not a post.
            "links": _link_count(legacy),
            "has_media": _has_media(legacy),
            "is_reply": bool(legacy.get("in_reply_to_status_id_str")),
            "reply_to": str(legacy.get("in_reply_to_screen_name") or ""),
            "author_stats": author_stats(result),
        })
    return {
        "handle": clean,
        "raw_query": raw_query,
        "observed_query": echoed_query(payload),
        "posts": posts,
        "gaps": gaps,
        "cursor": bottom_cursor(payload),
        "exhausted": not posts,
    }


def merge_pages(pages: list[dict], *, raw_query: str = "") -> dict:
    """Join paginated reads, keeping the first value seen for a duplicated id.

    Pages of one search can repeat a post when it moves between them; the first
    sighting is kept so a later, thinner stub cannot overwrite a full record.
    """
    posts: dict[str, dict] = {}
    gaps: list[dict] = []
    for page in pages:
        for gap in page.get("gaps", []):
            gaps.append(gap)
        for post in page.get("posts", []):
            posts.setdefault(post["post_id"], post)
    return {
        "raw_query": raw_query or (pages[0].get("raw_query", "") if pages else ""),
        "posts": list(posts.values()),
        "gaps": gaps,
        "complete": bool(pages) and bool(pages[-1].get("exhausted")),
    }


def coverage_gap_text(gaps: list[dict], *, stopped_at_limit: bool,
                      limit: int) -> str:
    """Describe what was *not* read, in the words the panel will show.

    Returns "" only when the read actually ran to the end of the query.
    """
    reasons: list[str] = []
    if stopped_at_limit:
        reasons.append(f"达到单次读取上限 {limit} 条，剩余结果未读取")
    if gaps:
        counted: dict[str, int] = {}
        for gap in gaps:
            counted[gap["reason"]] = counted.get(gap["reason"], 0) + 1
        detail = "、".join(f"{name} {count} 条" for name, count in sorted(counted.items()))
        reasons.append(f"有条目无法解析：{detail}")
    return "；".join(reasons)


def store_posts(store, posts: list[dict], *, handle: str, source: str = "search",
                gap_text: str = "") -> dict:
    """Upsert posts. Counts legitimately grow, so a re-read refreshes them and
    `captured_at` records when the shown number was true."""
    clean = handle.strip().lstrip("@")
    written = 0
    with store.db() as c:
        for post in posts:
            c.execute(
                """
                INSERT INTO benchmark_posts(
                    post_id,handle,author,text,text_source,url,posted_at,kind,
                    metrics_json,engagement_json,is_viral,captured_at,source,
                    coverage_gap)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(post_id) DO UPDATE SET
                    handle=excluded.handle,author=excluded.author,text=excluded.text,
                    text_source=excluded.text_source,url=excluded.url,
                    posted_at=excluded.posted_at,kind=excluded.kind,
                    metrics_json=excluded.metrics_json,
                    engagement_json=excluded.engagement_json,
                    is_viral=excluded.is_viral,captured_at=excluded.captured_at,
                    source=excluded.source,coverage_gap=excluded.coverage_gap
                """,
                (
                    post["post_id"], post["handle"], post.get("author", ""),
                    post.get("text", ""), post.get("text_source", ""),
                    post.get("url", ""), post.get("posted_at"),
                    post.get("kind", ""),
                    json.dumps(post.get("metrics", {}), sort_keys=True),
                    json.dumps({"unknown": post.get("unknown_metrics", [])},
                               sort_keys=True),
                    1 if post.get("is_viral") else 0, post.get("captured_at", 0),
                    source, gap_text,
                ),
            )
            written += 1
    return {"written": written}


def record_search_page(store, *, handle: str, payload: dict, observed_at: int,
                       raw_query: str = "", min_faves: int = MIN_FAVES,
                       source: str = "search") -> dict:
    """Parse and store one page. Returns a summary the caller can report."""
    page = parse_search_page(payload, handle=handle, observed_at=observed_at,
                             min_faves=min_faves, raw_query=raw_query)
    gap_text = coverage_gap_text(page["gaps"], stopped_at_limit=False, limit=0)
    if page["posts"]:
        store_posts(store, page["posts"], handle=handle, source=source,
                    gap_text=gap_text)
    return {"parsed": len(page["posts"]), "gaps": page["gaps"],
            "cursor": page["cursor"], "exhausted": page["exhausted"],
            "coverage_gap": gap_text}


def accounts(store, *, active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM benchmark_accounts"
    if active_only:
        sql += " WHERE active=1"
    return store.rows(sql + " ORDER BY added_at")


def add_account(store, handle: str, *, label: str = "", note: str = "",
                now: int | None = None) -> dict:
    clean = handle.strip().lstrip("@")
    if not clean:
        raise BenchmarkShapeError("handle_missing")
    with store.db() as c:
        c.execute(
            """
            INSERT INTO benchmark_accounts(handle,label,note,added_at,active)
            VALUES(?,?,?,?,1)
            ON CONFLICT(handle) DO UPDATE SET
                label=excluded.label,note=excluded.note,active=1
            """,
            (clean, label, note, int(now if now is not None else time.time())),
        )
    return {"handle": clean}


def posts(store, handle: str, *, viral_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM benchmark_posts WHERE handle=?"
    if viral_only:
        sql += " AND is_viral=1"
    rows = store.rows(sql + " ORDER BY posted_at IS NULL, posted_at DESC", (handle,))
    for row in rows:
        for field in ("metrics_json", "engagement_json"):
            try:
                row[field] = json.loads(row[field])
            except (TypeError, ValueError):
                row[field] = {}
        # SQLite has no boolean, so the flag comes back as 1/0; the API speaks JSON.
        row["is_viral"] = bool(row["is_viral"])
        # What X did not publish is part of the record (§4.8: 哪些数据看不到), so
        # the panel reads it the same way it reads the counts that are there.
        row["unknown_metrics"] = [name for name
                                  in row["engagement_json"].get("unknown", [])
                                  if name in METRICS]
    return rows


def deconstruction_id(post_id: str) -> str:
    return hashlib.sha256(f"deconstruction:{post_id}".encode()).hexdigest()[:32]
