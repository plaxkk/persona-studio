"""The 06B-1 read path: the server owns the query, the driver brings back pages.

The extension only navigates and harvests; §4.9.1's syntax, the one-year window
and the parsing all live on this side, so these tests drive the two halves --
what `next_action()` hands out and what `receive_search()` accepts back.
"""
import asyncio
import json
import threading
import time
from datetime import timedelta

import pytest

from studio import benchmarks
from studio.api import ERRORS, create_app
from studio.browser_identity import EXTENSION_ORIGIN
from studio.browser_reads import BrowserReads, SEARCH_LIMIT, SEARCH_MINIMUM
from studio.desktop import CLOUD_ORIGIN, desktop_route
from studio.jobs import JobError, Jobs
from studio.xreader import ReadError

HANDLE = "lifesinger"
CREATED = "Wed Apr 15 03:00:37 +0000 2026"


def post(post_id, *, likes=600, replies=15, retweets=162, quotes=24,
         bookmarks=750, views=108597, handle=HANDLE):
    return {
        "__typename": "Tweet", "rest_id": post_id,
        "legacy": {"created_at": CREATED, "full_text": f"第 {post_id} 条",
                   "favorite_count": likes, "reply_count": replies,
                   "retweet_count": retweets, "quote_count": quotes,
                   "bookmark_count": bookmarks},
        "core": {"user_results": {"result": {"core": {
            "screen_name": handle, "name": "Frank Wang 玉伯"}}}},
        "views": ({"state": "EnabledWithCount", "count": str(views)}
                  if views is not None else {"state": "Enabled"}),
    }


def page(*results):
    entries = [{"entryId": f"tweet-{r['rest_id']}", "content": {
        "entryType": "TimelineTimelineItem",
        "itemContent": {"tweet_results": {"result": r}}}} for r in results]
    return {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {
        "instructions": [{"type": "TimelineAddEntries", "entries": entries}]}}}}}


def pages(*results, stopped="exhausted"):
    """What a driver stops on: the items it read, then the page that ended it.

    A spent cursor comes back as a page with no items -- that empty page is the
    only evidence the search actually ran out, so it is part of the result.
    """
    return [page(*results)] + ([page()] if stopped == "exhausted" else [])


def ready(store, ident="a" * 32, *, version="1.4.0", handle=HANDLE, status="running"):
    """A benchmark task with its queue row, as the worker would leave it."""
    now = int(time.time())
    store.set("x_username", "kkplayit")
    with store.db(True) as db:
        db.execute("INSERT OR REPLACE INTO persona_browser_presence VALUES(1,?,?)",
                   (version, now))
        db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) "
                   "VALUES(?,?,?,?,?,?,?)",
                   (ident, "benchmark", json.dumps({"handle": handle}),
                    status, now, now, now))
        db.execute("INSERT INTO search_reads"
                   "(task_id,handle,account,query,status,updated) VALUES(?,?,?,?,?,?)",
                   (ident, handle, "kkplayit",
                    benchmarks.current_query(store, handle), "queued", now))
    return BrowserReads(store)


def delivered(reader, data, ident="a" * 32):
    action = reader.next_action()
    assert action and action["kind"] == "search_read"
    reader.receive(action["id"], data)
    return action


def result_of(store, ident="a" * 32):
    return json.loads(store.rows("SELECT result FROM search_reads WHERE task_id=?",
                                 (ident,))[0]["result"])


def reportable(store, ident="a" * 32):
    return store.rows("SELECT status,result FROM search_reads WHERE task_id=?",
                      (ident,))[0]


def test_the_action_carries_the_requirements_own_query(tmp_path):
    reader = ready(create_app(tmp_path, "http://testserver").state.store)
    action = reader.next_action()
    assert action["kind"] == "search_read" and action["transport"] == "sync"
    assert action["handle"] == HANDLE and action["account"] == "kkplayit"
    assert action["limit"] == SEARCH_LIMIT
    # §4.9.1's syntax and its one-year window, spelled out here so a change to
    # the builder cannot quietly redefine what the connector searches.
    today = benchmarks.today(reader.store)
    assert action["query"] == (
        f"from:{HANDLE} min_faves:500 -filter:replies -filter:retweets "
        f"since:{today - timedelta(days=365)} until:{today}")
    assert action["url"] == benchmarks.search_url(action["query"])
    assert action["url"].endswith("&f=live")  # Latest is the tab that paginates
    assert reader.next_action() is None  # the row is in flight now


def test_a_benchmark_read_needs_a_1_4_connector(tmp_path):
    store = create_app(tmp_path, "http://testserver").state.store
    ready(store, version="1.3.13")
    with pytest.raises(ReadError) as raised:
        asyncio.run(BrowserReads(store).benchmark(
            store.rows("SELECT * FROM tasks")[0], lambda job: True))
    assert raised.value.code == "browser_offline"
    assert SEARCH_MINIMUM == (1, 4, 0)
    # A 1.4.0 connector is not rejected by the same gate.
    with store.db(True) as db:
        db.execute("UPDATE persona_browser_presence SET version='1.4.0' WHERE id=1")
    assert BrowserReads(store).connected(SEARCH_MINIMUM)


def test_a_ready_result_becomes_posts(tmp_path):
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    delivered(reader, {"state": "ready", "account": "kkplayit", "handle": HANDLE,
                       "stopped_reason": "exhausted",
                       "pages": pages(post("2065286424128331927"))})
    rows = benchmarks.posts(store, HANDLE)
    assert [row["post_id"] for row in rows] == ["2065286424128331927"]
    assert rows[0]["metrics_json"]["likes"] == 600
    summary = result_of(store)
    assert summary["parsed"] == 1 and summary["complete"] is True
    assert summary["coverage_gap"] == ""
    assert reportable(store)["status"] == "done"


def test_a_mismatched_handle_is_refused_without_storing_anything(tmp_path):
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    delivered(reader, {"state": "ready", "account": "kkplayit",
                       "handle": "someoneelse",
                       "pages": [page(post("1"))]})
    assert benchmarks.posts(store, HANDLE) == []
    row = reportable(store)
    # The query came from here, so a different author is not something a retry
    # can fix; the read stops and says so instead of looping.
    assert row["status"] == "done"
    assert json.loads(row["result"]) == {"state": "account_mismatch"}


def test_a_read_that_never_arrives_is_retried_twice_then_given_up(tmp_path):
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    for expected in (1, 2):
        delivered(reader, {"state": "waiting_login"})
        row = reportable(store)
        assert row["status"] == "queued"
        assert json.loads(row["result"]) == {"state": "waiting_login",
                                             "_retry": expected}
        with store.db(True) as db:  # the retry backoff has to elapse
            db.execute("UPDATE search_reads SET updated=0")
    delivered(reader, {"state": "waiting_login"})
    row = reportable(store)
    assert row["status"] == "done"
    assert json.loads(row["result"])["_retry"] == 2


def test_a_page_that_answers_a_different_query_is_refused(tmp_path):
    """The response echoes the query; one carrying a different question is not
    this account's data, however well-formed its posts look."""
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    other = page(post("500"))
    other["data"]["search_by_raw_query"]["raw_query"] = "from:someoneelse min_faves:500"
    for _ in range(3):  # a mismatch may be a hiccup, so it is retried first
        delivered(reader, {"state": "ready", "account": "kkplayit", "handle": HANDLE,
                           "stopped_reason": "exhausted", "pages": [other]})
        with store.db(True) as db:
            db.execute("UPDATE search_reads SET updated=0")
    assert benchmarks.posts(store, HANDLE) == []
    row = reportable(store)
    assert row["status"] == "done"  # giving up is a decision, not a hang
    assert json.loads(row["result"]) == {"state": "query_mismatch", "_retry": 2}


def test_stopping_at_the_readers_own_limit_is_recorded_as_a_gap(tmp_path):
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    delivered(reader, {"state": "ready", "account": "kkplayit", "handle": HANDLE,
                       "stopped_reason": "limit",
                       "pages": [page(post("700"), post("701"))]})
    summary = result_of(store)
    assert summary["complete"] is False
    assert f"上限 {SEARCH_LIMIT}" in summary["coverage_gap"]
    # The gap is recorded on the posts too, so the panel can say so per row.
    assert all(row["coverage_gap"] for row in benchmarks.posts(store, HANDLE))


def test_a_withheld_view_count_is_not_stored_as_zero(tmp_path):
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    delivered(reader, {"state": "ready", "account": "kkplayit", "handle": HANDLE,
                       "stopped_reason": "exhausted",
                       "pages": pages(post("800", views=None))})
    row = benchmarks.posts(store, HANDLE)[0]
    assert "views" not in row["metrics_json"]
    assert row["engagement_json"]["unknown"] == ["views"]


def test_an_oversized_result_is_rejected_rather_than_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr("studio.browser_reads.SEARCH_RESULT_BYTES", 600)
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    delivered(reader, {"state": "ready", "account": "kkplayit", "handle": HANDLE,
                       "pages": [page(post(str(index))) for index in range(40)]})
    assert benchmarks.posts(store, HANDLE) == []
    assert json.loads(reportable(store)["result"])["state"] == "source_error"


def test_an_in_flight_search_holds_the_shared_browser_tab(tmp_path):
    """All browser reads share one Chrome tab, so a search must block the rest."""
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    action = reader.next_action()
    assert action["kind"] == "search_read"  # now in flight, owning the tab
    now = int(time.time())
    with store.db(True) as db:
        db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) "
                   "VALUES(?,?,?,'running',?,?,?)",
                   ("b" * 32, "profile_metric", "{}", now, now, now))
        db.execute("INSERT INTO profile_reads(task_id,account,status,updated) "
                   "VALUES(?,?,?,?)", ("b" * 32, "kkplayit", "queued", now))
    assert reader.next_action() is None  # a second read would navigate the tab away
    with store.db(True) as db:  # the search lands, the tab frees up
        db.execute("UPDATE search_reads SET status='done'")
    action = reader.next_action()
    assert action["kind"] == "profile_read" and action["id"].startswith("profile:")


def test_a_paused_studio_does_not_start_an_outward_read(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    now = int(time.time())
    with store.db(True) as db:
        db.execute("INSERT OR REPLACE INTO persona_browser_presence VALUES(1,'1.4.0',?)",
                   (now,))
    store.set("paused", True)
    jobs = Jobs(store)
    with pytest.raises(JobError) as raised:
        jobs.enqueue("benchmark", {"handle": HANDLE}, dedupe="benchmark:" + HANDLE)
    assert raised.value.code == "paused"
    store.set("paused", False)
    assert jobs.enqueue("benchmark", {"handle": HANDLE},
                        dedupe="benchmark:" + HANDLE)


def test_the_job_hands_back_what_the_read_reached(tmp_path):
    """The two halves joined: worker queues the read, connector answers, the
    task reports the summary the panel will show."""
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    reader = ready(store, status="queued")
    store.set("paused", False)
    jobs = Jobs(store)

    def worker():
        asyncio.run(jobs.run(jobs.claim()))

    thread = threading.Thread(target=worker)
    thread.start()
    action = None
    for _ in range(200):
        action = reader.next_action()
        if action:
            break
        time.sleep(0.05)
    assert action and action["kind"] == "search_read"
    reader.receive(action["id"], {"state": "ready", "account": "kkplayit",
                                  "handle": HANDLE, "stopped_reason": "exhausted",
                                  "pages": pages(post("900"))})
    thread.join(20)
    assert not thread.is_alive()
    finished = store.rows("SELECT status,result,error FROM tasks "
                          "WHERE kind='benchmark'")[0]
    assert finished["status"] == "succeeded" and not finished["error"]
    summary = json.loads(finished["result"])
    assert summary["parsed"] == 1 and summary["complete"] is True
    assert [row["post_id"] for row in benchmarks.posts(store, HANDLE)] == ["900"]
    assert reportable(store)["status"] == "done"


def test_the_job_says_what_to_do_instead_of_repeating_a_connector_state(tmp_path):
    """`waiting_login` is not a sentence, and the panel can only render codes it
    knows, so each connector state becomes the message the user needs."""
    store = create_app(tmp_path, "http://testserver").state.store
    expected = {"waiting_login": "needs_login",
                "account_mismatch": "identity_mismatch",
                "waiting_browser": "browser_offline",
                "query_mismatch": "source_error",
                "something_new": "source_error"}
    for state, code in expected.items():
        ident = ("c" * 30) + f"{list(expected).index(state):02d}"
        now = int(time.time())
        store.set("x_username", "kkplayit")
        with store.db(True) as db:
            db.execute("DELETE FROM search_reads WHERE task_id=?", (ident,))
            db.execute("INSERT OR REPLACE INTO persona_browser_presence "
                       "VALUES(1,'1.4.0',?)", (now,))
            db.execute("INSERT INTO search_reads(task_id,handle,account,query,"
                       "status,result,updated) VALUES(?,?,?,?,?,?,?)",
                       (ident, HANDLE, "kkplayit", "", "done",
                        json.dumps({"state": state}), now))
        job = {"id": ident, "kind": "benchmark", "payload": {"handle": HANDLE}}
        with pytest.raises(ReadError) as raised:
            asyncio.run(BrowserReads(store).benchmark(job, lambda job: True))
        assert raised.value.code == code, state
        assert ERRORS[raised.value.code]  # the panel has a sentence for it


def test_the_benchmark_routes_are_reachable_through_the_pairing_allowlists():
    for method, path in (("GET", "/api/v1/benchmarks/accounts"),
                         ("POST", "/api/v1/benchmarks/accounts"),
                         ("GET", "/api/v1/benchmarks/posts/lifesinger"),
                         ("POST", "/api/v1/benchmarks/read/lifesinger")):
        assert desktop_route(path, method), f"{method} {path}"
    assert not desktop_route("/api/v1/benchmarks/posts", "GET")
    assert not desktop_route("/api/v1/benchmarks/posts/lifesinger", "POST")
    assert CLOUD_ORIGIN and EXTENSION_ORIGIN


def test_the_api_lists_accounts_and_their_posts(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    token = "benchmark-session"
    from studio.security import digest
    with app.state.store.db(True) as db:
        db.execute("INSERT INTO sessions(token_hash,csrf,expires) VALUES(?,?,?)",
                   (digest(token), "csrf", int(time.time()) + 600))
    headers = {"cookie": f"studio_session={token}", "x-csrf-token": "csrf"}
    from fastapi.testclient import TestClient
    reader = ready(store)
    delivered(reader, {"state": "ready", "account": "kkplayit", "handle": HANDLE,
                       "stopped_reason": "exhausted",
                       "pages": pages(post("910", likes=500), post("911", likes=499))})
    with TestClient(app) as client:
        accounts = client.get("/api/v1/benchmarks/accounts", headers=headers)
        assert accounts.status_code == 200
        seeded = {row["handle"]: row for row in accounts.json()["accounts"]}
        assert seeded[HANDLE]["posts"] == 2 and seeded[HANDLE]["viral"] == 1
        assert accounts.json()["min_faves"] == 500
        listing = client.get(f"/api/v1/benchmarks/posts/{HANDLE}", headers=headers)
        assert [row["post_id"] for row in listing.json()["posts"]] == ["910", "911"]
        assert listing.json()["posts"][0]["is_viral"] is True
        assert client.get("/api/v1/benchmarks/posts/nobody",
                          headers=headers).status_code == 404
        # A malformed handle never reaches the table, so it is a validation
        # refusal (422), not a "not read yet" (404).
        assert client.get("/api/v1/benchmarks/posts/not%20a%20handle",
                          headers=headers).status_code == 422


def test_a_read_the_driver_cut_short_is_not_complete(tmp_path):
    """Only the driver's word that it reached the end makes a read complete.

    The pages can end on the empty one and the query still not be over: a driver
    that gave up waiting would otherwise be filed as having read the account out.
    """
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    delivered(reader, {"state": "ready", "account": "kkplayit", "handle": HANDLE,
                       "stopped_reason": "timeout",
                       "pages": pages(post("700"))})
    summary = result_of(store)
    assert summary["complete"] is False
    # Not complete, and nothing else to explain why, would render as a blank
    # line -- which reads exactly like a finished read.
    assert "未到结果末尾" in summary["coverage_gap"]
    assert reportable(store)["status"] == "done"


def test_an_unnamed_stop_reason_still_leaves_the_read_open(tmp_path):
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    delivered(reader, {"state": "ready", "account": "kkplayit", "handle": HANDLE,
                       "stopped_reason": {"unexpected": True},
                       "pages": pages(post("701"))})
    summary = result_of(store)
    assert summary["stopped_reason"] == ""
    assert summary["complete"] is False and summary["coverage_gap"]


def test_a_read_that_raised_the_tab_says_so(tmp_path):
    """The driver may borrow the foreground to keep X paginating.

    That it did is part of the read's record: a studio that works in someone's
    browser owes them an answer for why the page jumped to the front.
    """
    store = create_app(tmp_path, "http://testserver").state.store
    reader = ready(store)
    delivered(reader, {"state": "ready", "account": "kkplayit", "handle": HANDLE,
                       "stopped_reason": "exhausted", "promoted": True,
                       "pages": pages(post("702"))})
    summary = result_of(store)
    assert summary["foregrounded"] is True and summary["complete"] is True
