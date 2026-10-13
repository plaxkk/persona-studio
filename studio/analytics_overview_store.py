"""Transactional local staging for one Subscription Overview response."""

import json
import re

from .analytics_overview import parse_overview
from .subscription_analytics import AnalyticsShapeError


PARSER_VERSION = 1
MAX_RAW_BYTES = 8_000_000


def record_overview(store, *, run_id, account, payload, from_time, to_time,
                    prev_from, prev_to, observed_at):
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", run_id or ""):
        raise AnalyticsShapeError("run_id_invalid")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", account or ""):
        raise AnalyticsShapeError("account_invalid")
    parsed = parse_overview(payload, from_time=from_time, to_time=to_time,
                            prev_from=prev_from, prev_to=prev_to,
                            observed_at=observed_at)
    raw = json.dumps({"data": payload["data"]}, ensure_ascii=False,
                     separators=(",", ":"), sort_keys=True)
    if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        raise AnalyticsShapeError("overview_response_too_large")
    scalars = json.dumps(parsed["scalars"], ensure_ascii=False,
                         separators=(",", ":"), sort_keys=True)
    metadata = (account.lower(), from_time, to_time, prev_from, prev_to,
                observed_at, parsed["point_count"], parsed["gap_count"],
                PARSER_VERSION, scalars, raw)
    with store.db(True) as db:
        existing = db.execute(
            "SELECT account,window_from,window_to,previous_from,previous_to,"
            "observed_at,point_count,gap_count,parser_version,scalars_json,raw_json "
            "FROM analytics_overview_runs WHERE id=?", (run_id,)).fetchone()
        if existing:
            if tuple(existing) != metadata:
                raise AnalyticsShapeError("run_id_conflict")
        else:
            db.execute("INSERT INTO analytics_overview_runs"
                       "(id,account,window_from,window_to,previous_from,previous_to,"
                       "observed_at,point_count,gap_count,parser_version,scalars_json,raw_json) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, *metadata))
            db.executemany("INSERT INTO analytics_overview_points VALUES(?,?,?,?,?,?,?,?)", [
                (run_id, point["series"], point["item_index"], point["metric_index"],
                 point["timestamp_ms"], point["metric_type"], point["count"],
                 None if point["verified"] is None else int(point["verified"]))
                for point in parsed["points"]])
            db.executemany("INSERT INTO analytics_overview_gaps VALUES(?,?,?,?,?)", [
                (run_id, gap["series"], gap["index"], gap["metric_index"], gap["reason"])
                for gap in parsed["gaps"]])
    return {"run_id": run_id, "point_count": parsed["point_count"],
            "gap_count": parsed["gap_count"], "scalars": parsed["scalars"]}
