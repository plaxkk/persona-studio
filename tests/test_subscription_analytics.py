import pytest

from studio.subscription_analytics import AnalyticsShapeError, parse_content_page


def test_content_page_keeps_missing_metrics_unknown_and_reports_coverage_gaps():
    payload = {"data": {"viewer_v2": {"user_results": {"result": {"tweets_results": [
        {"rest_id": "123", "result": {"__typename": "Tweet", "details": {
            "created_at_ms": 1234567890000, "full_text": "A post"},
            "organic_metrics_total": [
                {"metric_type": "Impressions", "metric_value": 20},
                {"metric_type": "Likes", "metric_value": 0},
                {"metric_type": "Replies"},
            ]}},
        {"rest_id": "124", "result": {"__typename": "TweetWithVisibilityResults"}},
        {"rest_id": "125"},
    ]}}}}}
    parsed = parse_content_page(payload, from_time="2026-07-16T00:00:00Z",
                                to_time="2026-10-13T23:59:59Z", max_results=1000,
                                observed_at=1791880000)
    assert (parsed["received_count"], parsed["parsed_count"], parsed["gap_count"]) == (3, 1, 2)
    assert parsed["posts"][0]["metrics"]["Impressions"] == 20
    assert parsed["posts"][0]["metrics"]["Likes"] == 0
    assert parsed["posts"][0]["metrics"]["Replies"] is None
    assert parsed["gaps"] == [
        {"index": 1, "post_id": "124", "reason": "visibility_restricted"},
        {"index": 2, "post_id": "125", "reason": "missing_result"},
    ]
    assert parsed["at_request_limit"] is False


def test_content_page_fails_closed_on_changed_response_shape():
    with pytest.raises(AnalyticsShapeError, match="content_results_missing"):
        parse_content_page({"data": {}}, from_time="a", to_time="b",
                           max_results=1000, observed_at=1)
