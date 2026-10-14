"""Assemble one weekly diagnosis page from data the studio actually observed.

Section 4.8 asks for a short report: three things actually seen, the single
problem most worth fixing first, the result of last week's attempt, at most
three concrete actions, and — explicitly — which data is still out of reach.

Nothing here calls a model or the network, and nothing is interpolated. A value
that was never observed stays `None` and renders as 看不到 / 暂无数据; it is
never written as 0. Where the payload cannot answer a question at all (whether a
post is an original or a quote), the question is listed as unanswerable instead
of guessed.

Units: analytics windows and post timestamps are epoch milliseconds; everything
else in the local schema — profile snapshots, the experiment ledger added in
migration 10 — is epoch seconds. The only place the two meet is the follower
boundary, so the conversion lives inside `_followers_at`.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WEEK_DAYS = 7
DEFAULT_TIMEZONE = "Asia/Shanghai"
CONTENT_SOURCE = "subscription_content_query"
FOLLOW_SERIES = ("legacy_current_follow_metrics", "current_time_series")
FOLLOW_METRICS = ("Follow", "Unfollow")

# A boundary reading is only usable when a real snapshot sits close to it.
MAX_BOUNDARY_STALENESS_MS = 2 * 86400 * 1000
MIN_POSTS_FOR_CONFIDENCE = 5

UNSEEN = "看不到"
NO_DATA = "暂无数据"

QUALITY_OK = "ok"
QUALITY_LOW = "low_confidence"
QUALITY_INSUFFICIENT = "insufficient_data"

METHOD = (
    "周界＝ISO 周（周一 00:00 → 下周一 00:00，时区见 timezone）；"
    "粉丝净增＝本周末粉丝数 − 本周初粉丝数（§4.8），取该时刻之前最近一次主页快照，"
    "早于 2 天即视为过期并写「看不到」；回复与非回复按 Content 响应里的 "
    "reply_to_results 判定；当前窗口最多只统计到已存下的订阅分析读数，"
    "生成时间见 generated_at。"
)

FOLLOW_CAVEAT = (
    "这只是观察值：页面卡片口径与 current_time_series / legacy_* 序列尚未实测对齐，"
    "不能当作 §4.8 定义的「粉丝净增」，也不能把 current_time_series 与 hourly_backfill 相加。"
)

NO_COMPARABLE = "对标账号做了什么：" + NO_DATA + "（06B 未上线）。"


def timezone(store):
    name = str(store.get("timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def week_start_of(day: date) -> date:
    """ISO weeks run Monday→Sunday.

    §4.8 defines only 本周末 − 本周初 and never names a start day, so the choice
    is stated in the report instead of being left implicit.
    """
    return day - timedelta(days=day.weekday())


def week_bounds(week_start: date, zone) -> tuple[int, int]:
    start = datetime.combine(week_start, datetime.min.time(), zone)
    end = datetime.combine(week_start + timedelta(days=WEEK_DAYS), datetime.min.time(), zone)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def last_complete_week(now_ms: int, zone) -> date:
    """The most recent week that has already ended, so no day is still moving."""
    today = datetime.fromtimestamp(now_ms / 1000, zone).date()
    return week_start_of(today) - timedelta(days=WEEK_DAYS)


def resolve_week(store, now_ms: int, week_start=None) -> date:
    if week_start is None or week_start == "":
        return last_complete_week(now_ms, timezone(store))
    if not isinstance(week_start, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", week_start):
        raise ValueError("week_start_invalid")
    day = date.fromisoformat(week_start)
    if day != week_start_of(day):
        raise ValueError("week_start_not_monday")
    return day


def report_id(account: str, week_start: date) -> str:
    slug = re.sub(r"[^a-z0-9_]", "", str(account or "").lower())[:15]
    return f"w{week_start.strftime('%Y%m%d')}-{slug}"


def _merged_posts(rows) -> list:
    """One entry per post: the newest observation wins, and a later row whose
    `kind` is NULL never erases an earlier classification."""
    merged = {}
    for row in rows:
        current = merged.get(row["post_id"])
        if current is None:
            merged[row["post_id"]] = dict(row)
            continue
        if (row["observed_at"] or 0) > (current["observed_at"] or 0):
            merged[row["post_id"]] = dict(row, kind=row["kind"] or current["kind"])
        elif not current["kind"] and row["kind"]:
            current["kind"] = row["kind"]
    return [merged[key] for key in sorted(merged)]


def _plain(value):
    """The series and metrics arrive as REAL; 25.0 is the same count as 25 and
    reads as a mistake. Exact integers are shown as integers."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _metric(posts: list, name: str) -> dict:
    known = [item["metrics"].get(name) for item in posts]
    known = [value for value in known
             if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return {"total": _plain(sum(known)) if known else None,
            "known": len(known), "counted": len(posts)}


def _followers_at(store, account: str, at_ms: int) -> dict | None:
    """The nearest real follower count at or before a week boundary,
    or None when nothing was ever recorded there.

    The boundary arrives in analytics milliseconds while `profile_snapshots`
    stores epoch seconds, so this is the one place the two units meet.
    """
    at_seconds = at_ms // 1000
    rows = store.rows(
        "SELECT day,observed_at,followers FROM profile_snapshots "
        "WHERE lower(account)=lower(?) AND followers IS NOT NULL AND observed_at<=? "
        "ORDER BY observed_at DESC,id DESC LIMIT 1", (account, at_seconds))
    if not rows:
        return None
    row = rows[0]
    stale = (at_seconds - row["observed_at"]) * 1000 > MAX_BOUNDARY_STALENESS_MS
    return {"value": None if stale else row["followers"], "day": row["day"],
            "observed_at": row["observed_at"], "stale": stale}


def _follow_flow(store, account: str, from_ms: int, to_ms: int) -> list:
    """Daily follow/unfollow counts, kept per series and never summed together."""
    out = []
    for series in FOLLOW_SERIES:
        rows = store.rows(
            "SELECT p.metric_type,p.count FROM analytics_overview_points p "
            "JOIN analytics_overview_runs r ON r.id=p.run_id "
            "WHERE lower(r.account)=lower(?) AND p.series=? AND p.metric_type IN (?,?) "
            "AND p.timestamp_ms>=? AND p.timestamp_ms<?",
            (account, series, FOLLOW_METRICS[0], FOLLOW_METRICS[1], from_ms, to_ms))
        totals = {}
        for name in FOLLOW_METRICS:
            values = [row["count"] for row in rows if row["metric_type"] == name
                      and isinstance(row["count"], (int, float))]
            totals[name] = _plain(sum(values)) if values else None
        if not rows:
            continue
        net = (totals["Follow"] - totals["Unfollow"]
               if totals["Follow"] is not None and totals["Unfollow"] is not None else None)
        out.append({"series": series, "follows": totals["Follow"],
                    "unfollows": totals["Unfollow"], "net": net,
                    "points": len(rows), "caveat": FOLLOW_CAVEAT})
    return out


def collect(store, *, account: str, week_start: date, now_ms: int) -> dict:
    """Read every fact the report is allowed to use. No value is invented."""
    zone = timezone(store)
    from_ms, to_ms = week_bounds(week_start, zone)
    week_end = week_start + timedelta(days=WEEK_DAYS - 1)
    runs = store.rows(
        "SELECT id,window_from,window_to,observed_at,received_count,parsed_count,"
        "gap_count,at_request_limit FROM analytics_runs "
        "WHERE source=? AND lower(account)=lower(?) "
        "AND substr(window_to,1,10)>=? AND substr(window_from,1,10)<=? "
        "ORDER BY observed_at", (CONTENT_SOURCE, account,
                                 week_start.isoformat(), week_end.isoformat()))
    raw_posts = store.rows(
        "SELECT m.post_id,m.published_at_ms,m.text,m.metrics_json,m.kind,r.observed_at "
        "FROM analytics_post_metrics m JOIN analytics_runs r ON r.id=m.run_id "
        "WHERE lower(r.account)=lower(?) AND m.published_at_ms>=? AND m.published_at_ms<?",
        (account, from_ms, to_ms))
    posts = []
    for row in _merged_posts(raw_posts):
        metrics = json.loads(row["metrics_json"] or "{}")
        posts.append({"post_id": row["post_id"], "published_at_ms": row["published_at_ms"],
                      "text": row["text"], "kind": row["kind"],
                      "metrics": metrics if isinstance(metrics, dict) else {}})
    by_kind = {"post": 0, "reply": 0}
    for item in posts:
        if item["kind"] in by_kind:
            by_kind[item["kind"]] += 1
    observations = _measurements(store, [item["post_id"] for item in posts])
    gaps = store.rows(
        "SELECT g.reason,count(*) AS n FROM analytics_coverage_gaps g "
        "JOIN analytics_runs r ON r.id=g.run_id "
        "WHERE lower(r.account)=lower(?) AND r.source=? "
        "AND substr(r.window_to,1,10)>=? AND substr(r.window_from,1,10)<=? "
        "GROUP BY g.reason", (account, CONTENT_SOURCE,
                              week_start.isoformat(), week_end.isoformat()))
    # The experiment ledger stores epoch seconds, like the rest of the schema.
    experiments = store.rows(
        "SELECT id,hypothesis,metric,status,started_at,planned_end_at,ended_at,"
        "result_json,decision FROM experiments "
        "WHERE lower(account)=lower(?) AND started_at<? AND planned_end_at>? "
        "ORDER BY started_at DESC", (account, to_ms // 1000, from_ms // 1000))
    start = _followers_at(store, account, from_ms)
    end = _followers_at(store, account, to_ms)
    net, reason = None, ""
    if start is None or end is None:
        reason = ("本周初或本周末没有可用的主页粉丝快照；主页粉丝数是每日采集的，"
                  "从开始采集那天起才会有。")
    elif start["value"] is None or end["value"] is None:
        which = "本周初" if start["value"] is None else "本周末"
        stamp = start if start["value"] is None else end
        reason = (f"{which}最近的粉丝快照是 {stamp['day']}，早于该时刻超过 2 天，"
                  "不能当作当周的端点值。")
    else:
        net = end["value"] - start["value"]
    # The parser writes the singular "Bookmark"; a plural here would silently
    # read every post as having none and report that as 看不到.
    metrics = {name: _metric(posts, name)
               for name in ("Impressions", "ProfileVisits", "Follows", "Engagements",
                            "Likes", "Bookmark", "Retweets", "Replies", "UrlClicks")}
    return {
        "week_start": week_start.isoformat(), "week_end": week_end.isoformat(),
        "timezone": str(store.get("timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE),
        "from_ms": from_ms, "to_ms": to_ms, "collected_at": now_ms,
        "runs": runs, "run_count": len(runs),
        "posts": {"total": len(posts), "post": by_kind["post"], "reply": by_kind["reply"],
                  "unclassified": len(posts) - by_kind["post"] - by_kind["reply"],
                  "metrics": metrics, "items": sorted(
                      posts, key=lambda item: item["published_at_ms"])},
        "followers": {"start": start, "end": end, "net": net, "reason": reason},
        "follow_flow": _follow_flow(store, account, from_ms, to_ms),
        "coverage": {"gaps": gaps, "gap_total": sum(row["n"] for row in gaps),
                     "at_request_limit": sum(1 for row in runs if row["at_request_limit"])},
        "measurements": observations,
        "experiments": experiments,
    }


def _measurements(store, post_ids: list) -> dict:
    """24 h / 72 h readings for the week's own posts, kept per post."""
    wanted = [post_id for post_id in post_ids if re.fullmatch(r"\d+", str(post_id or ""))]
    if not wanted:
        return {"available": 0, "items": []}
    marks = ",".join("?" for _ in wanted)
    rows = store.rows(
        f"SELECT tweet_id,target_window,window_status,views,likes,replies,reposts,"
        f"quotes,bookmarks,observed_at FROM post_metric_snapshots "
        f"WHERE tweet_id IN ({marks}) AND target_window IN ('24h','72h') "
        f"ORDER BY tweet_id,target_window", tuple(wanted))
    return {"available": len(rows), "items": rows}


def _snippet(text: str, limit: int = 40) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _observations(facts: dict) -> list:
    """At most three claims, each one backed by a stored number."""
    posts = facts["posts"]
    metrics = posts["metrics"]
    out = []
    if posts["total"]:
        line = f"本周统计到 {posts['total']} 条推文"
        if posts["unclassified"] == posts["total"]:
            # Saying "非回复 0 条、回复 0 条" here would read as a measurement of
            # zero rather than as not having looked yet.
            line += "，都早于本次分类，暂时分不出回复与非回复"
        else:
            line += f"：非回复 {posts['post']} 条、回复 {posts['reply']} 条"
            if posts["unclassified"]:
                line += f"，另有 {posts['unclassified']} 条早于本次分类，无法判断是不是回复"
        impressions = metrics["Impressions"]
        if impressions["total"] is not None:
            line += f"；这 {impressions['counted']} 条有展示数，共 {impressions['total']} 次展示"
        out.append(line + "。")
    followers = facts["followers"]
    if followers["net"] is not None:
        out.append(f"粉丝净增 {followers['net']:+d}（本周初 {followers['start']['value']} → "
                   f"本周末 {followers['end']['value']}）。")
    ranked = [item for item in posts["items"]
              if isinstance(item["metrics"].get("Impressions"), (int, float))]
    if ranked:
        top = max(ranked, key=lambda item: item["metrics"]["Impressions"])
        detail = [f"{int(top['metrics']['Impressions'])} 次展示"]
        visits = top["metrics"].get("ProfileVisits")
        if isinstance(visits, (int, float)):
            detail.append(f"{int(visits)} 次主页访问")
        follows = top["metrics"].get("Follows")
        if isinstance(follows, (int, float)):
            detail.append(f"{int(follows)} 个关注")
        out.append(f"本周展示最多的一条是「{_snippet(top['text'])}」，" + "、".join(detail) + "。")
    return out[:3]


def _issue(facts: dict) -> dict | None:
    """§4.8's first thing to look at: did the readers who arrived fit the profile?

    It fires only when the two numbers that make the claim are both stored. The
    remaining four slots need a benchmark or a human judgment and stay empty
    rather than filled with a guess.
    """
    posts = facts["posts"]
    followers = facts["followers"]
    impressions = posts["metrics"]["Impressions"]["total"]
    net = followers["net"]
    if not impressions or net is None or net > 0:
        return None
    visits = posts["metrics"]["ProfileVisits"]["total"]
    evidence = f"本周 {int(impressions)} 次展示"
    if visits is not None:
        evidence += f"只带来 {int(visits)} 次主页访问"
    evidence += (f"，粉丝净增 {net:+d}（{followers['start']['value']} → "
                 f"{followers['end']['value']}）。看的人有一些，留下来的人没有。")
    return {"code": "readers_vs_profile", "title": "吸引来的读者和主页对不上",
            "evidence": evidence, "benchmark": "", "hypothesis": "",
            "alternative": "",
            "action": "把主页简介和置顶换成与本周展示量最高的那条推文同一主题，"
                      "让点进来的人能对上；下周只改这一件。"}


def _unavailable(facts: dict) -> list:
    posts = facts["posts"]
    metrics = posts["metrics"]
    out = []
    if facts["followers"]["net"] is None:
        out.append("粉丝净增：" + UNSEEN + "。" + facts["followers"]["reason"])
    missing = [name for name in ("Impressions", "ProfileVisits", "Follows")
               if metrics[name]["total"] is None]
    if not posts["total"]:
        out.append("本周没有订阅分析读数覆盖这段时间，展示、主页访问、关注都" + UNSEEN + "。")
    elif missing:
        out.append("、".join(missing) + "：" + UNSEEN + "（本周有读数的推文里没有这几项）。")
    if posts["unclassified"]:
        out.append(f"回复与非回复的分类：本周有 {posts['unclassified']} 条推文在这一次"
                   "分类之前就已入库，要等下一次内容读取才会补上。")
    out.append("原创与引用无法区分：Content 响应的条目里没有区分两者的字段，"
               "只能分出「回复」与「非回复」。")
    out.append(NO_COMPARABLE)
    if not facts["experiments"]:
        out.append("上周尝试的结果：" + NO_DATA + "（还没有登记过实验，§4.8 的 14 天实验尚未开始）。")
    return out


def _quality(facts: dict) -> str:
    if not facts["run_count"] or not facts["posts"]["total"]:
        return QUALITY_INSUFFICIENT
    if (facts["followers"]["net"] is None
            or facts["posts"]["total"] < MIN_POSTS_FOR_CONFIDENCE):
        return QUALITY_LOW
    return QUALITY_OK


def compose(facts: dict) -> dict:
    """Turn facts into the §4.8 page. No fact is added that `collect` did not see."""
    issue = _issue(facts)
    issues = [issue] if issue else []
    observed = _observations(facts)
    report = {
        "week_start": facts["week_start"], "week_end": facts["week_end"],
        "timezone": facts["timezone"], "data_quality": _quality(facts),
        "method": METHOD,
        "observed": observed,
        "observed_note": ("" if len(observed) == 3 else
                          f"只拼出 {len(observed)} 件有数据支撑的事，其余见「看不到」。"),
        "issues": issues,
        "priority_note": ("" if issues else
                          "本周没有触发自动判断的问题：§4.8 要求先看「吸引来的读者和主页对不对得上」，"
                          "这一步需要本周的展示数与粉丝净增同时存在，缺任一项就不下结论。"),
        "last_week_result": _last_week(facts),
        "actions": [item["action"] for item in issues],
        "actions_note": ("" if issues else
                         "没有数据支撑的动就不写；本周的动作等你确认问题后再定。"),
        "unavailable": _unavailable(facts),
    }
    return report


def _last_week(facts: dict) -> dict:
    experiments = facts["experiments"]
    if not experiments:
        return {"text": NO_DATA, "items": []}
    items = []
    for row in experiments:
        result = json.loads(row["result_json"] or "{}")
        items.append({"id": row["id"], "hypothesis": row["hypothesis"],
                      "metric": row["metric"], "status": row["status"],
                      "result": result if isinstance(result, dict) else {},
                      "decision": row["decision"]})
    return {"text": "", "items": items}


def generate(store, *, account: str, week_start=None, now_ms: int | None = None) -> dict:
    """Collect, compose and store one report. Returns the stored summary."""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    week = resolve_week(store, now_ms, week_start)
    facts = collect(store, account=account, week_start=week, now_ms=now_ms)
    report = compose(facts)
    rid = report_id(account, week)
    stamp = int(now_ms / 1000)
    with store.db(True) as c:
        c.execute(
            "INSERT INTO weekly_reports(id,account,week_start,week_end,timezone,from_ms,"
            "to_ms,generated_at,data_quality,facts_json,report_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "generated_at=excluded.generated_at,data_quality=excluded.data_quality,"
            "facts_json=excluded.facts_json,report_json=excluded.report_json,"
            "cloud_status='pending',cloud_attempts=0,cloud_retry_at=0,cloud_error=''",
            (rid, account, week.isoformat(), facts["week_end"], facts["timezone"],
             facts["from_ms"], facts["to_ms"], stamp, report["data_quality"],
             json.dumps(facts, ensure_ascii=False, separators=(",", ":")),
             json.dumps(report, ensure_ascii=False, separators=(",", ":"))))
        c.execute("DELETE FROM weekly_report_issues WHERE report_id=?", (rid,))
        for ordinal, item in enumerate(report["issues"]):
            c.execute("INSERT INTO weekly_report_issues VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (rid, ordinal, item["code"], item["title"], item["evidence"],
                       item["benchmark"], item["hypothesis"], item["alternative"],
                       item["action"], stamp))
        store.event("weekly", "周报已生成",
                    {"report_id": rid, "week_start": week.isoformat(),
                     "data_quality": report["data_quality"]}, c)
    return {"report_id": rid, "week_start": week.isoformat(),
            "week_end": facts["week_end"], "data_quality": report["data_quality"],
            "post_count": facts["posts"]["total"],
            "followers_net": facts["followers"]["net"],
            "issue_count": len(report["issues"])}


def load(store, report_id: str) -> dict | None:
    rows = store.rows("SELECT * FROM weekly_reports WHERE id=?", (report_id,))
    if not rows:
        return None
    row = dict(rows[0])
    issues = store.rows(
        "SELECT ordinal,code,title,evidence,benchmark,hypothesis,alternative,action "
        "FROM weekly_report_issues WHERE report_id=? ORDER BY ordinal", (report_id,))
    for field in ("facts_json", "report_json"):
        try:
            row[field[:-5]] = json.loads(row.pop(field) or "{}")
        except ValueError:
            row[field[:-5]] = {}
    row["issues"] = issues
    return row


def listing(store, account: str, limit: int = 20) -> list:
    return store.rows(
        "SELECT id,week_start,week_end,generated_at,data_quality FROM weekly_reports "
        "WHERE lower(account)=lower(?) ORDER BY week_start DESC LIMIT ?",
        (account, max(1, min(int(limit), 100))))
