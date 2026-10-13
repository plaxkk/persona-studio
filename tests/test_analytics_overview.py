import time

import pytest

from studio.analytics_overview import parse_overview
from studio.analytics_overview_store import record_overview
from studio.store import Store
from studio.subscription_analytics import AnalyticsShapeError


WINDOW = dict(from_time="2026-09-16T00:00:00.000Z",
              to_time="2026-10-14T00:00:00.000Z",
              prev_from="2026-08-19T00:00:00.000Z",
              prev_to="2026-09-16T00:00:00.000Z")


def fixture_payload():
    point = {"count": 3, "engagement_type": "Displayed",
             "is_engaging_user_verified": False, "timestamp": 1791763200000}
    return {"data": {"viewer_v2": {"user_results": {"result": {
        "current_time_series": [point],
        "hourly_backfill": [{"count": 2, "engagement_type": "Displayed",
                             "timestamp": point["timestamp"]}],
        "previous_totals": [{"count": 1, "engagement_type": "Displayed",
                             "timestamp": 1789430400000}],
        "legacy_current_follow_metrics": [{"metric_values": [
            {"metric_type": "Follows", "metric_value": 4},
            {"metric_type": "Unfollows"}],
            "timestamp": {"iso8601_time": "2026-10-12T00:00:00.000Z"}}],
        "legacy_previous_follow_metrics": [],
        "relationship_counts": {"followers": 501},
        "verified_follower_count": "3",
        "realtime_active_followers": {"active_count": 12,
                                      "is_approximate": True},
    }}}}}


def test_overview_series_remain_separate_and_missing_values_unknown():
    parsed = parse_overview(fixture_payload(), **WINDOW, observed_at=int(time.time()))
    assert parsed["point_count"] == 5
    current = [point for point in parsed["points"]
               if point["series"] == "current_time_series"][0]
    hourly = [point for point in parsed["points"]
              if point["series"] == "hourly_backfill"][0]
    assert current["timestamp_ms"] == hourly["timestamp_ms"]
    assert current["count"] == 3 and hourly["count"] == 2
    assert parsed["scalars"]["followers"] == 501
    assert parsed["scalars"]["active_followers_approximate"] is True
    assert [point for point in parsed["points"]
            if point["metric_type"] == "Unfollows"][0]["count"] is None


def test_overview_local_stage_is_idempotent_and_excludes_caller_headers(tmp_path):
    store = Store(tmp_path)
    payload = fixture_payload()
    payload["request_headers"] = {"authorization": "must-not-leak"}
    args = dict(run_id="a" * 32, account="kkplayit", payload=payload,
                observed_at=int(time.time()), **WINDOW)
    first = record_overview(store, **args)
    second = record_overview(store, **args)
    assert first == second
    assert len(store.rows("SELECT * FROM analytics_overview_points")) == 5
    run = store.rows("SELECT raw_json,cloud_status FROM analytics_overview_runs")[0]
    assert "authorization" not in run["raw_json"]
    assert run["cloud_status"] == "pending"
    with pytest.raises(AnalyticsShapeError, match="run_id_conflict"):
        record_overview(store, **{**args, "to_time": "2026-10-15T00:00:00.000Z"})


def test_overview_rejects_error_payload_without_core_series():
    payload = {"data": {"viewer_v2": {"user_results": {"result": {
        "relationship_counts": {"followers": 501},
    }}}}}
    with pytest.raises(AnalyticsShapeError, match="overview_core_series_missing"):
        parse_overview(payload, **WINDOW, observed_at=int(time.time()))
