import base64
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from studio.analytics_browser_reads import AnalyticsBrowserReads
from studio.api import create_app
from studio.browser_identity import EXTENSION_ORIGIN
from studio.desktop import CLOUD_ORIGIN, desktop_route
from studio.security import digest


def response_record(days=28):
    now = datetime.now(timezone.utc)
    return {
        "from_time": (now - timedelta(days=days)).isoformat(),
        "to_time": now.isoformat(),
        "max_results": 1000,
        "observed_at": int(now.timestamp()),
        "payload": {"data": {"viewer_v2": {"user_results": {"result": {
            "tweets_results": [{"rest_id": "123", "result": {
                "__typename": "Tweet", "details": {
                    "created_at_ms": int(now.timestamp() * 1000), "full_text": "真实内容样例"},
                "organic_metrics_total": [{"metric_type": "Impressions", "metric_value": 12}],
            }}],
        }}}}},
    }


def ready_job(store, ident="a" * 32, days=28):
    now = int(time.time())
    with store.db(True) as db:
        db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) "
                   "VALUES(?,?,?,?,?,?,?)",
                   (ident, "analytics_content", json.dumps({"days": days}),
                    "running", now, now, now))
        db.execute("INSERT INTO analytics_browser_reads(task_id,account,days,status,updated) "
                   "VALUES(?,?,?,'queued',?)", (ident, "kkplayit", days, now))


def test_authenticated_analytics_response_is_staged_once_without_headers(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    ready_job(store)
    token = "analytics-device-token"
    with store.db(True) as db:
        db.execute("INSERT INTO sessions(token_hash,csrf,expires) VALUES(?,?,?)",
                   (digest(token), "csrf", int(time.time()) + 600))
        db.execute("INSERT INTO desktop_tokens(token_hash,origin) VALUES(?,?)",
                   (digest(token), CLOUD_ORIGIN))
    record = response_record()
    template = record["payload"]["data"]["viewer_v2"]["user_results"]["result"]["tweets_results"][0]
    record["payload"]["data"]["viewer_v2"]["user_results"]["result"]["tweets_results"] = [
        {"rest_id": str(1000000 + index), "result": {
            **template["result"], "details": {**template["result"]["details"],
                                             "full_text": "中文" * 300}}}
        for index in range(600)]
    record["request_headers"] = {"authorization": "must-not-be-stored"}
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/persona-browser",
                                      headers={"origin": EXTENSION_ORIGIN}) as socket:
            socket.send_json({"token": token, "version": "1.3.11"})
            action = socket.receive_json()["action"]
            assert action["kind"] == "analytics_content_read"
            assert action["days"] == 28
            message = {"type": "result", "id": action["id"],
                       "data": {"state": "ready", "account": "kkplayit",
                                "record": record}}
            assert len(json.dumps(message)) > 950_000
            raw = json.dumps(message, ensure_ascii=False).encode("utf-8")
            digest_value = hashlib.sha256(raw).hexdigest()
            parts = [raw[i:i + 98_304] for i in range(0, len(raw), 98_304)]
            for index, part in enumerate(parts):
                socket.send_json({"type": "result_chunk", "id": action["id"],
                                  "index": index, "total": len(parts),
                                  "sha256": digest_value,
                                  "body": base64.b64encode(part).decode("ascii")})
            assert socket.receive_json()["action"] is None
    run = store.rows("SELECT * FROM analytics_runs")[0]
    assert run["id"] == "a" * 32 and run["parsed_count"] == 600
    assert "authorization" not in run["raw_json"]
    assert store.rows("SELECT status FROM analytics_browser_reads")[0]["status"] == "done"


def test_mismatched_window_does_not_create_analytics_run(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    ready_job(store)
    reader = AnalyticsBrowserReads(store)
    action = reader.next_action()
    reader.receive(action["id"], {"state": "ready", "account": "kkplayit",
                                  "record": response_record(90)})
    assert not store.rows("SELECT id FROM analytics_runs")
    row = store.rows("SELECT status,result FROM analytics_browser_reads")[0]
    assert row["status"] == "queued" and json.loads(row["result"])["_retry"] == 1


def test_existing_run_cannot_be_reused_for_another_query_window(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    ready_job(store)
    first = response_record()
    from studio.analytics_store import record_content_page
    record_content_page(store, run_id="a" * 32, account="kkplayit", **{
        key: first[key] for key in ("payload", "from_time", "to_time",
                               "max_results", "observed_at")})
    reader = AnalyticsBrowserReads(store)
    action = reader.next_action()
    another = response_record()
    another["to_time"] = first["to_time"]
    another["from_time"] = (datetime.fromisoformat(first["from_time"]) +
                            timedelta(minutes=1)).isoformat()
    reader.receive(action["id"], {"state": "ready", "account": "kkplayit",
                                  "record": another})
    row = store.rows("SELECT status,result FROM analytics_browser_reads")[0]
    assert row["status"] == "queued"
    assert json.loads(row["result"])["state"] == "source_error"


def test_analytics_api_and_device_allowlist(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as client:
        setup = client.post("/api/v1/auth/setup", json={"password": "fixture-password"})
        client.headers["x-csrf-token"] = setup.json()["csrf"]
        status = client.get("/api/v1/analytics/content")
        assert status.status_code == 200 and status.json()["browser_ready"] is False
        assert client.post("/api/v1/analytics/content/read", json={"days": 28}).status_code == 409
        assert client.post("/api/v1/analytics/content/read", json={"days": 30}).status_code == 422
        with app.state.store.db(True) as db:
            db.execute("INSERT INTO persona_browser_presence VALUES(1,'1.3.11',?)",
                       (int(time.time()),))
        app.state.store.set("paused", False)
        queued = client.post("/api/v1/analytics/content/read", json={"days": 28})
        assert queued.status_code == 200
        assert len(queued.json()["task_id"]) == 32
    assert desktop_route("/api/v1/analytics/content", "GET")
    assert desktop_route("/api/v1/analytics/content/read", "POST")
    assert not desktop_route("/api/v1/analytics/content/read", "GET")
