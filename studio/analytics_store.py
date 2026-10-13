"""Transactional local staging for Subscription Analytics business responses."""

from __future__ import annotations

import json
import re

from .subscription_analytics import AnalyticsShapeError, parse_content_page


PARSER_VERSION = 1
MAX_RAW_BYTES = 8_000_000


def record_content_page(store, *, run_id: str, account: str, payload: dict,
                        from_time: str, to_time: str, max_results: int,
                        observed_at: int) -> dict:
    """Persist one authenticated Content response and its coverage in one commit.

    The caller provides a stable run ID for retries. No cookies, request headers,
    or tokens are accepted or saved here.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", run_id or ""):
        raise AnalyticsShapeError("run_id_invalid")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", account or ""):
        raise AnalyticsShapeError("account_invalid")
    parsed = parse_content_page(payload, from_time=from_time, to_time=to_time,
                                max_results=max_results, observed_at=observed_at)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        raise AnalyticsShapeError("content_response_too_large")
    metadata = (account.lower(), parsed["source"], from_time, to_time,
                observed_at, max_results, parsed["received_count"],
                parsed["parsed_count"], parsed["gap_count"],
                int(parsed["at_request_limit"]), PARSER_VERSION, raw)
    with store.db(True) as db:
        existing = db.execute("SELECT account,source,window_from,window_to,observed_at,"
                              "request_limit,received_count,parsed_count,gap_count,"
                              "at_request_limit,parser_version,raw_json FROM analytics_runs WHERE id=?",
                              (run_id,)).fetchone()
        if existing:
            if tuple(existing) != metadata:
                raise AnalyticsShapeError("run_id_conflict")
        else:
            db.execute("INSERT INTO analytics_runs(id,account,source,window_from,window_to,"
                       "observed_at,request_limit,received_count,parsed_count,gap_count,"
                       "at_request_limit,parser_version,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, *metadata))
            db.executemany("INSERT INTO analytics_post_metrics VALUES(?,?,?,?,?)", [
                (run_id, item["post_id"], item["created_at_ms"], item["text"],
                 json.dumps(item["metrics"], ensure_ascii=False, separators=(",", ":")))
                for item in parsed["posts"]
            ])
            db.executemany("INSERT INTO analytics_coverage_gaps VALUES(?,?,?,?)", [
                (run_id, item["index"], item["post_id"], item["reason"])
                for item in parsed["gaps"]
            ])
    return {"run_id": run_id, "received_count": parsed["received_count"],
            "parsed_count": parsed["parsed_count"], "gap_count": parsed["gap_count"],
            "at_request_limit": parsed["at_request_limit"]}
