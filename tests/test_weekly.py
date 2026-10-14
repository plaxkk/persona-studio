"""The weekly diagnosis page reads only what the studio actually stored.

Section 4.8 lets missing data be named but never filled in. These tests pin the
two ways that could go wrong: grouping replies by a field that is not in the
payload, and turning an absent number into a zero.
"""
import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from studio import weekly
from studio.analytics_store import record_content_page
from studio.api import create_app
from studio.jobs import Jobs

ZONE = ZoneInfo("Asia/Shanghai")
WEEK = "2026-10-12"  # a Monday
SUNDAY_POST = int(datetime(2026, 10, 18, 20, 0, tzinfo=ZONE).timestamp() * 1000)
LAST_WEEK_POST = int(datetime(2026, 10, 5, 9, 0, tzinfo=ZONE).timestamp() * 1000)


def at(day, hour=0):
    """A local time in the report's timezone, in analytics milliseconds."""
    return int(datetime.fromisoformat(day).replace(tzinfo=ZONE).timestamp() * 1000
               + hour * 3600 * 1000)


def post(post_id, published_at_ms, text, metrics=None, reply=False):
    item = {"post_id": post_id, "published_at_ms": published_at_ms, "text": text,
            "metrics": metrics or {}}
    if reply:
        item["reply"] = True
    return item


def payload_for(items):
    results = []
    for item in items:
        result = {
            "__typename": "Tweet",
            "details": {"created_at_ms": item["published_at_ms"], "full_text": item["text"]},
            "organic_metrics_total": [
                {"metric_type": name, "metric_value": value}
                for name, value in item["metrics"].items() if value is not None
            ],
        }
        if item.get("reply"):
            # Observed shape: a dict holding the parent post, absent otherwise.
            result["reply_to_results"] = {"id": "1", "rest_id": "1"}
        results.append({"rest_id": item["post_id"], "result": result})
    return {"data": {"viewer_v2": {"user_results": {"result": {"tweets_results": results}}}}}


def stage(store, run_id, items, observed_ms=at("2026-10-18", 12),
          window=("2026-10-12", "2026-10-18")):
    """Store one Content read whose window covers the report's week, so the run
    itself is in range and not only the posts inside it."""
    iso = [datetime.fromisoformat(day).replace(tzinfo=ZONE).isoformat() for day in window]
    return record_content_page(
        store, run_id=run_id, account="kkplayit", payload=payload_for(items),
        from_time=iso[0], to_time=iso[1], max_results=1000,
        observed_at=observed_ms // 1000)


def boundary_start(store, followers):
    """The last daily snapshot before Monday 00:00 that opens the week."""
    snapshot(store, "2026-10-11", followers, at("2026-10-11", 23))


def boundary_end(store, followers):
    """The last daily snapshot before Monday 00:00 that closes the week.

    A reading taken after the boundary belongs to the next week; the report
    only ever looks backwards from an endpoint.
    """
    snapshot(store, "2026-10-18", followers, at("2026-10-18", 23))


def snapshot(store, day, followers, observed_ms=None):
    """A homepage follower count. `observed_ms` is analytics milliseconds; the
    table stores epoch seconds, like everything outside analytics."""
    stamp = int(observed_ms if observed_ms is not None else at(day, 12))
    with store.db(True) as db:
        db.execute("INSERT INTO profile_snapshots(account,observed_at,day,followers,"
                   "bio,pinned_tweet_id,source) VALUES(?,?,?,?,?,NULL,'browser') "
                   "ON CONFLICT(account,day,source) DO UPDATE SET "
                   "observed_at=excluded.observed_at,followers=excluded.followers",
                   ("kkplayit", stamp // 1000, day, followers, ""))


def report_for(store, now_ms, week_start=WEEK):
    return weekly.generate(store, account="kkplayit", week_start=week_start, now_ms=now_ms)["report_id"]


def facts_of(store, now_ms, week_start=WEEK):
    week = weekly.resolve_week(store, now_ms, week_start)
    return weekly.collect(store, account="kkplayit", week_start=week, now_ms=now_ms)


def test_week_boundaries_are_mondays_in_the_configured_timezone():
    store = _app_store()
    # 2026-10-14 is a Wednesday; the last finished week is 10-05.
    now = at("2026-10-14", 9)
    assert weekly.last_complete_week(now, ZONE).isoformat() == "2026-10-05"
    assert weekly.resolve_week(store, now, None).isoformat() == "2026-10-05"
    assert weekly.resolve_week(store, now, "2026-10-12").isoformat() == "2026-10-12"
    for bad in ("2026-10-13", "2026-10-1", "13/10/2026", ""):
        if bad == "":
            continue  # empty means "the last finished week", not an error
        with pytest.raises(ValueError):
            weekly.resolve_week(store, now, bad)
    start, end = weekly.week_bounds(datetime.fromisoformat(WEEK).date(), ZONE)
    assert start == at(WEEK, 0) and end == at("2026-10-19", 0)
    assert end - start == 7 * 86400 * 1000


def _app_store():
    import tempfile
    from pathlib import Path
    root = Path(tempfile.mkdtemp())
    app = create_app(root, "http://testserver")
    return app.state.store


def test_replies_are_separated_from_the_rest_and_older_rows_stay_unclassified():
    store = _app_store()
    stage(store, "run" + "a" * 20, [
        post("111", SUNDAY_POST, "原创的想法", {"Impressions": 100, "ProfileVisits": 2}),
        post("222", SUNDAY_POST, "@someone 我的回复", {"Impressions": 10}, reply=True),
        post("333", LAST_WEEK_POST, "上周的帖子", {"Impressions": 50}),
    ], at("2026-10-19", 1))
    with store.db(True) as db:
        # A row staged before migration 10 has no kind; it must not be counted
        # as a post just because it is not a reply.
        db.execute("INSERT INTO analytics_post_metrics"
                   "(run_id,post_id,published_at_ms,text,metrics_json,kind) "
                   "VALUES(?,?,?,?,?,NULL)",
                   ("run" + "a" * 20, "444", SUNDAY_POST, "旧的没分类", "{}"))
    facts = facts_of(store, at("2026-10-20", 9))
    assert facts["posts"]["total"] == 3  # 444 is outside no window filter, but same run window
    assert (facts["posts"]["post"], facts["posts"]["reply"]) == (1, 1)
    assert facts["posts"]["unclassified"] == 1
    assert facts["posts"]["metrics"]["Impressions"]["total"] == 110
    assert facts["posts"]["metrics"]["Impressions"]["known"] == 2


def test_bookmarks_read_the_parser_key_and_an_absent_one_stays_unseen():
    """The parser writes the singular "Bookmark". A key that does not match reads
    as 看不到 for every post, which looks exactly like a real gap."""
    store = _app_store()
    stage(store, "run" + "d" * 20, [
        post("111", SUNDAY_POST, "有收藏数的一条", {"Impressions": 10, "Bookmark": 4}),
        post("112", SUNDAY_POST, "没有收藏数的一条", {"Impressions": 10}),
    ])
    facts = facts_of(store, at("2026-10-20", 9))
    bookmarks = facts["posts"]["metrics"]["Bookmark"]
    assert bookmarks["total"] == 4
    assert (bookmarks["known"], bookmarks["counted"]) == (1, 2)
    # The stored counts are REAL; an exact integer is shown as an integer.
    assert isinstance(facts["posts"]["metrics"]["Impressions"]["total"], int)
    assert facts["posts"]["metrics"]["Impressions"]["total"] == 20


def test_posts_that_all_predate_the_classification_say_so_instead_of_zero():
    store = _app_store()
    stage(store, "run" + "e" * 20, [post("111", SUNDAY_POST, "一条", {"Impressions": 7})])
    with store.db(True) as db:
        db.execute("UPDATE analytics_post_metrics SET kind=NULL WHERE post_id='111'")
    facts = facts_of(store, at("2026-10-20", 9))
    assert facts["posts"]["unclassified"] == 1
    report = weekly.load(store, report_for(store, at("2026-10-20", 9)))["report"]
    line = next(item for item in report["observed"] if "条推文" in item)
    assert "非回复 0 条" not in line and "回复 0 条" not in line
    assert "都早于本次分类" in line
    assert any("下一次内容读取" in item for item in report["unavailable"])


def test_a_missing_snapshot_is_unseen_never_zero():
    store = _app_store()
    stage(store, "run" + "b" * 20, [
        post("111", SUNDAY_POST, "原创的想法", {"Impressions": 100}),
        post("112", SUNDAY_POST, "第二条", {"Impressions": 20}),
        post("113", SUNDAY_POST, "第三条", {"Impressions": 5}),
        post("114", SUNDAY_POST, "第四条", {"Impressions": 5}),
        post("115", SUNDAY_POST, "第五条", {"Impressions": 5}),
    ], at("2026-10-19", 1))
    facts = facts_of(store, at("2026-10-20", 9))
    assert facts["followers"]["net"] is None
    assert "主页粉丝快照" in facts["followers"]["reason"]
    stored = weekly.load(store, report_for(store, at("2026-10-20", 9)))
    assert stored["data_quality"] == weekly.QUALITY_LOW
    report = stored["report"]
    assert any("粉丝净增" in line for line in report["unavailable"])
    # No claim was invented out of the absent followers number.
    assert not any("净增" in line for line in report["observed"])
    assert not any("净增" in line for line in [item["evidence"] for item in stored["issues"]])
    assert stored["facts"]["followers"]["net"] is None


def test_a_stale_boundary_snapshot_is_refused_rather_than_interpolated():
    store = _app_store()
    snapshot(store, "2026-10-05", 120)  # a week before the boundary it must serve
    boundary_end(store, 130)
    facts = facts_of(store, at("2026-10-20", 9))
    assert facts["followers"]["start"]["stale"] is True
    assert facts["followers"]["start"]["value"] is None
    assert facts["followers"]["net"] is None


def test_net_change_is_computed_when_both_ends_are_real():
    store = _app_store()
    boundary_start(store, 100)
    boundary_end(store, 104)
    facts = facts_of(store, at("2026-10-20", 9))
    assert facts["followers"]["net"] == 4
    assert facts["followers"]["start"]["value"] == 100
    assert facts["followers"]["end"]["value"] == 104


def test_the_first_thing_to_look_at_fires_only_with_both_numbers():
    store = _app_store()
    boundary_start(store, 100)
    boundary_end(store, 100)
    stage(store, "run" + "c" * 20, [
        post(str(1000 + index), SUNDAY_POST, f"第 {index} 条", {"Impressions": 400, "ProfileVisits": 3})
        for index in range(5)
    ])
    report = weekly.load(store, report_for(store, at("2026-10-20", 9)))["report"]
    assert [item["code"] for item in report["issues"]] == ["readers_vs_profile"]
    issue = report["issues"][0]
    assert "2000 次展示" in issue["evidence"] and "净增 +0" in issue["evidence"]
    # Only the evidence and the one action can be filled from local data.
    assert issue["benchmark"] == "" and issue["hypothesis"] == "" and issue["alternative"] == ""
    assert report["actions"] == [issue["action"]] and len(report["actions"]) <= 3
    assert any("06B" in line for line in report["unavailable"])

    # Same week, growing account: the same code path must stay silent.
    boundary_end(store, 130)
    report = weekly.load(store, report_for(store, at("2026-10-20", 9)))["report"]
    assert report["issues"] == [] and report["actions"] == []
    assert report["priority_note"]


def test_an_empty_week_is_insufficient_data_and_says_so():
    store = _app_store()
    report = weekly.load(store, report_for(store, at("2026-10-20", 9)))
    assert report["data_quality"] == weekly.QUALITY_INSUFFICIENT
    assert report["report"]["observed"] == []
    assert any("没有订阅分析读数" in line for line in report["report"]["unavailable"])
    assert report["facts"]["posts"]["total"] == 0


def test_the_api_generates_lists_and_reads_one_report():
    app = create_app(_tmp_root(), "http://testserver")
    # Relative to the real clock, so the "week has not ended" gate is exercised
    # against the same comparison the route makes.
    now_ms = int(time.time() * 1000)
    done = weekly.last_complete_week(now_ms, ZONE)
    current = weekly.week_start_of(datetime.fromtimestamp(now_ms / 1000, ZONE).date())
    with TestClient(app) as client:
        setup = client.post("/api/v1/auth/setup", json={"password": "testing-password-123"})
        client.headers["x-csrf-token"] = setup.json()["csrf"]
        store, jobs = app.state.store, app.state.jobs
        store.set("x_username", "kkplayit")
        generate = "/api/v1/reports/weekly/generate"
        # A Tuesday is not a week start at all.
        assert client.post(generate,
                           json={"week_start": (current + timedelta(days=1)).isoformat()}).status_code == 422
        # This week is still running, so it cannot be reported on yet.
        assert client.post(generate, json={"week_start": current.isoformat()}).status_code == 422
        assert client.post(generate, json={"week_start": done.isoformat()}).status_code == 200
        job = jobs.claim()
        assert job["kind"] == "weekly"
        asyncio.run(jobs.run(job))
        report_id = weekly.report_id("kkplayit", done)
        listing = client.get("/api/v1/reports/weekly").json()
        assert listing["account"] == "kkplayit"
        assert listing["latest_week_start"] == done.isoformat()
        assert [item["id"] for item in listing["reports"]] == [report_id]
        detail = client.get("/api/v1/reports/weekly/" + report_id).json()
        assert detail["week_start"] == done.isoformat()
        assert detail["week_end"] == (done + timedelta(days=6)).isoformat()
        assert detail["report"]["method"].startswith("周界＝ISO 周")
        # An honest reading of a week with no stored readings, not a zero.
        assert detail["report"]["observed"] == []
        assert detail["data_quality"] == weekly.QUALITY_INSUFFICIENT
        previous = weekly.report_id("kkplayit", done - timedelta(days=7))
        assert client.get("/api/v1/reports/weekly/" + previous).status_code == 404
        # Paused or not, the report is local work: the studio defaults to paused
        # and the job above still ran.
        assert store.get("paused") is not False


def _tmp_root():
    import tempfile
    from pathlib import Path
    return Path(tempfile.mkdtemp())


def test_the_bridge_reaches_the_report_routes_and_nothing_wider():
    from studio.desktop import desktop_route

    report = "/api/v1/reports/weekly"
    for path, method in [(report, "GET"), (report + "/generate", "POST"),
                         (report + "/w20261012-kkplayit", "GET")]:
        assert desktop_route(path, method), path
    for path, method in [("/api/v1/reports", "GET"), (report, "POST"),
                         (report + "/w20261012-kkplayit", "POST"),
                         (report + "/w20261012-kkplayit", "DELETE"),
                         (report + "/w20261012-kkplayit/../settings", "GET"),
                         (report + "/a b", "GET"), (report + "?week=2026-10-12", "GET")]:
        assert not desktop_route(path, method), f"{path} {method}"


def test_weekly_scheduling_runs_once_after_monday_morning():
    store = _app_store()
    store.set("x_username", "kkplayit")
    store.set("paused", False)
    jobs = Jobs(store)
    # Monday 01:00 is too early: the day's own content read has not caught up.
    jobs.schedule_weekly(at("2026-10-12", 1) // 1000, "kkplayit")
    assert store.rows("SELECT id FROM tasks WHERE kind='weekly'") == []
    jobs.schedule_weekly(at("2026-10-12", 4) // 1000, "kkplayit")
    tasks = store.rows("SELECT id,dedupe FROM tasks WHERE kind='weekly'")
    assert len(tasks) == 1 and tasks[0]["dedupe"] == "weekly:2026-10-05"
    jobs.schedule_weekly(at("2026-10-12", 5) // 1000, "kkplayit")
    tasks = store.rows("SELECT id FROM tasks WHERE kind='weekly'")
    assert len(tasks) == 1
    with store.db(True) as db:
        db.execute("UPDATE tasks SET status='failed',created=? WHERE id=?", (at("2026-10-12", 4) // 1000, tasks[0]["id"]))
    jobs.schedule_weekly(at("2026-10-12", 6) // 1000, "kkplayit")
    assert len(store.rows("SELECT id FROM tasks WHERE kind='weekly'")) == 1
    jobs.schedule_weekly(at("2026-10-13", 6) // 1000, "kkplayit")
    assert len(store.rows("SELECT id FROM tasks WHERE kind='weekly'")) == 2


def test_a_weekly_task_runs_while_the_studio_is_paused():
    store = _app_store()
    store.set("x_username", "kkplayit")
    store.set("paused", True)
    jobs = Jobs(store)
    jobs.enqueue("weekly", {"week_start": WEEK})
    job = jobs.claim()
    assert job is not None and job["kind"] == "weekly"
    assert jobs.alive(job)
    asyncio.run(jobs.run(job))
    assert store.rows("SELECT status FROM tasks WHERE kind='weekly'")[0]["status"] == "succeeded"
