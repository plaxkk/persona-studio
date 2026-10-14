"""Normalize observed X Subscription Analytics responses without inventing coverage.

This module accepts business JSON that the authenticated browser has already read.
It neither performs network requests nor handles browser credentials.
"""

from __future__ import annotations

import re


CONTENT_METRICS = (
    "Impressions", "Likes", "Engagements", "Bookmark", "Share", "Follows",
    "Replies", "Retweets", "ProfileVisits", "DetailExpands", "UrlClicks",
    "HashtagClicks", "PermalinkClicks",
)


class AnalyticsShapeError(ValueError):
    """The response does not have the observed Content page structure."""


def _tweet_results(payload: dict) -> list:
    try:
        results = payload["data"]["viewer_v2"]["user_results"]["result"]["tweets_results"]
    except (KeyError, TypeError) as exc:
        raise AnalyticsShapeError("content_results_missing") from exc
    if not isinstance(results, list):
        raise AnalyticsShapeError("content_results_not_list")
    return results


def _metrics(values: object) -> dict[str, int | float | None] | None:
    if not isinstance(values, list):
        return None
    metrics: dict[str, int | float | None] = dict.fromkeys(CONTENT_METRICS)
    for entry in values:
        if not isinstance(entry, dict) or not isinstance(entry.get("metric_type"), str):
            continue
        kind = entry["metric_type"]
        if kind not in metrics:
            continue
        value = entry.get("metric_value")
        # A missing field means unknown. An explicit numeric zero is valid.
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            metrics[kind] = value
    return metrics


def _kind(result: dict) -> str:
    """Classify a post as a reply only when the response proves it is one.

    `reply_to_results` holds the parent post (`{"id", "rest_id"}`) and is absent
    or null for everything else — measured on 815 stored items, where it agreed
    with the leading-`@` pattern 805 times. There is no original/quote marker in
    this payload, so a non-reply is recorded as `post` without claiming it is an
    original; the weekly report lists that gap instead of guessing.
    """
    parent = result.get("reply_to_results")
    return "reply" if isinstance(parent, dict) and parent else "post"


def parse_content_page(payload: dict, *, from_time: str, to_time: str,
                       max_results: int, observed_at: int) -> dict:
    """Return posts and explicit gaps for one Content query window.

    `coverage_complete` is intentionally absent: one successful response cannot
    establish that X returned every historical post in the selected window.
    """
    if not isinstance(payload, dict) or not from_time or not to_time or max_results < 1 or observed_at < 1:
        raise AnalyticsShapeError("content_request_invalid")
    results = _tweet_results(payload)
    posts, gaps, seen_ids = [], [], set()
    for index, outer in enumerate(results):
        if not isinstance(outer, dict):
            gaps.append({"index": index, "post_id": None, "reason": "invalid_item"})
            continue
        post_id = str(outer.get("rest_id") or "")
        result = outer.get("result")
        if not isinstance(result, dict):
            reason = "missing_result"
        elif result.get("__typename") == "TweetWithVisibilityResults":
            reason = "visibility_restricted"
        elif result.get("__typename") != "Tweet":
            reason = "unexpected_type"
        else:
            details = result.get("details")
            metrics = _metrics(result.get("organic_metrics_total"))
            if post_id in seen_ids:
                reason = "duplicate_post"
            elif (re.fullmatch(r"\d+", post_id) and isinstance(details, dict)
                    and isinstance(details.get("created_at_ms"), int)
                    and details["created_at_ms"] > 0
                    and isinstance(details.get("full_text"), str)
                    and details["full_text"].strip() and metrics is not None):
                posts.append({"post_id": post_id, "created_at_ms": details["created_at_ms"],
                              "text": details["full_text"], "metrics": metrics,
                              "kind": _kind(result)})
                seen_ids.add(post_id)
                continue
            else:
                reason = "invalid_tweet"
        gaps.append({"index": index, "post_id": post_id or None, "reason": reason})
    return {
        "source": "subscription_content_query",
        "from_time": from_time, "to_time": to_time, "observed_at": observed_at,
        "max_results": max_results, "received_count": len(results),
        "parsed_count": len(posts), "gap_count": len(gaps),
        "at_request_limit": len(results) >= max_results,
        "posts": posts, "gaps": gaps,
    }
