"""Authenticated, restart-safe Content reads from the signed-in X browser."""

import asyncio
from datetime import datetime
import json
import re
import time

from .analytics_store import record_content_page
from .browser_reads import BrowserReads
from .subscription_analytics import AnalyticsShapeError
from .xreader import ReadError


CONTENT_DAYS = (7, 14, 28, 90)


def _seconds(value):
    value = str(value or "")
    if re.fullmatch(r"\d{10}", value):
        return int(value)
    if re.fullmatch(r"\d{13}", value):
        return int(value) / 1000
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _matches_window(record, days, now):
    if not isinstance(record, dict):
        return False
    start, end = _seconds(record.get("from_time")), _seconds(record.get("to_time"))
    observed = record.get("observed_at")
    limit = record.get("max_results")
    return (start is not None and end is not None and
            days - 2 <= (end - start) / 86400 <= days + 2 and
            type(observed) is int and abs(now - observed) <= 600 and
            type(limit) is int and 1 <= limit <= 10_000 and
            isinstance(record.get("payload"), dict))


class AnalyticsBrowserReads:
    def __init__(self, store):
        self.store = store

    def next_action(self):
        with self.store.db(True) as db:
            row = db.execute(
                "SELECT b.* FROM analytics_browser_reads b JOIN tasks t ON t.id=b.task_id "
                "WHERE b.status='queued' AND b.updated<=? AND t.status='running' "
                "AND t.cancel=0 ORDER BY b.updated,b.task_id LIMIT 1",
                (int(time.time()),),
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE analytics_browser_reads SET status='sent',updated=? "
                       "WHERE task_id=?", (int(time.time()), row["task_id"]))
            return {"id": "analytics:" + row["task_id"],
                    "kind": "analytics_content_read", "transport": "analytics",
                    "account": row["account"], "days": row["days"]}

    def receive(self, ident, data):
        if not isinstance(ident, str) or not re.fullmatch(r"analytics:[a-f0-9]{32}", ident):
            return
        task_id = ident[10:]
        rows = self.store.rows("SELECT b.* FROM analytics_browser_reads b "
                               "JOIN tasks t ON t.id=b.task_id WHERE b.task_id=? "
                               "AND b.status='sent' AND t.status='running' AND t.cancel=0",
                               (task_id,))
        if not rows:
            return
        row = rows[0]
        now = int(time.time())
        state = data.get("state") if isinstance(data, dict) else "source_error"
        summary = None
        if state == "ready":
            if str(data.get("account", "")).lower() != row["account"].lower():
                state = "account_mismatch"
            elif not _matches_window(data.get("record"), row["days"], now):
                state = "source_error"
            else:
                record = data["record"]
                try:
                    existing = self.store.rows(
                        "SELECT account,window_from,window_to,request_limit,"
                        "received_count,parsed_count,gap_count,at_request_limit "
                        "FROM analytics_runs WHERE id=?", (task_id,))
                    if existing and (existing[0]["account"].lower() != row["account"].lower() or
                                     existing[0]["window_from"] != record["from_time"] or
                                     existing[0]["window_to"] != record["to_time"] or
                                     existing[0]["request_limit"] != record["max_results"]):
                        raise AnalyticsShapeError("run_query_conflict")
                    summary = (dict(existing[0], run_id=task_id) if existing else
                               record_content_page(
                                   self.store, run_id=task_id, account=row["account"],
                                   payload=record["payload"],
                                   from_time=record["from_time"],
                                   to_time=record["to_time"],
                                   max_results=record["max_results"],
                                   observed_at=record["observed_at"]))
                    for field in ("account", "window_from", "window_to", "request_limit"):
                        summary.pop(field, None)
                except (AnalyticsShapeError, KeyError, TypeError, ValueError):
                    state = "source_error"
        with self.store.db(True) as db:
            current = db.execute("SELECT status,result FROM analytics_browser_reads "
                                 "WHERE task_id=?", (task_id,)).fetchone()
            if not current or current["status"] != "sent":
                return
            retry = int(json.loads(current["result"]).get("_retry", 0))
            if state in ("waiting_browser", "source_error") and retry < 2:
                outcome = {"state": state, "_retry": retry + 1}
                status, available = "queued", now + 10 * 2 ** retry
            else:
                outcome = {"state": state, **({"summary": summary} if summary else {})}
                status, available = "done", now
            db.execute("UPDATE analytics_browser_reads SET status=?,result=?,updated=? "
                       "WHERE task_id=?", (status, json.dumps(outcome), available, task_id))

    async def read(self, job, alive):
        if not BrowserReads(self.store).connected(11):
            raise ReadError("browser_offline")
        days = job["payload"].get("days")
        account = self.store.get("x_username", "")
        if days not in CONTENT_DAYS or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", account):
            raise ReadError("source_error")
        now = int(time.time())
        with self.store.db(True) as db:
            db.execute("INSERT OR IGNORE INTO analytics_browser_reads"
                       "(task_id,account,days,status,updated) VALUES(?,?,?,'queued',?)",
                       (job["id"], account, days, now))
            db.execute("UPDATE analytics_browser_reads SET status='queued' "
                       "WHERE task_id=? AND status='sent'", (job["id"],))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if not alive(job):
                raise ReadError("cancelled")
            row = self.store.rows("SELECT status,result FROM analytics_browser_reads "
                                  "WHERE task_id=?", (job["id"],))[0]
            if row["status"] == "done":
                result = json.loads(row["result"])
                if result.get("state") == "ready" and result.get("summary"):
                    return result["summary"]
                raise ReadError(result.get("state") if result.get("state") in
                                ("waiting_login", "account_mismatch") else "source_error")
            if not BrowserReads(self.store).connected(11):
                raise ReadError("browser_offline")
            await asyncio.sleep(1)
        raise ReadError("timeout")
