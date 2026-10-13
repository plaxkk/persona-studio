import json

import httpx
from fastapi.testclient import TestClient

from studio.analytics_store import record_content_page
from studio.analytics_overview_store import record_overview
from studio.api import create_app
from studio.desktop import desktop_route
from studio.store import Store
from studio.supabase_analytics import sync_one_content_run, sync_one_overview_run


def test_overview_delivery_retries_points_and_keeps_series_separate(tmp_path):
    store = Store(tmp_path)
    record_overview(store, run_id="overview-run-x", account="owner",
                    payload={"data": {"viewer_v2": {"user_results": {"result": {
                        "current_time_series": [{"timestamp": 1791763200000,
                                                 "engagement_type": "Displayed", "count": 3}],
                        "hourly_backfill": [{"timestamp": 1791763200000,
                                             "engagement_type": "Displayed", "count": 2}],
                        "previous_totals": [], "legacy_current_follow_metrics": [],
                        "legacy_previous_follow_metrics": [],
                        "relationship_counts": {"followers": 501},
                    }}}}}, from_time="2026-09-16T00:00:00Z",
                    to_time="2026-10-14T00:00:00Z",
                    prev_from="2026-08-19T00:00:00Z",
                    prev_to="2026-09-16T00:00:00Z", observed_at=1000)
    env = {"SUPABASE_URL": "https://example.supabase.co",
           "SUPABASE_SECRET_KEY": "sb_secret_test_key_only_in_local_process"}
    seen = []
    def respond(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(503 if len(seen) == 2 else 204)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert sync_one_overview_run(store, environ=env, client=client, now=1000)["status"] == "retry"
        assert sync_one_overview_run(store, environ=env, client=client, now=1030)["status"] == "synced"
    assert len(seen) == 4
    assert [point["series"] for point in seen[3][1]] == ["current_time_series", "hourly_backfill"]
    assert [point["count"] for point in seen[3][1]] == [3, 2]
    assert store.rows("SELECT cloud_status FROM analytics_overview_runs")[0]["cloud_status"] == "synced"


def test_supabase_delivery_retries_partial_write_without_duplicate_rows(tmp_path):
    store = Store(tmp_path)
    payload = {"data": {"viewer_v2": {"user_results": {"result": {"tweets_results": [
        {"rest_id": "123", "result": {"__typename": "Tweet", "details": {
            "created_at_ms": 1234567890000, "full_text": "A post"},
            "organic_metrics_total": [{"metric_type": "Impressions", "metric_value": 3}]}},
        {"rest_id": "124", "result": {"__typename": "TweetWithVisibilityResults"}},
    ]}}}}}
    record_content_page(store, run_id="content-run-x", account="owner", payload=payload,
                        from_time="2026-07-16T00:00:00Z", to_time="2026-10-13T23:59:59Z",
                        max_results=1000, observed_at=1000)
    env = {"SUPABASE_URL": "https://example.supabase.co",
           "SUPABASE_SECRET_KEY": "sb_secret_test_key_only_in_local_process"}
    seen = []
    def respond(request):
        seen.append((request.url.path, dict(request.headers), json.loads(request.content)))
        if request.url.path.endswith("persona_analytics_post_metrics") and len(seen) == 2:
            return httpx.Response(503)
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        first = sync_one_content_run(store, environ=env, client=client, now=1000)
        assert first == {"status": "retry", "run_id": "content-run-x",
                         "error": "http_503", "retry_at": 1030}
        assert sync_one_content_run(store, environ=env, client=client, now=1029) == {"status": "idle"}
        second = sync_one_content_run(store, environ=env, client=client, now=1030)
    assert second == {"status": "synced", "run_id": "content-run-x",
                      "post_count": 1, "gap_count": 1}
    assert len(seen) == 4
    assert seen[0][2][0]["coverage_gaps"][0]["reason"] == "visibility_restricted"
    assert seen[1][2][0]["metrics"]["Impressions"] == 3
    assert all(headers.get("apikey") == env["SUPABASE_SECRET_KEY"] and "authorization" not in headers
               for _, headers, _ in seen)
    assert store.rows("SELECT cloud_status,cloud_attempts,cloud_error FROM analytics_runs")[0] == {
        "cloud_status": "synced", "cloud_attempts": 1, "cloud_error": ""}
    assert env["SUPABASE_SECRET_KEY"] not in store.path.read_bytes().decode("utf-8", errors="ignore")


def test_supabase_missing_config_does_not_consume_pending_run(tmp_path):
    store = Store(tmp_path)
    assert sync_one_content_run(store, environ={}) == {"status": "not_configured"}


def test_supabase_secret_stays_local_and_project_switch_requires_disconnect(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as client:
        setup = client.post("/api/v1/auth/setup", json={"password": "testing-password-123"}).json()
        client.headers["x-csrf-token"] = setup["csrf"]
        secret = "sb_secret_example_private_local_key"
        base = "/api/v1/connections/supabase"
        assert client.put(base, json={"url": "https://not-supabase.example", "secret_key": secret}).status_code == 422
        assert client.put(base, json={"url": "https://one.supabase.co", "secret_key": secret}).json() == {
            "configured": True}
        public = client.get("/api/v1/connections").json()["supabase"]
        assert public == {"url": "https://one.supabase.co", "configured": True,
                          "queued": 0, "synced": 0, "last_status": "pending",
                          "last_checked_at": 0, "last_error": ""}
        assert secret not in json.dumps(public)
        assert secret not in app.state.store.path.read_bytes().decode("utf-8", errors="ignore")
        assert not desktop_route(base, "PUT")
        record_content_page(app.state.store, run_id="configured-run-1", account="owner",
                            payload={"data": {"viewer_v2": {"user_results": {"result": {
                                "tweets_results": []}}}}}, from_time="a", to_time="b",
                            max_results=1000, observed_at=1000)
        sent = []
        def acknowledge(request):
            sent.append(request)
            return httpx.Response(204)
        with httpx.Client(transport=httpx.MockTransport(acknowledge)) as mock:
            assert sync_one_content_run(app.state.store, client=mock, now=1000)["status"] == "synced"
        assert len(sent) == 1 and sent[0].headers["apikey"] == secret
        assert client.put(base, json={"url": "https://two.supabase.co", "secret_key": secret}).status_code == 409
        assert client.delete(base).json() == {"configured": False}
        assert client.get("/api/v1/connections").json()["supabase"]["configured"] is False
