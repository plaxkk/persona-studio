"""Authenticated, restart-safe Content reads from the signed-in X browser."""

import asyncio
from datetime import date, datetime, timedelta, timezone
import json
import re
import time

from .analytics_store import record_content_page
from .analytics_overview_store import record_overview
from .browser_reads import BrowserReads
from .subscription_analytics import AnalyticsShapeError
from .xreader import ReadError


CONTENT_DAYS = (7, 14, 28, 90)


def valid_date_range(start, end):
    try:
        if not isinstance(start, str) or not isinstance(end, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
            return False
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        return 0 <= (last - first).days <= 30 and last <= datetime.now(timezone.utc).date()
    except ValueError:
        return False


def backfill_windows(start, end):
    if (not isinstance(start, str) or not isinstance(end, str) or
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end)):
        return None
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return None
    if not 0 <= (last - first).days <= 364 or last > datetime.now(timezone.utc).date():
        return None
    windows = []
    cursor = first
    while cursor <= last:
        stop = min(cursor + timedelta(days=27), last)
        windows.append((cursor.isoformat(), stop.isoformat()))
        cursor = stop + timedelta(days=1)
    return windows


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


def _matches_window(record, days, now, from_date=None, to_date=None):
    if not isinstance(record, dict):
        return False
    start, end = _seconds(record.get("from_time")), _seconds(record.get("to_time"))
    observed = record.get("observed_at")
    limit = record.get("max_results")
    if from_date and to_date and start is not None and end is not None:
        first = datetime.combine(date.fromisoformat(from_date), datetime.min.time(), timezone.utc).timestamp()
        last = (datetime.combine(date.fromisoformat(to_date) + timedelta(days=1),
                                 datetime.min.time(), timezone.utc).timestamp() - .001)
        current_day = to_date == datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        last_day_start = last - 86399.999
        end_ok = (last_day_start <= end <= last + 60) if current_day else abs(end - last) < 60
        window_ok = abs(start - first) < 60 and end_ok
    else:
        window_ok = start is not None and end is not None and days in CONTENT_DAYS and days - 2 <= (end - start) / 86400 <= days + 2
    return (window_ok and
            type(observed) is int and abs(now - observed) <= 600 and
            type(limit) is int and 1 <= limit <= 10_000 and
            isinstance(record.get("payload"), dict))


def _matches_overview_window(record, now):
    if not isinstance(record, dict) or record.get("query_kind") != "overview":
        return False
    start, end = _seconds(record.get("from_time")), _seconds(record.get("to_time"))
    previous_start = _seconds(record.get("prev_from"))
    previous_end = _seconds(record.get("prev_to"))
    if any(value is None for value in (start, end, previous_start, previous_end)):
        return False
    return (26 <= (end - start) / 86400 <= 30 and
            26 <= (previous_end - previous_start) / 86400 <= 30 and
            abs(previous_end - start) < 60 and
            type(record.get("observed_at")) is int and
            abs(now - record["observed_at"]) <= 600 and
            isinstance(record.get("payload"), dict))


class AnalyticsBrowserReads:
    def __init__(self, store):
        self.store = store

    def next_action(self):
        browser = BrowserReads(self.store)
        if not browser.connected(11):
            return None
        custom_ready = browser.connected(12)
        overview_ready = browser.connected(13)
        with self.store.db(True) as db:
            row = db.execute(
                "SELECT b.* FROM analytics_browser_reads b JOIN tasks t ON t.id=b.task_id "
                "WHERE b.status='queued' AND b.updated<=? AND t.status='running' "
                "AND t.cancel=0 AND (b.days!=0 OR ?) "
                "AND (b.kind!='overview' OR ?) "
                "ORDER BY b.updated,b.task_id LIMIT 1",
                (int(time.time()), int(custom_ready), int(overview_ready)),
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE analytics_browser_reads SET status='sent',updated=? "
                       "WHERE task_id=?", (int(time.time()), row["task_id"]))
            return {"id": "analytics:" + row["task_id"],
                    "kind": ("analytics_overview_read" if row["kind"] == "overview"
                             else "analytics_content_read"), "transport": "analytics",
                    "account": row["account"], "days": row["days"],
                    "from_date": row["from_date"], "to_date": row["to_date"]}

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
            elif not ((_matches_overview_window(data.get("record"), now)
                       if row["kind"] == "overview" else
                       _matches_window(data.get("record"), row["days"], now,
                                       row["from_date"], row["to_date"]))):
                state = "source_error"
            else:
                record = data["record"]
                try:
                    if row["kind"] == "overview":
                        existing = self.store.rows(
                            "SELECT account,window_from,window_to,previous_from,previous_to,"
                            "point_count,gap_count,scalars_json FROM analytics_overview_runs "
                            "WHERE id=?", (task_id,))
                        if existing:
                            old = existing[0]
                            if (old["account"].lower() != row["account"].lower() or
                                    old["window_from"] != record["from_time"] or
                                    old["window_to"] != record["to_time"] or
                                    old["previous_from"] != record["prev_from"] or
                                    old["previous_to"] != record["prev_to"]):
                                raise AnalyticsShapeError("run_query_conflict")
                            summary = {"run_id": task_id,
                                       "point_count": old["point_count"],
                                       "gap_count": old["gap_count"],
                                       "scalars": json.loads(old["scalars_json"])}
                        else:
                            summary = record_overview(
                                self.store, run_id=task_id, account=row["account"],
                                payload=record["payload"],
                                from_time=record["from_time"],
                                to_time=record["to_time"],
                                prev_from=record["prev_from"],
                                prev_to=record["prev_to"],
                                observed_at=record["observed_at"])
                    else:
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
        overview = job["kind"] == "analytics_overview"
        if not BrowserReads(self.store).connected(13 if overview else 11):
            raise ReadError("browser_offline")
        days = 28 if overview else job["payload"].get("days", 0)
        from_date = job["payload"].get("from_date")
        to_date = job["payload"].get("to_date")
        account = self.store.get("x_username", "")
        custom = valid_date_range(from_date, to_date)
        if (not (overview or days in CONTENT_DAYS or (days == 0 and custom)) or
                not re.fullmatch(r"[A-Za-z0-9_]{1,15}", account)):
            raise ReadError("source_error")
        if custom and not BrowserReads(self.store).connected(12):
            raise ReadError("browser_offline")
        now = int(time.time())
        with self.store.db(True) as db:
            db.execute("INSERT OR IGNORE INTO analytics_browser_reads"
                       "(task_id,account,days,status,updated,from_date,to_date,kind) "
                       "VALUES(?,?,?,'queued',?,?,?,?)",
                       (job["id"], account, days, now, from_date, to_date,
                        "overview" if overview else "content"))
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
            if not BrowserReads(self.store).connected(13 if overview else 12 if custom else 11):
                raise ReadError("browser_offline")
            await asyncio.sleep(1)
        raise ReadError("timeout")
