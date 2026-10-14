"""Benchmark reads record what X published, and name what it withheld.

§4.7.2 asks for 浏览量、回复数、点赞数、收藏数 on other people's viral posts. X
does publish all four through SearchTimeline, so the risk is not a missing
feature but a wrong number: a quote's stub counts, a truncated body, or a
withheld view count rendered as 0. These tests pin each of those.
"""
import time

from studio import benchmarks
from studio.store import Store

HANDLE = "lifesinger"
CREATED = "Wed Apr 15 03:00:37 +0000 2026"
CREATED_EPOCH = 1776222037
NEWER = "Wed Aug 27 15:45:28 +0000 2026"


def tweet(post_id, text, *, likes=600, replies=15, retweets=162, quotes=24,
          bookmarks=750, views=108597, handle=HANDLE, note=None, quote=None,
          created=CREATED):
    legacy = {
        "created_at": created, "full_text": text, "favorite_count": likes,
        "reply_count": replies, "retweet_count": retweets, "quote_count": quotes,
        "bookmark_count": bookmarks,
    }
    result = {
        "__typename": "Tweet", "rest_id": post_id, "legacy": legacy,
        "core": {"user_results": {"result": {"core": {
            "screen_name": handle, "name": "Frank Wang 玉伯"}}}},
        "views": ({"state": "EnabledWithCount", "count": str(views)}
                  if views is not None else {"state": "Enabled"}),
    }
    if note is not None:
        result["note_tweet"] = {"note_tweet_results": {"result": {"text": note}}}
    if quote is not None:
        result["quoted_status_result"] = {"result": quote}
    return result


def item(result):
    return {"entryId": f"tweet-{result['rest_id']}", "content": {
        "entryType": "TimelineTimelineItem",
        "itemContent": {"tweet_results": {"result": result}}}}


CURSOR = {"entryId": "cursor-bottom-0", "content": {
    "entryType": "TimelineTimelineCursor", "cursorType": "Bottom",
    "value": "CURSOR-DOWN"}}


def page(*contents):
    return {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {
        "instructions": [{"type": "TimelineAddEntries", "entries": list(contents)}]}}}}}


def test_search_query_sends_only_the_filters_the_requirement_names():
    query = benchmarks.search_query(HANDLE, since="2025-10-14", until="2026-10-14")
    assert query == ("from:lifesinger min_faves:500 -filter:replies "
                     "-filter:retweets since:2025-10-14 until:2026-10-14")
    # §4.9.1 says the handle must not carry its @, and the table's extra
    # filters are examples -- adding them would shrink coverage silently.
    assert benchmarks.search_query("@lifesinger", since="s", until="u").startswith(
        "from:lifesinger ")
    assert "min_retweets" not in query and "lang:" not in query
    assert benchmarks.search_url(query).endswith("&f=live")


def test_the_window_is_one_year_before_today_until_today():
    import datetime

    assert benchmarks.one_year_window(datetime.date(2026, 10, 14)) == (
        "2025-10-14", "2026-10-14")


def test_a_long_post_is_read_from_its_note_body():
    body = "关于无招，直接说几句大白话：" + "很长的正文。" * 40
    payload = page(item(tweet("2065286424128331927", "关于无招…", note=body)))
    post = benchmarks.parse_search_page(
        payload, handle=HANDLE, observed_at=1)["posts"][0]
    assert post["text"] == body and post["text_source"] == "note_tweet"
    short = page(item(tweet("9", "短的")))
    post = benchmarks.parse_search_page(short, handle=HANDLE, observed_at=1)["posts"][0]
    assert post["text"] == "短的" and post["text_source"] == "full_text"


def test_a_quoted_post_cannot_replace_the_quoting_posts_counts():
    """A quoted post travels inside the parent, stub counts and all. Reading it
    by walking the JSON reported a live 874-like post as 0."""
    quoted = tweet("111", "被引用的原文", likes=0, replies=0, retweets=0,
                   quotes=0, bookmarks=0, views=None)
    payload = page(item(tweet("222", "曾一起逛知乎的朋友", likes=874, replies=224,
                              retweets=43, quotes=9, bookmarks=174,
                              views=172206, quote=quoted)))
    parsed = benchmarks.parse_search_page(payload, handle=HANDLE, observed_at=1)
    assert len(parsed["posts"]) == 1
    post = parsed["posts"][0]
    assert post["post_id"] == "222" and post["kind"] == "quote"
    assert post["metrics"] == {"likes": 874, "replies": 224, "retweets": 43,
                               "quotes": 9, "bookmarks": 174, "views": 172206}


def test_a_withheld_view_count_is_unseen_rather_than_zero():
    payload = page(item(tweet("333", "看不到浏览量", views=None, bookmarks=89)))
    post = benchmarks.parse_search_page(
        payload, handle=HANDLE, observed_at=1)["posts"][0]
    assert "views" not in post["metrics"]
    assert post["unknown_metrics"] == ["views"]
    assert post["metrics"]["bookmarks"] == 89


def test_a_missing_count_field_is_named_rather_than_zeroed():
    result = tweet("444", "缺字段")
    del result["legacy"]["bookmark_count"]
    payload = page(item(result))
    post = benchmarks.parse_search_page(
        payload, handle=HANDLE, observed_at=1)["posts"][0]
    assert "bookmarks" not in post["metrics"]
    assert "bookmarks" in post["unknown_metrics"]


def test_a_page_with_no_posts_is_exhausted_not_complete():
    """The end of a query looks like a page of cursors and nothing else."""
    parsed = benchmarks.parse_search_page(
        page(CURSOR), handle=HANDLE, observed_at=1)
    assert parsed["posts"] == [] and parsed["exhausted"] is True
    assert parsed["cursor"] == "CURSOR-DOWN"
    paginated = benchmarks.merge_pages([parsed, parsed], raw_query="q")
    assert paginated["complete"] is True


def test_an_unreadable_row_becomes_a_gap_not_a_post():
    payload = page(CURSOR, {"entryId": "tweet-1", "content": {
        "entryType": "TimelineTimelineItem",
        "itemContent": {"tweet_results": {"result": {
            "__typename": "TweetUnavailable"}}}}})
    parsed = benchmarks.parse_search_page(payload, handle=HANDLE, observed_at=1)
    assert parsed["posts"] == []
    assert parsed["gaps"] == [{"index": 1, "post_id": None,
                               "reason": "unavailable_tweet"}]
    assert "无法解析" in benchmarks.coverage_gap_text(
        parsed["gaps"], stopped_at_limit=True, limit=1000)
    assert "上限 1000" in benchmarks.coverage_gap_text(
        parsed["gaps"], stopped_at_limit=True, limit=1000)


def test_a_full_read_reports_no_gap():
    assert benchmarks.coverage_gap_text([], stopped_at_limit=False, limit=1000) == ""


def test_virality_uses_the_requirements_own_bar():
    payload = page(item(tweet("500", "刚好达标", likes=500)),
                   item(tweet("499", "差一个", likes=499)))
    posts = {p["post_id"]: p for p in benchmarks.parse_search_page(
        payload, handle=HANDLE, observed_at=1)["posts"]}
    assert posts["500"]["is_viral"] is True
    assert posts["499"]["is_viral"] is False


def test_an_unparseable_timestamp_stays_null():
    payload = page(item(tweet("666", "时间读不出", created="not a date")))
    post = benchmarks.parse_search_page(
        payload, handle=HANDLE, observed_at=1)["posts"][0]
    assert post["posted_at"] is None
    assert benchmarks.parse_search_page(
        page(item(tweet("667", "正常"))), handle=HANDLE,
        observed_at=1)["posts"][0]["posted_at"] == CREATED_EPOCH


def test_stored_posts_keep_the_counts_and_the_gaps_apart(tmp_path):
    store = Store(tmp_path)
    payload = page(item(tweet("333", "看不到浏览量", views=None)),
                   item(tweet("777", "正常", bookmarks=933, likes=920,
                              created=NEWER)))
    summary = benchmarks.record_search_page(
        store, handle=HANDLE, payload=payload, observed_at=1000)
    assert summary["parsed"] == 2
    rows = benchmarks.posts(store, HANDLE)
    assert [row["post_id"] for row in rows] == ["777", "333"]  # newest first
    unseen = next(row for row in rows if row["post_id"] == "333")
    assert unseen["metrics_json"] == {"likes": 600, "quotes": 24, "replies": 15,
                                      "retweets": 162, "bookmarks": 750}
    assert unseen["engagement_json"] == {"unknown": ["views"]}
    assert unseen["captured_at"] == 1000


def test_a_re_read_refreshes_growing_counts_without_duplicating_posts(tmp_path):
    store = Store(tmp_path)
    benchmarks.record_search_page(store, handle=HANDLE, observed_at=1000,
                                  payload=page(item(tweet("888", "在涨", likes=600))))
    benchmarks.record_search_page(store, handle=HANDLE, observed_at=2000,
                                  payload=page(item(tweet("888", "在涨", likes=6806))))
    rows = benchmarks.posts(store, HANDLE)
    assert len(rows) == 1
    assert rows[0]["metrics_json"]["likes"] == 6806
    assert rows[0]["captured_at"] == 2000


def test_a_later_empty_page_cannot_erase_an_earlier_post(tmp_path):
    """merge_pages keeps the first sighting, so a thinner stub cannot win."""
    first = benchmarks.parse_search_page(
        page(item(tweet("999", "完整"))), handle=HANDLE, observed_at=1)
    thinner = benchmarks.parse_search_page(
        page(item(tweet("999", "存根", likes=0, bookmarks=0))),
        handle=HANDLE, observed_at=1)
    merged = benchmarks.merge_pages([first, thinner])
    assert merged["posts"][0]["metrics"]["likes"] == 600


def test_the_two_accounts_named_by_the_requirement_are_recorded(tmp_path):
    store = Store(tmp_path)
    handles = [row["handle"] for row in benchmarks.accounts(store)]
    assert handles == ["lifesinger", "chunxiangai"]
    assert "§4.7.1" in benchmarks.accounts(store)[0]["note"]
    # Seeded accounts have no posts yet; an empty read must not invent any.
    assert benchmarks.posts(store, "lifesinger") == []


def test_adding_an_account_normalizes_the_at_sign(tmp_path):
    store = Store(tmp_path)
    benchmarks.add_account(store, "@someone", label="新对标", now=1234)
    rows = {row["handle"]: row for row in benchmarks.accounts(store)}
    assert "someone" in rows and rows["someone"]["label"] == "新对标"
    assert rows["someone"]["added_at"] == 1234


def test_the_read_stamps_its_own_query_so_coverage_is_checkable(tmp_path):
    store = Store(tmp_path)
    query = benchmarks.search_query(HANDLE, since="2025-10-14", until="2026-10-14")
    summary = benchmarks.record_search_page(
        store, handle=HANDLE, observed_at=int(time.time()),
        payload=page(item(tweet("123", "带查询式"))), raw_query=query)
    assert summary["parsed"] == 1
    assert summary["exhausted"] is False


def test_the_echoed_query_is_read_only_when_it_is_there():
    payload = page(item(tweet("1", "带查询式")))
    assert benchmarks.echoed_query(payload) == ""
    payload["data"]["search_by_raw_query"]["raw_query"] = "from:lifesinger min_faves:500"
    parsed = benchmarks.parse_search_page(payload, handle=HANDLE, observed_at=1,
                                         raw_query="from:lifesinger min_faves:500")
    # Both halves are kept: what was asked for, and what X says it answered.
    assert parsed["raw_query"].startswith("from:lifesinger")
    assert parsed["observed_query"] == "from:lifesinger min_faves:500"


def test_a_respaced_echo_still_matches_but_a_different_question_does_not():
    asked = "from:lifesinger min_faves:500 -filter:replies -filter:retweets"
    assert benchmarks.query_matches(asked, asked) is True
    assert benchmarks.query_matches("from:lifesinger  min_faves:500 "
                                    "-filter:retweets -filter:replies", asked) is True
    # No echo at all is unproved, not wrong: an X schema change must not be able
    # to kill every benchmark read the way a hard gate would.
    assert benchmarks.query_matches("", asked) is True
    assert benchmarks.query_matches("from:someoneelse min_faves:500", asked) is False
    # A window that has drifted is a different question even for the same author.
    assert benchmarks.query_matches("from:lifesinger min_faves:500 "
                                    "since:2024-01-01 until:2024-06-01", asked) is False
