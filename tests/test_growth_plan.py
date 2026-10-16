import asyncio
import time
import json

import httpx

from fastapi.testclient import TestClient

from studio.api import create_app
from studio.desktop import desktop_route
from studio.growth_browser_reads import GrowthBrowserReads, reward_metrics
from studio.browser_reads import BrowserReads
from studio.supabase_analytics import sync_one_growth_snapshot


def _client(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    client = TestClient(app)
    client.__enter__()
    auth = client.post("/api/v1/auth/setup", json={"password": "growth-test-password"}).json()
    client.headers["x-csrf-token"] = auth["csrf"]
    app.state.store.set("x_username", "kkplayit")
    return app, client


def test_milestone_unknown_stale_and_durable_completion(tmp_path):
    app, client = _client(tmp_path)
    try:
        store = app.state.store
        page = client.get("/api/v1/growth/plan").json()
        assert [row["target"] for row in page["milestones"]] == [500, 1000, 3000, 5000, 10000]
        assert page["profile"] is None and page["milestones"][0]["progress"] is None
        now = int(time.time())
        with store.db(True) as db:
            db.execute("INSERT INTO profile_snapshots(account,observed_at,day,followers,source) VALUES(?,?,?,?,?)",
                       ("kkplayit", now - 120, "2026-10-16", 120, "test"))
        page = client.get("/api/v1/growth/plan").json()
        assert page["milestones"][0]["progress"] == 0
        first = page["milestones"][0]
        assert client.put(f"/api/v1/growth/milestones/{first['id']}",
                          json={"target": 550, "deadline": "2026-12-31"}).status_code == 200
        with store.db(True) as db:
            db.execute("UPDATE profile_snapshots SET followers=600 WHERE account='kkplayit'")
        reached = client.get("/api/v1/growth/plan").json()
        assert reached["milestones"][0]["completed_at"] is not None
        assert reached["milestones"][1]["remaining"] == 400
        with store.db(True) as db:
            db.execute("UPDATE profile_snapshots SET followers=510 WHERE account='kkplayit'")
        fallen = client.get("/api/v1/growth/plan").json()
        assert fallen["milestones"][0]["completed_at"] is not None
        assert fallen["milestones"][0]["remaining"] == 40
    finally:
        client.__exit__(None, None, None)


def test_week_plan_confirmation_checkin_and_experiment(tmp_path):
    app, client = _client(tmp_path)
    try:
        page = client.get("/api/v1/growth/plan").json()
        assert page["plan"] is None
        plan = client.post("/api/v1/growth/weeks/suggest", json={}).json()
        assert plan["status"] == "suggested" and plan["actions"] == []
        assert client.post("/api/v1/growth/weeks/suggest", json={}).json()["id"] == plan["id"]
        endpoint = f"/api/v1/growth/weeks/{plan['id']}"
        assert client.put(endpoint, json={"actions": [], "confirm": True}).status_code == 422
        action = {"title": "回复一位同阶段开发者", "problem": "讨论不足", "measure": "有价值的回复", "effort": "20 分钟", "evidence": "上周周报"}
        assert client.put(endpoint, json={"actions": [action] * 4, "confirm": True}).status_code == 422
        confirmed = client.put(endpoint, json={"actions": [action], "confirm": True}).json()
        assert confirmed["plan"]["status"] == "confirmed"
        action_id = confirmed["plan"]["actions"][0]["id"]
        check = {"day": page["today"], "kind": "reply", "note": "已回复并补充项目经验", "action_id": action_id, "minutes": 20}
        assert client.post("/api/v1/growth/checkins", json=check).json()["checkins"][0]["action_title"] == action["title"]
        assert client.post("/api/v1/growth/experiments", json={"hypothesis": "换开头", "metric": "72 小时主页访问", "baseline": "上一组帖子"}).status_code == 200
        assert client.post("/api/v1/growth/experiments", json={"hypothesis": "再换标题", "metric": "曝光"}).status_code == 409
        active = client.get("/api/v1/growth/plan").json()["experiments"][0]
        finished = client.put(f"/api/v1/growth/experiments/{active['id']}", json={"result": "只有 2 条帖子", "decision": "继续使用"}).json()
        assert finished["experiments"][0]["conclusion_ready"] is False
        assert finished["experiments"][0]["result"]["early"] is True
    finally:
        client.__exit__(None, None, None)


def test_growth_routes_bound_to_desktop():
    for method, path in (("GET", "/growth/plan"), ("POST", "/growth/weeks/suggest"),
                         ("POST", "/growth/checkins"), ("POST", "/growth/experiments"),
                         ("PUT", "/growth/milestones/1"), ("PUT", "/growth/weeks/" + "a" * 32),
                         ("PUT", "/growth/experiments/" + "a" * 32)):
        assert desktop_route("/api/v1" + path, method)
    assert not desktop_route("/api/v1/growth/experiments/../../../settings", "PUT")


def test_reward_failure_keeps_history_and_follower_bands_are_cumulative(tmp_path):
    app, client = _client(tmp_path)
    try:
        store = app.state.store
        stamp = int(time.time())
        with store.db(True) as db:
            db.execute("INSERT INTO persona_browser_presence VALUES(1,'1.4.7',?)", (stamp,))
            for ident, kind in (("a" * 32, "rewards"), ("b" * 32, "followers")):
                db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) "
                           "VALUES(?,?,?,'running',?,?,?)",
                           (ident, "growth_" + kind, "{}", stamp, stamp, stamp))
                db.execute("INSERT INTO growth_browser_reads VALUES(?,?,?,'sent','{}',?)",
                           (ident, "kkplayit", kind, stamp))
        reader = GrowthBrowserReads(store)
        lines = ["Original content rewards", "Have at least 500 Verified followers", "120"]
        assert reward_metrics(lines)[0]["current_display"] == "120"
        reader.receive("growth:" + "a" * 32, {"state": "ready", "account": "kkplayit",
            "page_status": "check_back_later", "period": None, "lines": lines,
            "raw_text": "\n".join(lines)})
        assert client.get("/api/v1/growth/plan").json()["rewards"][0]["metrics"][0]["current_display"] == "120"
        reader.receive("growth:" + "b" * 32, {"state": "ready", "account": "kkplayit",
            "discovered": 3, "stopped_reason": "bounded_sample", "records": [
                {"handle": "one", "followers": 12000, "followers_display": "12,000 Followers", "verified": True},
                {"handle": "two", "followers": 3200, "followers_display": "3,200 Followers", "verified": False},
                {"handle": "three", "followers": None, "followers_display": "1.2K Followers", "verified": True}]})
        page = client.get("/api/v1/growth/plan").json()
        assert page["follower_observation"]["bands"] == {"1000": 2, "3000": 2, "5000": 1, "10000": 1}
        assert page["follower_observation"]["verified_observed"] == 2
        assert page["follower_observation"]["exact_counts"] == 2
        assert client.put("/api/v1/growth/relationships/one", json={"handle": "one", "note": "有相同的开发经验", "next_step": "下次回复"}).status_code == 200
        assert client.put("/api/v1/growth/relationships/other", json={"handle": "one", "note": "test"}).status_code == 422
        with store.db(True) as db:
            failed_id = "c" * 32
            db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) "
                       "VALUES(?,'growth_rewards','{}','running',?,?,?)", (failed_id, stamp, stamp, stamp))
            db.execute("INSERT INTO growth_browser_reads VALUES(?,'kkplayit','rewards','sent','{}',?)",
                       (failed_id, stamp))
        reader.receive("growth:" + failed_id, {"state": "waiting_login"})
        page = client.get("/api/v1/growth/plan").json()
        assert len(page["rewards"]) == 1
        assert page["relationships"][0]["handle"] == "one"
        with store.db(True) as db:
            empty_id = "d" * 32
            db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) "
                       "VALUES(?,'growth_followers','{}','running',?,?,?)", (empty_id, stamp, stamp, stamp))
            db.execute("INSERT INTO growth_browser_reads VALUES(?,'kkplayit','followers','sent','{}',?)",
                       (empty_id, stamp))
        reader.receive("growth:" + empty_id, {"state": "ready", "account": "kkplayit",
            "discovered": 0, "stopped_reason": "bounded_sample", "records": []})
        result = store.rows("SELECT result FROM growth_browser_reads WHERE task_id=?", (empty_id,))[0]
        assert json.loads(result["result"])["state"] == "source_error"
        assert store.rows("SELECT count(*) AS n FROM growth_follower_reads")[0]["n"] == 1
    finally:
        client.__exit__(None, None, None)


def test_new_growth_browser_routes_reject_unknown_methods():
    for method, path in (("POST", "/growth/rewards/read"), ("POST", "/growth/followers/read"),
                         ("PUT", "/growth/relationships/lifesinger")):
        assert desktop_route("/api/v1" + path, method)
    assert not desktop_route("/api/v1/growth/rewards/read", "GET")


def test_dispatched_follower_read_can_finish_after_presence_lease_expires(tmp_path, monkeypatch):
    app, client = _client(tmp_path)
    try:
        store = app.state.store
        task_id = "d" * 32
        stamp = int(time.time())
        with store.db(True) as db:
            db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) "
                       "VALUES(?,'growth_followers','{}','running',?,?,?)",
                       (task_id, stamp, stamp, stamp))
        calls = 0

        def connected(_self, _minimum):
            nonlocal calls
            calls += 1
            return calls == 1

        monkeypatch.setattr(BrowserReads, "connected", connected)
        reader = GrowthBrowserReads(store)

        async def run():
            pending = asyncio.create_task(reader.read({"id": task_id, "kind": "growth_followers"},
                                                      lambda _job: True))
            await asyncio.sleep(0.01)
            with store.db(True) as db:
                db.execute("UPDATE growth_browser_reads SET status='sent' WHERE task_id=?", (task_id,))
            reader.receive("growth:" + task_id, {"state": "ready", "account": "kkplayit",
                "discovered": 1, "stopped_reason": "bounded_sample", "records": [
                    {"handle": "reader", "followers": 1200, "followers_display": "1,200 Followers",
                     "verified": False}]})
            return await pending

        assert asyncio.run(run())["read_count"] == 1
        assert calls == 1
    finally:
        client.__exit__(None, None, None)


def test_false_empty_follower_read_is_removed_from_coverage_on_migration(tmp_path):
    app, client = _client(tmp_path)
    try:
        store = app.state.store
        stamp = int(time.time())
        task_id = "e" * 32
        with store.db(True) as db:
            db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) "
                       "VALUES(?,'growth_followers','{}','succeeded',?,?,?)",
                       (task_id, stamp, stamp, stamp))
            db.execute("INSERT INTO growth_browser_reads VALUES(?,'kkplayit','followers','done',?,?)",
                       (task_id, '{\"state\":\"ready\"}', stamp))
            db.execute("INSERT INTO growth_follower_reads(task_id,account,observed_at,discovered,"
                       "read_count,stopped_reason) VALUES(?,'kkplayit',?,0,0,'bounded_sample')",
                       (task_id, stamp))
            db.execute("DELETE FROM migrations WHERE version=17")
        store.migrate()
        page = client.get("/api/v1/growth/plan").json()
        assert page["follower_observation"]["latest_read"] is None
        assert page["growth_read_tasks"][0]["status"] == "failed"
        assert store.rows("SELECT cloud_status FROM growth_follower_reads")[0]["cloud_status"] == "skip"
    finally:
        client.__exit__(None, None, None)


def test_growth_cloud_retries_without_losing_local_snapshot(tmp_path):
    app, client = _client(tmp_path)
    try:
        store = app.state.store
        with store.db(True) as db:
            db.execute("INSERT INTO growth_reward_snapshots "
                       "(id,account,observed_at,page_status,metrics_json,raw_json,source) "
                       "VALUES('reward-1','kkplayit',1000,'check_back_later',?,?,?)",
                       (json.dumps([{"label": "followers", "current_display": "120"}]),
                        json.dumps({"text": "120"}), "x_logged_in_browser"))
        env = {"SUPABASE_URL": "https://example.supabase.co",
               "SUPABASE_SECRET_KEY": "sb_secret_test_key_only_in_local_process"}
        seen = []
        def respond(request):
            seen.append(json.loads(request.content)[0])
            return httpx.Response(503 if len(seen) == 1 else 204)
        with httpx.Client(transport=httpx.MockTransport(respond)) as mock:
            assert sync_one_growth_snapshot(store, environ=env, client=mock, now=1000)["status"] == "retry"
            assert sync_one_growth_snapshot(store, environ=env, client=mock, now=1029)["status"] == "idle"
            assert sync_one_growth_snapshot(store, environ=env, client=mock, now=1030)["status"] == "synced"
        assert len(seen) == 2 and seen[0] == seen[1]
        assert seen[0]["metrics"][0]["current_display"] == "120"
        assert store.rows("SELECT cloud_status FROM growth_reward_snapshots")[0]["cloud_status"] == "synced"
    finally:
        client.__exit__(None, None, None)
