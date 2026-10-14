"""06C-1: 账号观察只回答「测得到吗」——采集、特征、读不到时说清楚为什么。

Three things are pinned here, and they fail differently in production: the four
reads go out one at a time through the shared browser tab; what comes back lands
in `account_samples` and not in the benchmark tables; and a feature without a
sample is recorded as unknown with a reason rather than as 0.
"""
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from studio import account_quality, benchmarks
from studio.account_quality import READ_SET
from studio.api import create_app
from studio.browser_reads import SEARCH_MINIMUM, BrowserReads
from studio.browser_identity import EXTENSION_ORIGIN
from studio.desktop import CLOUD_ORIGIN, desktop_route
from studio.security import digest
from studio.xreader import ReadError

HANDLE = "lifesinger"
# The account's own registration time, as the search response publishes it.
JOINED_AT = int(datetime(2011, 12, 1, 5, 0, tzinfo=timezone.utc).timestamp())
# A post time inside the 90-day window the plan reads.
POST_TIME = int(datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc).timestamp())
# The three sample-count features are the only honest zeros: "we read zero
# posts" is a reading, unlike "we could not read how many posts there were".
COUNTERS = {"posts_sampled", "received_replies_sampled", "own_replies_sampled"}


def stamp(epoch: int) -> str:
    """X's timestamp format, which is the only one `parse` accepts."""
    return time.strftime("%a %b %d %H:%M:%S +0000 %Y", time.gmtime(epoch))


def user(handle=HANDLE, *, followers=12000, following=310, statuses=3400,
         bio="写代码的人", default_avatar=False, verified=False,
         joined_at=JOINED_AT):
    return {
        "rest_id": "9001", "is_blue_verified": verified,
        "core": {"screen_name": handle, "name": "玉伯"},
        "legacy": {"followers_count": followers, "friends_count": following,
                   "statuses_count": statuses, "created_at": stamp(joined_at),
                   "description": bio, "default_profile_image": default_avatar,
                   "verified": verified},
    }


def post(post_id, *, text=None, handle=HANDLE, likes=600, replies=15,
         retweets=162, quotes=24, bookmarks=750, views=108597, stats=None,
         links=0, media=False, reply_to=None, created=POST_TIME):
    body = text if text is not None else f"第 {post_id} 条"
    legacy = {
        "created_at": stamp(created), "full_text": body,
        "favorite_count": likes, "reply_count": replies,
        "retweet_count": retweets, "quote_count": quotes,
        "bookmark_count": bookmarks,
        "entities": {"urls": [{"expanded_url": "https://example.com/写"}] * links,
                     "media": [{"type": "photo"}] if media else []},
    }
    if reply_to:
        legacy["in_reply_to_status_id_str"] = "1"
        legacy["in_reply_to_screen_name"] = reply_to
    return {
        "__typename": "Tweet", "rest_id": post_id, "legacy": legacy,
        "core": {"user_results": {"result": stats or user(handle)}},
        "views": ({"state": "EnabledWithCount", "count": str(views)}
                  if views is not None else {"state": "Enabled"}),
    }


def page(*results, echo=None):
    payload = {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {
        "instructions": [{"type": "TimelineAddEntries", "entries": [
            {"entryId": f"tweet-{item['rest_id']}", "content": {
                "entryType": "TimelineTimelineItem",
                "itemContent": {"tweet_results": {"result": item}}}}
            for item in results]}]}}}}}
    if echo is not None:
        payload["data"]["search_by_raw_query"]["raw_query"] = echo
    return payload


def pages(*results, stopped="exhausted", echo=None):
    """The items a driver read, then the empty page that ended the search.

    Only the empty page says the search ran out of road, so a read without it is
    recorded as not having reached the end.
    """
    first = page(*results, echo=echo)
    return [first] + ([page()] if stopped == "exhausted" else [])


def profile(**over):
    return {"name": "玉伯", "bio": "写代码的人", "location": "杭州",
            "url": "https://example.com", "joined": "Joined December 2011",
            "followers": 12000, "following": 310,
            # The header carries no post total; recording it as absent, with why,
            # keeps it from reading later as "this account has 0 posts".
            "posts_count": None, "posts_count_reason": "主页头图区不显示帖子总数",
            "has_avatar": True, "default_avatar": False, "has_banner": True,
            "verified": False, **over}


def queued(store, *, handle=HANDLE, ident="a" * 32, version="1.4.0",
           status="running"):
    """A quality task with its four queue rows, as the worker would leave them."""
    now = int(time.time())
    store.set("x_username", "kkplayit")
    with store.db(True) as db:
        db.execute("INSERT OR REPLACE INTO persona_browser_presence VALUES(1,?,?)",
                   (version, now))
        db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available)"
                   " VALUES(?,?,?,?,?,?,?)",
                   (ident, "account_quality", json.dumps({"handle": handle}),
                    status, now, now, now))
        plan = account_quality.plan(store, handle)
        for kind in READ_SET:
            db.execute("INSERT INTO account_reads(task_id,kind,handle,account,query,"
                       "status,updated) VALUES(?,?,?,?,?,?,?)",
                       (ident, kind, plan["handle"], "kkplayit",
                        plan["reads"].get(kind, ""), "queued", now))
    return BrowserReads(store)


def job_of(store, ident="a" * 32):
    row = store.rows("SELECT id,payload FROM tasks WHERE id=?", (ident,))[0]
    return {"id": row["id"], "kind": "account_quality",
            "payload": json.loads(row["payload"])}


def search_result(reader, action, results=(), *, stopped="exhausted", echo=None):
    """What the driver sends back for one search, echoing the query it ran."""
    row = reader.store.rows("SELECT query FROM account_reads WHERE id=?",
                            (int(action["id"][8:]),))[0]
    payload = pages(*results, stopped=stopped,
                    echo=row["query"] if echo is None else echo)
    return {"state": "ready", "account": "kkplayit", "handle": action["handle"],
            "stopped_reason": stopped, "pages": payload}


def take(reader):
    """The next read the driver would be handed.

    `next_action()` sends as it selects, so it is not idempotent: calling it
    twice hands out two reads and the second one blocks the first.
    """
    action = reader.next_action()
    assert action, "the queue had nothing to send"
    return action


def answer_profile(reader, record=None, *, handle=None, action=None):
    action = action or take(reader)
    assert action["kind"] == "account_read", action
    reader.receive(action["id"], {"state": "ready", "account": "kkplayit",
                                  "handle": handle or action["handle"],
                                  "record": record or profile()})
    return action


def answer_search(reader, results=(), *, stopped="exhausted", echo=None, action=None):
    action = action or take(reader)
    assert action["kind"] == "search_read", action
    reader.receive(action["id"], search_result(reader, action, results,
                                               stopped=stopped, echo=echo))
    return action


def resend(store, ident):
    """Make a requeued read selectable again without waiting out its backoff."""
    with store.db(True) as db:
        db.execute("UPDATE account_reads SET updated=0 WHERE id=?", (ident,))


def observe(store, job, *steps):
    """Run the worker's `quality()` while a driver answers its four reads.

    Order matters here: `quality()` first re-queues any read that is not a
    success — that is the 补读 semantics, a failed read is worth another try on
    a later run — and only then waits. Answering the reads beforehand and
    calling `quality()` afterwards re-queues the failed one and waits out
    ACCOUNT_DEADLINE for a driver that has already gone home.
    """
    reader = BrowserReads(store)

    async def run():
        task = asyncio.create_task(reader.quality(job, lambda job: True))
        await asyncio.sleep(0)  # let it create (or re-queue) its rows first
        for step in steps:
            step(reader)
            await asyncio.sleep(0)
        return await task
    return asyncio.run(run())


def profile_step(record=None, **kw):
    return lambda reader: answer_profile(reader, record, **kw)


def posts_step(results, **kw):
    return lambda reader: answer_search(reader, results, **kw)


def failing_step(state="source_error", attempts=3):
    """Exhaust one read's retries, leaving it finished and failed."""
    def step(reader):
        for _ in range(attempts):
            action = take(reader)
            reader.receive(action["id"], {"state": state})
            resend(reader.store, int(action["id"][8:]))
    return step


def states(store, ident="a" * 32):
    return account_quality.read_states(store, ident)


def by_name(store, handle=HANDLE):
    return {row["name"]: row
            for row in account_quality.features(store, handle)["features"]}


def stored(store, table, **where):
    clause = " AND ".join(f"{key}=?" for key in where) or "1"
    return store.rows(f"SELECT * FROM {table} WHERE {clause}", tuple(where.values()))


def app_store(tmp_path):
    # A brand new studio starts paused, and a quality read browses X, so it is
    # deliberately not on the paused-mode allowlist: the route must 409 until the
    # operator resumes syncing.
    store = create_app(tmp_path, "http://testserver").state.store
    store.set("paused", False)
    return store


# --- 采集层 -----------------------------------------------------------------

def test_the_plan_owns_the_queries_it_will_run(tmp_path):
    store = app_store(tmp_path)
    plan = account_quality.plan(store, "@" + HANDLE.upper())
    today = benchmarks.today(store).isoformat()
    assert plan["handle"] == HANDLE and plan["until"] == today
    since, until = plan["since"], plan["until"]
    # §4.11 的三段读法写死在这里，这样改查询构造器不会悄悄换掉搜的是什么。
    # 质量读取不带 min_faves：一个账号的常态互动率不能只靠最火的那几条算。
    assert plan["reads"] == {
        "posts": f"from:{HANDLE} -filter:replies -filter:retweets since:{since} until:{until}",
        "received": f"to:{HANDLE} -from:{HANDLE} since:{since} until:{until}",
        "replies": f"from:{HANDLE} filter:replies -filter:retweets since:{since} until:{until}",
    }
    assert "min_faves" not in " ".join(plan["reads"].values())
    # 主页不是搜索，所以它没有查询，但它在读取集合里。
    assert account_quality.PROFILE not in plan["reads"]
    assert tuple(READ_SET) == ("profile", "posts", "received", "replies")
    assert plan["since"] < plan["until"]
    assert account_quality.clean_handle("@LifeSinger") == HANDLE


def test_a_quality_read_needs_a_1_4_connector(tmp_path):
    store = app_store(tmp_path)
    queued(store, version="1.3.13")
    with pytest.raises(ReadError) as raised:
        asyncio.run(BrowserReads(store).quality(job_of(store), lambda job: True))
    assert raised.value.code == "browser_offline"
    assert SEARCH_MINIMUM == (1, 4, 0)


def test_the_four_reads_share_one_queue_and_one_tab(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    order, queries, idents = [], [], set()
    for _ in READ_SET:
        action = take(reader)
        assert action["transport"] == "sync" and action["id"].startswith("account:")
        # 一个浏览器标签页上跑不了两个读取：在手的那条没回来之前不发下一条。
        assert reader.next_action() is None
        assert action["id"] not in idents
        idents.add(action["id"])
        order.append(action["kind"])
        if action["kind"] == "account_read":
            assert action["handle"] == HANDLE and "query" not in action
            answer_profile(reader, action=action)
        else:
            assert action["limit"] == account_quality.SAMPLE_LIMIT
            assert action["url"] == benchmarks.search_url(action["query"])
            queries.append(action["query"])
            answer_search(reader, [post("2065286424128331927")], action=action)
    # 主页先发（队列里的第一条），三条搜索各不相同，而且不是对标那套带 min_faves 的查询。
    assert order == ["account_read", "search_read", "search_read", "search_read"]
    assert len(set(queries)) == 3 and all("min_faves" not in q for q in queries)
    rows = stored(store, "account_reads")
    assert len(rows) == 4 and all(row["status"] == "done" for row in rows)


def test_a_ready_search_becomes_samples_not_benchmark_posts(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    answer_search(reader, [post("2065286424128331927", links=1)])
    rows = account_quality.samples(store, HANDLE, "post")
    assert [row["sample_id"] for row in rows] == ["2065286424128331927"]
    assert rows[0]["text_source"] == "full_text"
    assert rows[0]["metrics_json"]["likes"] == 600
    assert rows[0]["extra_json"]["links"] == 1
    assert rows[0]["extra_json"]["is_reply"] is False
    assert states(store)["posts"]["state"] == "ready"
    # 质量读取没有爆款门槛，所以它绝不能落进对标表——否则那会读成一个达标数
    # 为 0 的对标账号，而那个 0 是这次读取造成的，不是这个账号的。
    assert stored(store, "benchmark_posts") == []


def test_a_profile_read_lands_in_the_samples_table(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    rows = stored(store, "account_samples", sample_kind="profile")
    assert len(rows) == 1 and rows[0]["handle"] == HANDLE
    extra = json.loads(rows[0]["extra_json"])
    assert extra["followers"] == 12000 and extra["joined"] == "Joined December 2011"
    # 主页不显示帖子总数，读不到就记成读不到，不猜一个数。
    assert extra["posts_count"] is None and extra["posts_count_reason"]
    assert rows[0]["text"] == "写代码的人" and rows[0]["text_source"] == "dom"
    # A 层是这一片存在的理由之一，所以它必须真的从这次读取里算出来。
    named = by_name(store)
    assert named["followers"]["value"] == 12000
    assert named["follower_following_ratio"]["value"] == pytest.approx(12000 / 310)
    assert named["profile_completeness"]["value"] == 1
    # 主页只给到月，所以年龄算得出来、但要把精度写在旁边。
    assert named["account_age_days"]["known"] == 1
    assert "2011年12月" in named["account_age_days"]["note"]
    assert account_quality.features(store, HANDLE)["counts"]["profile"] == 1


def test_a_reread_replaces_the_profile_instead_of_adding_one(tmp_path):
    store = app_store(tmp_path)
    account_quality.store_profile(store, HANDLE, profile(), observed_at=1000)
    account_quality.store_profile(
        store, HANDLE, profile(followers=12001, bio="改了简介"), observed_at=2000)
    rows = stored(store, "account_samples", sample_kind="profile")
    assert len(rows) == 1
    assert rows[0]["captured_at"] == 2000
    assert rows[0]["text"] == "改了简介"
    # 这一行说的是「现在看到的」，所以上个月的读数不能被留下当这次的答案。
    assert json.loads(rows[0]["metrics_json"])["followers"] == 12001
    assert json.loads(rows[0]["extra_json"])["followers"] == 12001


def test_a_page_about_another_account_is_refused(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader, handle="someoneelse")
    assert states(store)["profile"]["state"] == "account_mismatch"
    # 重试治不好这个：读到的是另一个号，得人来改，所以重试次数用光也不算失败。
    assert states(store)["profile"]["status"] == "done"
    assert stored(store, "account_samples") == []


def test_a_different_query_in_the_response_is_refused(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    answer_search(reader, [post("2065286424128331927")],
                  echo=f"from:someoneelse min_faves:500 -filter:replies")
    assert states(store)["posts"]["state"] == "query_mismatch"
    assert account_quality.samples(store, HANDLE, "post") == []


def test_a_read_that_never_arrives_is_retried_twice_then_given_up(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    for _ in range(3):
        # A requeue puts the row back to 'queued' with a backoff, so the driver
        # has to be handed it again before the next failure can be reported.
        action = take(reader)
        reader.receive(action["id"], {"state": "source_error"})
        resend(store, int(action["id"][8:]))
    # 三次都读不出来就收手，并且留下"读不出来"这个事实，而不是一个空的成功。
    assert states(store)["posts"] == {"status": "done", "state": "source_error",
                                      "complete": False, "coverage_gap": ""}
    assert account_quality.samples(store, HANDLE, "post") == []


def test_a_read_cut_short_records_what_it_did_not_read(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    answer_search(reader, [post(f"20652864241283319{n}", created=POST_TIME + n * 86400)
                           for n in range(20)], stopped="buffer_full")
    state = states(store)["posts"]
    assert state["state"] == "ready" and state["complete"] is False
    assert "浏览器一次带回的页面总量到顶" in state["coverage_gap"]
    # 缺口不影响已经拿到的样本：读到的 20 条照样入库。
    assert len(account_quality.samples(store, HANDLE, "post")) == 20


# --- 特征层 -----------------------------------------------------------------

def test_nothing_read_is_insufficient_data(tmp_path):
    store = app_store(tmp_path)
    computed = account_quality.features(store, HANDLE)
    assert computed["counts"] == {"profile": 0, "post": 0, "received": 0, "reply": 0}
    assert account_quality.confidence(computed["counts"], {}) == "insufficient_data"
    for row in computed["features"]:
        if row["name"] in COUNTERS:
            # 「读到了 0 条」是一个读数，所以它有值；别的没有。
            assert row["known"] == 1 and row["value"] == 0
            continue
        assert row["known"] == 0 and row["value"] is None and row["note"], row["name"]
    named = {row["name"]: row for row in computed["features"]}
    assert named["followers"]["note"] == "主页没有读到，这一层暂时没有数据"
    assert named["reply_cliche_rate"]["note"] == "近 90 天没有读到别人回复它的内容"
    assert computed["review"] == {"high_engagement": [], "ordinary": [], "links": []}


def test_the_three_unmeasurable_features_are_recorded_with_a_reason(tmp_path):
    store = app_store(tmp_path)
    named = by_name(store)
    # 这三个问题这条通道答不了。留成行而不是删掉，是为了让 06C-2 分得清
    # "还没算" 和 "算不了"。
    for name in ("engagement_burst_score", "engager_overlap_rate",
                 "own_reply_context_match_rate"):
        row = named[name]
        assert row["known"] == 0 and row["value"] is None and len(row["note"]) > 10
    assert all(row["layer"].startswith(("A ", "B ", "C ", "D "))
               for row in account_quality.features(store, HANDLE)["features"])


def test_observed_quantities_are_numbers_and_unknowns_are_not_zero(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    answer_search(reader, [post(f"20652864241283319{n}", likes=100 + n,
                                bookmarks=200 + n, links=1 if n % 2 else 0,
                                media=n % 3 == 0, created=POST_TIME + n * 86400)
                           for n in range(12)])
    named = by_name(store)
    assert named["posts_sampled"]["value"] == 12
    assert named["link_share"]["value"] == pytest.approx(0.5)
    assert named["media_share"]["value"] == pytest.approx(4 / 12)
    # 时间戳读到了，所以发帖节奏才有数；这是"可观测"和"读不到"的分界。
    assert named["post_gap_hours_median"]["value"] == pytest.approx(24)
    assert named["impression_er_median"]["value"] is not None
    assert named["follower_er_median"]["known"] == 1  # 主页读到了，能做分母
    expected = statistics.median([(200 + n) / (100 + n) for n in range(12)])
    assert named["bookmark_like_ratio_median"]["value"] == pytest.approx(
        expected, rel=1e-4)
    # 没有读到互动者，所以那一层是「不知道」，不是「0 个粉丝」。
    assert named["engager_median_followers"]["value"] is None
    assert named["engager_empty_bio_rate"]["value"] is None
    assert named["engager_median_followers"]["note"]
    # 没有互动者就没有样本数可言：0 会和「读了 0 个互动者」混起来。
    assert named["engager_median_followers"]["sample_size"] is None
    counts = account_quality.features(store, HANDLE)["counts"]
    assert counts == {"profile": 1, "post": 12, "received": 0, "reply": 0}
    assert account_quality.confidence(counts, {}) == "low_confidence"


def test_a_reply_is_not_counted_as_a_post(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    answer_search(reader, [post("1101"), post("1102", reply_to="someoneelse")])
    # -filter:replies 应该已经排掉回复；万一它漏进来一条，它也不该被当成原帖。
    assert account_quality.features(store, HANDLE)["counts"]["post"] == 1
    assert len(account_quality.samples(store, HANDLE, "post")) == 2


def test_engager_stats_ride_along_with_the_reply_sample(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    answer_search(reader, [post("1001")])
    answer_search(reader, [
        post(f"300{n}", handle=f"engager{n}", likes=3, text="学到了",
             stats=user(f"engager{n}", followers=3 * n + 3, statuses=2,
                        bio="" if n % 2 else "hi", default_avatar=True))
        for n in range(4)])
    named = by_name(store)
    counts = account_quality.features(store, HANDLE)["counts"]
    assert counts["received"] == 4
    assert named["distinct_engagers"]["value"] == 4
    assert named["engager_median_followers"]["value"] == pytest.approx(7.5)  # 3/6/9/12
    assert named["engager_median_statuses"]["value"] == pytest.approx(2)
    assert named["engager_median_age_days"]["known"] == 1
    assert named["engager_empty_bio_rate"]["value"] == pytest.approx(0.5)
    assert named["engager_default_avatar_rate"]["value"] == pytest.approx(1)
    assert named["engager_stats_known_share"]["value"] == pytest.approx(1)
    assert named["reply_cliche_rate"]["value"] == pytest.approx(1)


def test_a_reply_that_only_delivers_a_link_is_not_content(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    answer_search(reader, [post("1001")])
    answer_search(reader, [
        post("4001", handle="bot", text="@lifesinger https://t.me/joinchat/xx"),
        post("4002", handle="human", text="我也踩过这个坑，后来改成用原子写了"),
        post("4003", handle="asker", text="这个怎么买？多少钱？"),
    ])
    named = by_name(store)
    # 只带 @ 和链接的回复去掉那些 token 之后没剩内容，算模板；它也不是一次意向咨询。
    assert named["reply_cliche_rate"]["value"] == pytest.approx(1 / 3)
    assert named["reply_intent_density"]["value"] == pytest.approx(1 / 3)
    assert named["engager_repeat_rate"]["value"] == pytest.approx(0)


def test_the_review_set_is_the_same_set_twice(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    answer_search(reader, [post(f"20652864241283319{n}", likes=n * 10)
                           for n in range(20)])
    first = account_quality.features(store, HANDLE)["review"]
    second = account_quality.features(store, HANDLE)["review"]
    # 随机抽会让两个人看同一批数据却看到不同的帖子，然后把差异叫成分歧。
    assert first == second
    assert first["high_engagement"] == [f"20652864241283319{n}"
                                        for n in range(19, 11, -1)]
    assert len(first["ordinary"]) == account_quality.REVIEW_ORDINARY
    assert not set(first["high_engagement"]) & set(first["ordinary"])


def test_every_post_with_a_link_is_in_the_review_set(tmp_path):
    """方案 §三 第 2 步 的第三档：「带链接/卖货帖**全部**」「否则会被一条爆款带偏」。

    An account's 带货 posts are exactly the ones a 爆款 can bury, so this bucket
    is neither sampled nor truncated: 20 链接帖 all come back, even though the
    other two buckets cap at 8.
    """
    store = app_store(tmp_path)
    reader = queued(store)
    answer_profile(reader)
    quiet = [post(f"20652864241283319{n}", likes=1, links=1) for n in range(20)]
    loud = [post(f"2099000000000000{n}", likes=1000 - n) for n in range(20)]
    answer_search(reader, quiet + loud)
    review = account_quality.features(store, HANDLE)["review"]
    links = [f"20652864241283319{n}" for n in range(20)]
    assert review["links"] == links
    # 「全部」是真的全部：这一档比另外两档的 8 条上限还长，也没有被截断。
    assert account_quality.REVIEW_ORDINARY == 8 < len(review["links"])
    assert not set(review["high_engagement"]) & set(links)
    assert len(review["ordinary"]) == account_quality.REVIEW_ORDINARY


def test_a_partial_read_still_records_features_and_names_what_failed(tmp_path):
    store = app_store(tmp_path)
    queued(store)
    summary = observe(
        store, job_of(store),
        profile_step(),
        posts_step([post(f"20652864241283319{n}") for n in range(11)]),
        failing_step(),
        posts_step([post(f"2077000000000000{n}") for n in range(4)]))
    assert states(store)["received"] == {"status": "done", "state": "source_error",
                                         "complete": False, "coverage_gap": ""}
    # 读到一半也是答案：三个读到了、一个没有，这条读取仍然要出特征。
    assert summary["partial"] is True
    assert summary["reads"] == {"profile": "ready", "posts": "ready",
                               "received": "source_error", "replies": "ready"}
    assert summary["counts"] == {"profile": 1, "post": 11, "received": 0, "reply": 4}
    assert summary["confidence"] == "low_confidence"
    assert summary["coverage_gap"] == "收到的回复：页面结构读不出来"
    # 特征照样算出来了：读不到的只是那一层。
    assert by_name(store)["posts_sampled"]["value"] == 11
    assert by_name(store)["engager_median_followers"]["value"] is None


# --- 记录与路由 -------------------------------------------------------------

def finished():
    """The four steps a driver takes to answer a quality task."""
    return (
        profile_step(),
        posts_step([post(f"20652864241283319{n}", links=n % 2, media=n % 3 == 0,
                         created=POST_TIME + n * 86400) for n in range(12)]),
        posts_step([post(f"2099000000000000{n}", handle=f"engager{n}", likes=2,
                         text="怎么买？" if n % 2 else "学到了",
                         stats=user(f"engager{n}", followers=5 * n + 5))
                    for n in range(32)]),
        posts_step([post(f"2077000000000000{n}", text="我也遇到过",
                         reply_to=f"peer{n}") for n in range(4)]),
    )


def test_one_run_records_features_and_does_not_reread_successes(tmp_path):
    store = app_store(tmp_path)
    queued(store)
    job = job_of(store)
    summary = observe(store, job, *finished())
    assert summary["partial"] is False and summary["failures"] == {}
    assert summary["reads"] == {kind: "ready" for kind in READ_SET}
    assert summary["confidence"] == "ok"
    run = account_quality.latest(store, HANDLE)
    assert run["id"] == summary["feature_id"] and run["task_id"] == job["id"]
    values = {row["name"]: row for row in run["values"]}
    assert values["posts_sampled"]["known"] is True
    assert values["posts_sampled"]["value"] == 12
    assert values["engager_median_followers"]["known"] is True
    assert values["engagement_burst_score"]["known"] is False
    counted = run["sample_counts_json"]
    assert counted["counts"]["post"] == 12 and counted["counts"]["received"] == 32
    assert counted["review"]["high_engagement"] and counted["review"]["ordinary"]
    # 录在特征行里的是每个读取自己的状态，不只是"成功"与否。
    assert {kind: state["state"] for kind, state in counted["reads"].items()} == {
        kind: "ready" for kind in READ_SET}
    # 再跑一次不会重发已经读成功的读取——四个读取要占着一个浏览器标签页几分钟。
    again = asyncio.run(BrowserReads(store).quality(job, lambda job: True))
    assert again["reads"] == {kind: "ready" for kind in READ_SET}
    assert all(row["status"] == "done" for row in stored(store, "account_reads"))


def test_recording_twice_for_the_same_instant_updates_one_run(tmp_path):
    store = app_store(tmp_path)
    first = account_quality.record(store, HANDLE, observed_at=1000)
    second = account_quality.record(store, HANDLE, observed_at=1000)
    assert first["feature_id"] == second["feature_id"]
    assert len(stored(store, "account_features")) == 1
    assert len(stored(store, "account_feature_values")) == len(
        account_quality.features(store, HANDLE)["features"])


def test_the_list_keeps_the_newest_runs_confidence_not_the_best_one(tmp_path):
    store = app_store(tmp_path)
    account_quality.record(store, HANDLE, observed_at=1000)
    with store.db(True) as db:
        db.execute("UPDATE account_features SET confidence='ok' WHERE observed_at=1000")
    account_quality.record(store, HANDLE, observed_at=2000)
    with store.db(True) as db:
        db.execute("UPDATE account_features SET confidence='insufficient_data'"
                   " WHERE observed_at=2000")
    rows = {row["handle"]: row for row in account_quality.accounts(store)}
    # "ok" 按字母序排在 "insufficient_data" 后面，所以 max() 会留住一个过期的好
    # 结论；这里要的是最近一次读取的说法。
    assert rows[HANDLE]["confidence"] == "insufficient_data"
    assert rows[HANDLE]["observed_at"] == 2000


def test_an_account_with_only_samples_is_still_listed(tmp_path):
    store = app_store(tmp_path)
    reader = queued(store, handle="chunxiangai")
    answer_profile(reader, handle="chunxiangai")
    rows = {row["handle"]: row for row in account_quality.accounts(store)}
    # 采到了样本但还没算特征：面板要说得出这是「只采到样本」，而不是当它不存在。
    assert rows["chunxiangai"]["observed_at"] is None
    assert rows["chunxiangai"]["confidence"] is None
    assert rows["chunxiangai"]["samples"] == 1
    assert account_quality.sample_counts(store, "chunxiangai")["profile"] == 1


def test_the_account_routes_are_reachable_through_the_pairing_allowlists():
    for method, path in (("GET", "/api/v1/accounts/quality"),
                         ("GET", "/api/v1/accounts/quality/lifesinger"),
                         ("POST", "/api/v1/accounts/quality/read/lifesinger")):
        assert desktop_route(path, method), f"{method} {path}"
    # 路由形状只能走路径参数：连接助手不接受查询串，所以这里的每一条都必须
    # 认路径不认 `?handle=`。读取与详情是两条不同的形状，不能互相冒充。
    assert not desktop_route("/api/v1/accounts/quality/read", "POST")
    assert not desktop_route("/api/v1/accounts/quality/read/lifesinger", "GET")
    assert not desktop_route("/api/v1/accounts/quality/lifesinger/read", "POST")
    # 06C-3 把观察室开成了正式路由（这一条 06C-1 时写的断言现在反过来）。
    assert desktop_route("/api/v1/accounts/watchroom", "GET")
    assert CLOUD_ORIGIN and EXTENSION_ORIGIN


def test_the_routes_answer_for_an_account_that_was_never_read(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    store.set("paused", False)
    token = "account-session"
    with store.db(True) as db:
        db.execute("INSERT INTO sessions(token_hash,csrf,expires) VALUES(?,?,?)",
                   (digest(token), "csrf", int(time.time()) + 600))
    headers = {"cookie": f"studio_session={token}", "x-csrf-token": "csrf"}
    with TestClient(app) as client:
        empty = client.get("/api/v1/accounts/quality", headers=headers)
        assert empty.status_code == 200 and empty.json()["accounts"] == []
        # 门槛跟着列表一起给，免得面板和工作室各写一份。
        assert empty.json()["thresholds"] == {
            "days": account_quality.SAMPLE_WINDOW_DAYS,
            "limit": account_quality.SAMPLE_LIMIT,
            "min_post_sample": account_quality.MIN_POST_SAMPLE,
            "min_reply_sample": account_quality.MIN_REPLY_SAMPLE,
            "reads": account_quality.READ_LABEL}

        # 从没读过的账号也要能打开：面板得先看得到读取计划，才有得点。404 会让
        # 这个账号根本无从下手。
        detail = client.get(f"/api/v1/accounts/quality/{HANDLE}", headers=headers)
        assert detail.status_code == 200
        body = detail.json()
        assert body["run"] is None and body["reads"] == {}
        assert body["samples"] == {kind: 0 for kind in READ_SET}
        assert body["window"]["days"] == account_quality.SAMPLE_WINDOW_DAYS
        assert body["plan"]["reads"]["received"].startswith(f"to:{HANDLE} ")
        assert body["thresholds"]["reads"]["posts"] == "它的原帖"
        # 用户名格式不对是校验拒绝（422），不是「还没读过」。
        assert client.get("/api/v1/accounts/quality/not%20a%20handle",
                          headers=headers).status_code == 422
        assert client.get("/api/v1/accounts/quality/way-too-long-handle",
                          headers=headers).status_code == 422

        started = client.post(f"/api/v1/accounts/quality/read/{HANDLE}",
                              headers=headers).json()
        assert started["handle"] == HANDLE
        assert started["plan"]["reads"]["posts"].startswith(f"from:{HANDLE} ")
        # 同一个账号再点一次是加入第一次，而不是排第二个任务，也不是排第二组读取。
        again = client.post(f"/api/v1/accounts/quality/read/{HANDLE.upper()}",
                            headers=headers).json()
        assert again["task_id"] == started["task_id"]
        assert len(stored(store, "tasks")) == 1
        assert stored(store, "account_reads") == []
