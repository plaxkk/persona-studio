"""Parse X Subscription Overview without merging overlapping time series."""

from __future__ import annotations

from datetime import datetime
import re

from .subscription_analytics import AnalyticsShapeError


SERIES = ("current_time_series", "hourly_backfill", "previous_totals",
          "legacy_current_follow_metrics", "legacy_previous_follow_metrics")


def _timestamp_ms(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, dict):
        iso = value.get("iso8601_time")
        if isinstance(iso, str):
            try:
                parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    return int(parsed.timestamp() * 1000)
            except ValueError:
                pass
    return None


def _value(value):
    return value if type(value) in (int, float) else None


def parse_overview(payload, *, from_time, to_time, prev_from, prev_to,
                   observed_at):
    if (not isinstance(payload, dict) or
            not all(isinstance(value, str) and value
                    for value in (from_time, to_time, prev_from, prev_to)) or
            type(observed_at) is not int or observed_at < 1):
        raise AnalyticsShapeError("overview_request_invalid")
    try:
        root = payload["data"]["viewer_v2"]["user_results"]["result"]
    except (KeyError, TypeError) as exc:
        raise AnalyticsShapeError("overview_result_missing") from exc
    if not isinstance(root, dict):
        raise AnalyticsShapeError("overview_result_invalid")
    if (not isinstance(root.get("current_time_series"), list) or
            not isinstance(root.get("previous_totals"), list)):
        raise AnalyticsShapeError("overview_core_series_missing")
    points, gaps = [], []
    for series in SERIES:
        items = root.get(series)
        if not isinstance(items, list):
            gaps.append({"series": series, "index": -1, "metric_index": -1,
                         "reason": "series_missing"})
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                gaps.append({"series": series, "index": index, "metric_index": -1,
                             "reason": "invalid_item"})
                continue
            timestamp_ms = _timestamp_ms(item.get("timestamp"))
            values = (item.get("metric_values") if series.startswith("legacy_") else
                      [{"metric_type": item.get("engagement_type"),
                        "metric_value": item.get("count")}])
            if timestamp_ms is None or not isinstance(values, list) or not values:
                gaps.append({"series": series, "index": index, "metric_index": -1,
                             "reason": "invalid_item"})
                continue
            for metric_index, metric in enumerate(values):
                if (not isinstance(metric, dict) or
                        not isinstance(metric.get("metric_type"), str) or
                        not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", metric["metric_type"])):
                    gaps.append({"series": series, "index": index,
                                 "metric_index": metric_index,
                                 "reason": "invalid_metric"})
                    continue
                points.append({"series": series, "item_index": index,
                               "metric_index": metric_index,
                               "timestamp_ms": timestamp_ms,
                               "metric_type": metric["metric_type"],
                               "count": _value(metric.get("metric_value")),
                               "verified": (item.get("is_engaging_user_verified")
                                            if type(item.get("is_engaging_user_verified")) is bool
                                            else None)})
    relationship = root.get("relationship_counts")
    active = root.get("realtime_active_followers")
    verified = root.get("verified_follower_count")
    scalars = {
        "followers": _value(relationship.get("followers")) if isinstance(relationship, dict) else None,
        "verified_followers": int(verified) if isinstance(verified, str) and re.fullmatch(r"\d+", verified) else _value(verified),
        "active_followers": _value(active.get("active_count")) if isinstance(active, dict) else None,
        "active_followers_approximate": (active.get("is_approximate") if isinstance(active, dict) and
                                         type(active.get("is_approximate")) is bool else None),
    }
    return {"from_time": from_time, "to_time": to_time,
            "prev_from": prev_from, "prev_to": prev_to,
            "observed_at": observed_at, "points": points, "gaps": gaps,
            "point_count": len(points), "gap_count": len(gaps),
            "scalars": scalars}
