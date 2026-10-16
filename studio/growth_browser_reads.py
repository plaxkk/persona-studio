"""Bounded, account-checked X reads for the growth plan."""
from __future__ import annotations

import asyncio
import json
import re
import time

from .browser_reads import BrowserReads
from .xreader import ReadError

MINIMUM = (1, 4, 8)


def reward_metrics(lines):
    """Extract only displayed values; ambiguous abbreviations remain strings."""
    out = []
    for index, line in enumerate(lines):
        if not isinstance(line, str):
            continue
        if re.search(r"verified followers|认证粉丝|verified home timeline impressions|主页时间线展示", line, re.I):
            following = lines[index + 1:index + 4]
            current = next((part for part in following if isinstance(part, str) and
                            re.fullmatch(r"[\d,.]+\s*[KkMm万千]?", part.strip())), None)
            out.append({"label": line[:250], "current_display": current})
    return out


class GrowthBrowserReads:
    def __init__(self, store):
        self.store = store

    def next_action(self):
        if not BrowserReads(self.store).connected(MINIMUM):
            return None
        with self.store.db(True) as db:
            row = db.execute(
                "SELECT b.* FROM growth_browser_reads b JOIN tasks t ON t.id=b.task_id "
                "WHERE b.status='queued' AND b.updated<=? AND t.status='running' AND t.cancel=0 "
                "ORDER BY b.updated,b.task_id LIMIT 1", (int(time.time()),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE growth_browser_reads SET status='sent',updated=? WHERE task_id=?",
                       (int(time.time()), row["task_id"]))
            exclude = []
            if row["kind"] == "followers":
                exclude = [item[0] for item in db.execute(
                    "SELECT handle FROM growth_observed_followers WHERE lower(account)=lower(?) ORDER BY observed_at DESC LIMIT 2000",
                    (row["account"],)).fetchall()]
            return {"id": "growth:" + row["task_id"], "kind": "growth_" + row["kind"] + "_read",
                    "transport": "growth", "account": row["account"], "exclude": exclude}

    def receive(self, ident, data):
        if not isinstance(ident, str) or not re.fullmatch(r"growth:[a-f0-9]{32}", ident):
            return
        task_id = ident[7:]
        now = int(time.time())
        with self.store.db(True) as db:
            row = db.execute("SELECT b.* FROM growth_browser_reads b JOIN tasks t ON t.id=b.task_id "
                             "WHERE b.task_id=? AND b.status='sent' AND t.status='running' AND t.cancel=0",
                             (task_id,)).fetchone()
            if not row:
                return
            state = data.get("state") if isinstance(data, dict) else "source_error"
            if state == "ready" and str(data.get("account", "")).lower() != row["account"].lower():
                state = "account_mismatch"
            if state == "ready" and len(json.dumps(data, ensure_ascii=False)) > 50_000:
                state = "source_error"
            summary = None
            if state == "ready" and row["kind"] == "rewards":
                lines = data.get("lines")
                if (not isinstance(lines, list) or len(lines) > 80 or
                    any(not isinstance(line, str) or len(line) > 500 for line in lines) or
                    not isinstance(data.get("raw_text"), str) or len(data["raw_text"]) > 12000 or
                    data.get("page_status") not in ("visible", "check_back_later")):
                    state = "source_error"
                else:
                    metrics = reward_metrics(lines)
                    db.execute("INSERT OR IGNORE INTO growth_reward_snapshots "
                               "(id,account,observed_at,page_status,period,metrics_json,raw_json,source) "
                               "VALUES(?,?,?,?,?,?,?,?)",
                               (task_id, row["account"], now, data["page_status"],
                                data.get("period") if isinstance(data.get("period"), str) else None,
                                json.dumps(metrics, ensure_ascii=False),
                                json.dumps({"lines": lines, "text": data["raw_text"]}, ensure_ascii=False),
                                "x_logged_in_browser"))
                    summary = {"observed_at": now, "page_status": data["page_status"],
                               "metrics": metrics}
            elif state == "ready" and row["kind"] == "followers":
                records = data.get("records")
                if (not isinstance(records, list) or len(records) > 12 or
                    not isinstance(data.get("discovered"), int) or data["discovered"] < len(records) or
                    data["discovered"] > 180 or
                    (data["discovered"] == 0 and data.get("stopped_reason") != "explicit_empty") or
                    any(not isinstance(item, dict) or
                            not re.fullmatch(r"[A-Za-z0-9_]{1,15}", str(item.get("handle", ""))) or
                            (item.get("followers") is not None and
                             (type(item["followers"]) is not int or not 0 <= item["followers"] <= 1_000_000_000)) or
                            type(item.get("verified")) is not bool or
                            len(str(item.get("followers_display", ""))) > 50 for item in records)):
                    state = "source_error"
                else:
                    for item in records:
                        db.execute("INSERT INTO growth_observed_followers "
                                   "VALUES(?,?,?,?,?,?,?) ON CONFLICT(account,handle) DO UPDATE SET "
                                   "followers=excluded.followers,followers_display=excluded.followers_display,"
                                   "verified=excluded.verified,observed_at=excluded.observed_at",
                                   (row["account"], item["handle"], item["followers"],
                                    item.get("followers_display", ""), int(item["verified"]), now,
                                    "x_followers_page"))
                    observed = db.execute("SELECT followers,verified FROM growth_observed_followers "
                                          "WHERE lower(account)=lower(?)", (row["account"],)).fetchall()
                    bands = {str(limit): sum(1 for item in observed if item["followers"] is not None
                             and item["followers"] >= limit) for limit in (1000, 3000, 5000, 10000)}
                    db.execute("INSERT OR IGNORE INTO growth_follower_reads "
                               "(task_id,account,observed_at,discovered,read_count,stopped_reason,"
                               "bands_json,exact_count,verified_observed) VALUES(?,?,?,?,?,?,?,?,?)",
                               (task_id, row["account"], now, data["discovered"], len(records),
                                str(data.get("stopped_reason", ""))[:40],
                                json.dumps(bands), sum(item["followers"] is not None for item in observed),
                                sum(bool(item["verified"]) for item in observed)))
                    summary = {"observed_at": now, "discovered": data["discovered"],
                               "read_count": len(records)}
            if state != "ready":
                retry = int(json.loads(row["result"]).get("_retry", 0))
                if state in ("waiting_browser", "source_error") and retry < 2:
                    result, status, available = {"state": state, "_retry": retry + 1}, "queued", now + 10 * 2 ** retry
                else:
                    hint = data.get("hint") if isinstance(data, dict) else None
                    result = {"state": state}
                    if hint in ("identity", "no_main", "no_cards", "card_links", "x_page_error", "open"):
                        result["hint"] = hint
                    result, status, available = result, "done", now
            else:
                result, status, available = {"state": "ready", "summary": summary}, "done", now
            db.execute("UPDATE growth_browser_reads SET status=?,result=?,updated=? WHERE task_id=?",
                       (status, json.dumps(result, ensure_ascii=False), available, task_id))

    async def read(self, job, alive):
        if not BrowserReads(self.store).connected(MINIMUM):
            raise ReadError("browser_offline")
        account = str(self.store.get("x_username", "") or "")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", account):
            raise ReadError("source_error")
        kind = "rewards" if job["kind"] == "growth_rewards" else "followers"
        now = int(time.time())
        with self.store.db(True) as db:
            db.execute("INSERT OR IGNORE INTO growth_browser_reads VALUES(?,?,?,'queued','{}',?)",
                       (job["id"], account, kind, now))
            db.execute("UPDATE growth_browser_reads SET status='queued' WHERE task_id=? AND status='sent'",
                       (job["id"],))
        deadline = time.monotonic() + (300 if kind == "followers" else 90)
        while time.monotonic() < deadline:
            if not alive(job):
                raise ReadError("cancelled")
            row = self.store.rows("SELECT status,result FROM growth_browser_reads WHERE task_id=?",
                                  (job["id"],))[0]
            if row["status"] == "done":
                result = json.loads(row["result"])
                if result.get("state") == "ready":
                    return result["summary"]
                raise ReadError(result.get("state") if result.get("state") in
                                ("waiting_login", "account_mismatch") else "source_error")
            # A bounded follower read may keep the extension busy longer than
            # the 45-second presence lease. Once dispatched, wait for its
            # result or this task's deadline instead of reporting it offline.
            await asyncio.sleep(1)
        raise ReadError("timeout")
