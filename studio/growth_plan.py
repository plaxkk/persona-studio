"""A local, user-confirmed growth plan built from observed account data."""
from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime, timedelta

from . import weekly


def _id():
    return uuid.uuid4().hex


def _week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _today(store) -> date:
    return datetime.now(weekly.timezone(store)).date()


def _account(store) -> str:
    return str(store.get("x_username", "") or "").strip().lstrip("@")


def _profile(store, account):
    rows = store.rows(
        "SELECT followers,observed_at,day FROM profile_snapshots "
        "WHERE lower(account)=lower(?) AND followers IS NOT NULL "
        "ORDER BY observed_at DESC,id DESC LIMIT 1", (account,))
    if not rows:
        return None
    result = rows[0]
    result["stale"] = int(time.time()) - result["observed_at"] > max(
        2 * int(store.get("sync_interval", 1800) or 1800), 86400)
    return result


def _milestones(store, account, profile):
    # A reached stage is durable: a later follower decline does not erase the
    # achievement, but the live gap always uses the new reading.
    first = store.rows(
        "SELECT followers,observed_at FROM profile_snapshots WHERE lower(account)=lower(?) "
        "AND followers IS NOT NULL ORDER BY observed_at,id LIMIT 1", (account,))
    starting = first[0]["followers"] if first else None
    with store.db(True) as c:
        rows = c.execute("SELECT * FROM growth_milestones ORDER BY target").fetchall()
        if profile and not profile["stale"]:
            previous_reached = True
            previous_target = None
            for row in rows:
                if previous_reached and row["started_at"] is None:
                    c.execute("UPDATE growth_milestones SET started_at=?,start_followers=?,updated_at=? WHERE id=?",
                              (first[0]["observed_at"] if previous_target is None and first else profile["observed_at"],
                               starting if previous_target is None else previous_target,
                               int(time.time()), row["id"]))
                if row["completed_at"] is None and profile["followers"] >= row["target"]:
                    c.execute("UPDATE growth_milestones SET completed_at=?,updated_at=? WHERE id=?",
                              (profile["observed_at"], int(time.time()), row["id"]))
                    previous_reached = True
                else:
                    previous_reached = row["completed_at"] is not None
                previous_target = row["target"]
        rows = [dict(row) for row in c.execute("SELECT * FROM growth_milestones ORDER BY target")]
    current = next((row for row in rows if row["completed_at"] is None), rows[-1] if rows else None)
    for index, item in enumerate(rows):
        base = rows[index - 1]["target"] if index else starting
        if item["start_followers"] is not None:
            base = item["start_followers"]
        item["stage_start"] = base
        value = profile["followers"] if profile else None
        item["remaining"] = max(0, item["target"] - value) if value is not None else None
        item["progress"] = (max(0, min(1, (value - base) / (item["target"] - base)))
                            if value is not None and base is not None and item["target"] > base else None)
    return rows, current


def _plan(store, account, week_start):
    rows = store.rows("SELECT * FROM growth_week_plans WHERE lower(account)=lower(?) AND week_start=?",
                      (account, week_start))
    if not rows:
        return None
    plan = rows[0]
    plan["actions"] = store.rows("SELECT * FROM growth_actions WHERE plan_id=? ORDER BY position",
                                 (plan["id"],))
    return plan


def suggest(store, account, week_start=None):
    day = _week(_today(store)) if week_start is None else date.fromisoformat(week_start)
    if day.weekday():
        raise ValueError("计划周必须从周一开始。")
    existing = _plan(store, account, day.isoformat())
    if existing:
        return existing
    reports = store.rows("SELECT id,report_json,generated_at FROM weekly_reports "
                         "WHERE lower(account)=lower(?) AND week_start<? AND week_start>=? "
                         "ORDER BY week_start DESC LIMIT 1",
                         (account, day.isoformat(), (day - timedelta(days=14)).isoformat()))
    report = json.loads(reports[0]["report_json"]) if reports else {}
    issues = report.get("issues") or []
    actions = []
    for issue in issues[:3]:
        action = str(issue.get("action", "")).strip()
        if action:
            actions.append({"title": action[:300], "problem": str(issue.get("title", ""))[:300],
                            "measure": "下周周报中的相关数据", "effort": "由你估计",
                            "evidence": str(issue.get("evidence", ""))[:1000]})
    stamp = int(time.time())
    pid = _id()
    with store.db(True) as c:
        c.execute("INSERT INTO growth_week_plans VALUES(?,?,?,?,?,?,?)",
                  (pid, account, day.isoformat(), "suggested", reports[0]["id"] if reports else None,
                   stamp, stamp))
        for n, action in enumerate(actions):
            c.execute("INSERT INTO growth_actions VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (_id(), pid, n, action["title"], action["problem"], action["measure"],
                       action["effort"], action["evidence"], stamp, stamp))
    return _plan(store, account, day.isoformat())


def publications(store, account, start, end):
    start_ts = int(datetime.combine(start, datetime.min.time(), weekly.timezone(store)).timestamp())
    end_ts = int(datetime.combine(end, datetime.min.time(), weekly.timezone(store)).timestamp())
    rows = store.rows(
        "SELECT p.draft_id,p.tweet_id,p.url,p.publication_kind,p.published_at,"
        "p.user_confirmed_at,p.platform_observed_at,d.text "
        "FROM draft_publications p JOIN drafts d ON d.id=p.draft_id "
        "WHERE lower(p.author)=lower(?) AND COALESCE(p.published_at,p.user_confirmed_at)>=? "
        "AND COALESCE(p.published_at,p.user_confirmed_at)<? "
        "ORDER BY COALESCE(p.published_at,p.user_confirmed_at) DESC",
        (account, start_ts, end_ts))
    for row in rows:
        row["time_source"] = "platform" if row["published_at"] else "user_confirmed"
    return rows


def overview(store):
    account = _account(store)
    day = _today(store)
    week_start = _week(day)
    profile = _profile(store, account) if account else None
    milestones, current = _milestones(store, account, profile)
    plan = _plan(store, account, week_start.isoformat()) if account else None
    checkins = store.rows(
        "SELECT c.*,a.title AS action_title,p.url AS publication_url FROM growth_checkins c "
        "LEFT JOIN growth_actions a ON a.id=c.action_id "
        "LEFT JOIN draft_publications p ON p.draft_id=c.publication_id "
        "WHERE lower(c.account)=lower(?) AND c.day>=? AND c.day<=? ORDER BY c.day DESC,c.created_at DESC",
        (account, week_start.isoformat(), day.isoformat())) if account else []
    experiments = store.rows(
        "SELECT id,hypothesis,metric,baseline_json,target_json,started_at,planned_end_at,"
        "ended_at,status,result_json,decision,created,updated FROM experiments "
        "WHERE lower(account)=lower(?) ORDER BY created DESC LIMIT 10", (account,)) if account else []
    for item in experiments:
        for key in ("baseline_json", "target_json", "result_json"):
            item[key[:-5]] = json.loads(item.pop(key) or "{}")
        start = datetime.fromtimestamp(item["started_at"], weekly.timezone(store)).date()
        stop = datetime.fromtimestamp(item["ended_at"] or int(time.time()), weekly.timezone(store)).date() + timedelta(days=1)
        item["publications"] = publications(store, account, start, stop)[:50]
        spent = store.rows("SELECT sum(minutes) AS total FROM growth_checkins "
                           "WHERE lower(account)=lower(?) AND day>=? AND day<?",
                           (account, start.isoformat(), stop.isoformat()))
        item["minutes_logged"] = spent[0]["total"] if spent else None
        mature = [post for post in item["publications"] if post["published_at"]
                  and post["published_at"] <= int(time.time()) - 72 * 3600]
        item["mature_post_count"] = len(mature)
        item["conclusion_ready"] = (item["status"] != "active" and bool(item["decision"])
                                    and item["planned_end_at"] <= int(time.time())
                                    and len(mature) >= 10)
    report = store.rows("SELECT id,week_start,report_json,facts_json FROM weekly_reports "
                        "WHERE lower(account)=lower(?) AND week_start<? "
                        "ORDER BY week_start DESC LIMIT 1", (account, week_start.isoformat())) if account else []
    previous = None
    if report:
        prior_plan = _plan(store, account, report[0]["week_start"])
        prior_start = date.fromisoformat(report[0]["week_start"])
        prior_checkins = store.rows(
            "SELECT kind,count(*) AS count,sum(minutes) AS minutes FROM growth_checkins "
            "WHERE lower(account)=lower(?) AND day>=? AND day<? GROUP BY kind",
            (account, prior_start.isoformat(), (prior_start + timedelta(days=7)).isoformat()))
        previous = {"id": report[0]["id"], "week_start": report[0]["week_start"],
                    "report": json.loads(report[0]["report_json"]),
                    "facts": json.loads(report[0]["facts_json"]),
                    "plan": prior_plan, "checkins": prior_checkins}
    rewards = store.rows("SELECT observed_at,page_status,period,metrics_json,raw_json,cloud_status,cloud_error FROM growth_reward_snapshots "
                         "WHERE lower(account)=lower(?) ORDER BY observed_at DESC LIMIT 2", (account,)) if account else []
    for item in rewards:
        item["metrics"] = json.loads(item.pop("metrics_json"))
        item["raw"] = json.loads(item.pop("raw_json"))
    follower_rows = store.rows("SELECT followers,verified FROM growth_observed_followers "
                               "WHERE lower(account)=lower(?)", (account,)) if account else []
    bands = {str(limit): sum(1 for item in follower_rows if item["followers"] is not None
                             and item["followers"] >= limit) for limit in (1000, 3000, 5000, 10000)}
    recent_read = store.rows("SELECT observed_at,discovered,read_count,stopped_reason,cloud_status,cloud_error "
                             "FROM growth_follower_reads WHERE lower(account)=lower(?) "
                             "AND stopped_reason!='invalid_empty_read' "
                             "ORDER BY observed_at DESC LIMIT 1", (account,)) if account else []
    relationships = store.rows("SELECT n.handle,n.note,n.interaction_url,n.next_step,n.updated_at,"
                               "f.followers,f.verified FROM growth_relationship_notes n "
                               "LEFT JOIN growth_observed_followers f ON lower(f.account)=lower(n.account) "
                               "AND lower(f.handle)=lower(n.handle) "
                               "WHERE lower(n.account)=lower(?) ORDER BY n.updated_at DESC LIMIT 100",
                               (account,)) if account else []
    from .browser_reads import BrowserReads
    ready = BrowserReads(store).connected((1, 4, 7))
    read_tasks = store.rows("SELECT kind,status,error,created FROM tasks "
                            "WHERE kind IN ('growth_rewards','growth_followers') "
                            "ORDER BY created DESC LIMIT 10")
    return {"account": account, "today": day.isoformat(), "week_start": week_start.isoformat(),
            "profile": profile, "milestones": milestones,
            "current_milestone_id": current["id"] if current else None,
            "plan": plan, "checkins": checkins,
            "publications": publications(store, account, week_start, day + timedelta(days=1)) if account else [],
            "previous_report": previous, "experiments": experiments,
            "browser_ready": ready, "growth_read_tasks": read_tasks,
            "rewards": rewards,
            "follower_observation": {"observed": len(follower_rows),
                                     "exact_counts": sum(1 for item in follower_rows if item["followers"] is not None),
                                     "verified_observed": sum(1 for item in follower_rows if item["verified"]),
                                     "bands": bands, "latest_read": recent_read[0] if recent_read else None,
                                     "latest_profile_total": profile["followers"] if profile else None},
            "relationships": relationships}
