"""上游算法更新跟踪（06D）。

X 在 2026-01 开源了 For You 的推荐算法（`xai-org/x-algorithm`，Rust + Python 重写），
并说大约每四周更新一次。这个模块每天读一次那个仓库最新一条提交，和上次看到的那条
比：不一样就是「上游动过」，然后把这次动了哪几个目录、哪几个模块算出来。

三条不许越过的线：

1. **没读到就不写事件。** 网络不通、被限流、仓库改名——这些只记在这次检查的结果里
   （`last_status` + `last_note`），不会变成一条「改了什么」。要的是真实提交，编一条
   比空着更坏。
2. **第一次检查只记起点。** 没有基线时把当前的 SHA 记下来（`baseline=1`），**不生成
   事件**：否则一开页就会把整个仓库的历史当成本次更新报出来。
3. **看不到的地方写「看不到」。** 两次检查之间如果还有别的提交，本机只知道有、不知道
   有几条（`intervening` 留空）；一次提交的文件清单 GitHub 最多列 300 条，超了就把
   总数和「只列了前 300 条」一起写下来。空不是 0。

网络那一下是可注入的（`fetch`），测试用假响应跑遍 304 / 限流 / 5xx / 清单拿不到，
一个字节都不出网。
"""

import json
import time
import urllib.parse
import uuid
from datetime import datetime

import httpx

REPO = "xai-org/x-algorithm"
BRANCH = "main"
API = "https://api.github.com"
USER_AGENT = "persona-studio-algorithm-watch"
RULE_VERSION = "06D.1"

# 方案 §4.10 要的是「上游动了就告诉我」，没说多勤。这个仓库自己说大约每四周更新一次，
# 所以一天一次远远够；检查本身只读一个公开仓库的一个提交。
CHECK_INTERVAL = 86400
# GitHub 的提交详情一次最多列 300 个文件（它自己的上限，不是我们定的）。
FILE_CAP = 300
# 每个模块在页面上列几条路径。列不满的部分用计数说明，不假装列全了。
PATHS_PER_MODULE = 8
EVENT_LIMIT = 30

# 四个模块的名字与它们各自的路径前缀，照上游仓库的目录结构。顺序就是页面上的顺序：
# 召回在前、排序在后，读的人顺着信息流的流向看。
MODULES = (
    ("home-mixer", "信息流编排",
     "候选怎么被拼成一条 For You、每条能带多少权重，在这里定"),
    ("phoenix", "排序模型（Phoenix）",
     "给每条候选打分的 Transformer，分数怎么来的在这里"),
    ("thunder", "关注流召回",
     "你关注的人发的帖怎么进候选池，关注关系值不值钱看这里"),
    ("candidate-pipeline", "候选流水线",
     "召回→过滤→排序的公共骨架，模块之间怎么交接在这里"),
)
MODULE_LABEL = {key: label for key, label, _ in MODULES}
MODULE_WHY = {key: why for key, _, why in MODULES}
OTHER = "other"
OTHER_LABEL = "四个模块之外"
OTHER_WHY = "文档、脚本、测试这类改动；四个模块的行为不受它影响"
MODULE_ORDER = [key for key, _, _ in MODULES] + [OTHER]

# 任务的三个状态由人给，名字照方案 §4.10 的说法。
STATUSES = ("proposed", "watching", "adopted", "ignored")
STATUS_LABEL = {
    "proposed": "待判断",
    "watching": "先看着",
    "adopted": "跟着改",
    "ignored": "不管",
}

# 一次检查的结果。前三样是「查到了」，其余都是「这一次没查成」，面板要把这两类
# 分成两句话——「上游没动」和「我们看不到上游」不是一个意思。
CHECK_LABEL = {
    "baseline": "第一次检查，只记下起点",
    "not_modified": "查过了，上游没有变化",
    "ok": "查过了，还是上次那条提交",
    "changed": "查过了，上游动过",
    "rate_limited": "被 GitHub 限流，这一次没查成",
    "unauthorized": "凭据被拒，这一次没查成",
    "forbidden": "被 GitHub 拒绝，这一次没查成",
    "not_found": "仓库不存在（可能改名或转私有），这一次没查成",
    "upstream_error": "GitHub 返回错误，这一次没查成",
    "network_error": "连不上 GitHub，这一次没查成",
    "bad_response": "GitHub 返回的内容读不懂，这一次没查成",
    "internal_error": "本机出错，这一次没查成",
}
CHECKED = ("baseline", "not_modified", "ok", "changed")
# 「这一次没查成」里属于碰运气的那几种：过一会儿再试多半就好了。所以自动检查遇到
# 这几种只等一小时，而不是等满一天。剩下几种（仓库改名、凭据被拒）再试也是一样的
# 结果，就按正常间隔来。
TRANSIENT = ("network_error", "upstream_error", "rate_limited")


def thresholds():
    return {"check_interval": CHECK_INTERVAL, "file_cap": FILE_CAP,
            "paths_per_module": PATHS_PER_MODULE, "events_shown": EVENT_LIMIT}


def check_labels():
    return [{"status": status, "label": label} for status, label in CHECK_LABEL.items()]


def repository(store):
    return str(store.get("algorithm_repo", "") or REPO)


def watching(store):
    return bool(store.get("algorithm_watch", True))


def _blank(repo):
    """还没查过时也要答得上来。

    面板拿不到这一份就得自己编一个「没有上游」，那是两件事：这里说的是「本机还没
    看过」，不是「上游不存在」。
    """
    return {"repo": repo, "branch": BRANCH, "etag": "", "last_sha": "",
            "last_commit_at": None, "baseline": 0, "last_checked": None,
            "last_ok": None, "last_status": "", "last_note": "",
            "last_release_tag": "", "checked_count": 0, "created": 0}


def upstream(store, repo=None):
    repo = repo or repository(store)
    rows = store.rows("SELECT * FROM algorithm_upstream_state WHERE repo=?", (repo,))
    return dict(rows[0]) if rows else _blank(repo)


def github_fetch(url, headers):
    """读一个公开仓库，只用 GET。返回 (状态码, 响应头, 解析后的正文)。"""
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get(url, headers=headers)
        if response.status_code == 304:
            return 304, dict(response.headers), None
        try:
            body = response.json()
        except ValueError:
            body = None
        return response.status_code, dict(response.headers), body


def _header(headers, name):
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return str(value)
    return ""


def _moment(text):
    """GitHub 给的 ISO8601 时间戳换成秒；读不出来就留空。"""
    try:
        return int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def module_of(path):
    first = str(path).split("/", 1)[0].strip().lower()
    return first if first in MODULE_LABEL else OTHER


def group(files):
    """把文件清单按模块归拢。计数是完整的，路径只列前几条。"""
    buckets = {}
    for item in files:
        key = module_of(item["path"])
        bucket = buckets.get(key)
        if bucket is None:
            bucket = buckets[key] = {"module": key, "label": MODULE_LABEL.get(key, OTHER_LABEL),
                                     "why": MODULE_WHY.get(key, OTHER_WHY),
                                     "files": 0, "added": 0, "removed": 0,
                                     "paths": [], "paths_truncated": False}
        bucket["files"] += 1
        bucket["added"] += item["added"]
        bucket["removed"] += item["removed"]
        if len(bucket["paths"]) < PATHS_PER_MODULE:
            bucket["paths"].append(item["path"])
        else:
            bucket["paths_truncated"] = True
    return [buckets[key] for key in MODULE_ORDER if key in buckets]


def summarize(modules, listed, total, complete):
    """本机算出来的「改了什么」。只写算得出来的话。"""
    if not modules:
        return "这次提交的文件清单是空的，所以改了什么看不到。"
    touched = "、".join(
        f"{item['label']} {item['files']} 个文件（+{item['added']} / −{item['removed']}）"
        for item in modules)
    parts = [f"动过 {len(modules)} 个模块：{touched}。"]
    if complete:
        moved = {item["module"] for item in modules}
        quiet = [label for key, label, _ in MODULES if key not in moved]
        if quiet:
            parts.append("这次没有动 " + "、".join(quiet) + "。")
        parts.append(f"这次提交一共 {total} 个文件。")
    else:
        # 「没有动 X」只有在清单完整时才成立，所以清单不全就干脆不说这半句。
        parts.append("这次的文件清单不全，所以「哪个模块没被碰过」这句话不成立，这里不写。")
    return "".join(parts)


def _files(fetch, repo, sha):
    """这次提交的文件清单。拿不到就返回空清单 + 一句为什么。"""
    url = f"{API}/repos/{repo}/commits/{sha}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT,
               "X-GitHub-Api-Version": "2022-11-28"}
    try:
        code, _, body = fetch(url, headers)
    except Exception as exc:
        return [], None, f"这条提交的文件清单没拿到（连接出错：{type(exc).__name__}），这次只知道上游动过，不知道动了哪些文件。"
    if code != 200 or not isinstance(body, dict):
        return [], None, f"这条提交的文件清单没拿到（GitHub 返回 {code}），这次只知道上游动过，不知道动了哪些文件。"
    files = [{"path": str(item.get("filename") or ""),
              "status": str(item.get("status") or ""),
              "added": int(item.get("additions") or 0),
              "removed": int(item.get("deletions") or 0)}
             for item in (body.get("files") or []) if item.get("filename")]
    stats = body.get("stats") or {}
    total = stats.get("total") if isinstance(stats.get("total"), int) else None
    if total is None:
        return files, None, (
            f"GitHub 没给这条提交的文件总数，这里列出来 {len(files)} 个，可能不止这些。")
    if len(files) < total:
        return files, total, (
            f"这条提交一共 {total} 个文件，GitHub 一次最多列 {FILE_CAP} 个，"
            f"这里只看到前 {len(files)} 个，剩下的没看到。")
    return files, total, ""


def _remember(store, repo, branch, now, *, sha=None, etag=None, commit_at=None,
              baseline=None, status, note, ok):
    """记下这次检查。状态每次都改；`last_sha` 只在真的前进时改。"""
    with store.db(True) as c:
        row = c.execute("SELECT * FROM algorithm_upstream_state WHERE repo=?", (repo,)).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO algorithm_upstream_state(repo,branch,created,checked_count)"
                " VALUES(?,?,?,0)", (repo, branch, now))
            row = c.execute("SELECT * FROM algorithm_upstream_state WHERE repo=?", (repo,)).fetchone()
        fields = {"branch": branch, "last_checked": now, "last_status": status,
                  "last_note": note, "checked_count": (row["checked_count"] or 0) + 1}
        if ok:
            fields["last_ok"] = now
        if sha is not None:
            fields["last_sha"] = sha
            fields["last_commit_at"] = commit_at
        if etag is not None:
            fields["etag"] = etag
        if baseline is not None:
            fields["baseline"] = 1 if baseline else 0
        sets = ",".join(f"{key}=?" for key in fields)
        c.execute(f"UPDATE algorithm_upstream_state SET {sets} WHERE repo=?",
                  (*fields.values(), repo))


def _result(status, repo, branch, **extra):
    return {"status": status, "label": CHECK_LABEL.get(status, status), "repo": repo,
            "branch": branch, "checked": status in CHECKED,
            "changed": status == "changed", "event_id": "", "sha": "",
            "summary": "", "notice": None, **extra}


def _failed(store, repo, branch, status, note, now):
    """一次没查成。写状态、发一次通知，但**不写事件**。"""
    _remember(store, repo, branch, now, status=status, note=note, ok=False)
    day = datetime.utcfromtimestamp(now).date().isoformat()
    return _result(status, repo, branch, note=note, notice={
        "key": f"algorithm:{repo}:fail:{status}:{day}",
        "title": "上游算法这一次没查成",
        "text": f"{repo}\n{note}\n\n不是「上游没动」——是这一次没读到。",
    })


def _rate_status(code, headers):
    if code == 429:
        return "rate_limited"
    if code == 403:
        return "rate_limited" if _header(headers, "x-ratelimit-remaining") == "0" else "forbidden"
    if code == 401:
        return "unauthorized"
    if code == 404:
        return "not_found"
    if 500 <= code < 600:
        return "upstream_error"
    return "bad_response"


def check(store, *, fetch=None, now=None):
    """读一次上游。返回这次的结果；只有真的动过才写事件、才要发通知。"""
    fetch = fetch or github_fetch
    now = int(time.time()) if now is None else int(now)
    repo = repository(store)
    prior = upstream(store, repo)
    branch = prior["branch"] or BRANCH
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT,
               "X-GitHub-Api-Version": "2022-11-28"}
    if prior["etag"]:
        headers["If-None-Match"] = prior["etag"]
    url = (f"{API}/repos/{repo}/commits?"
           + urllib.parse.urlencode({"sha": branch, "per_page": 1}))
    try:
        code, response_headers, body = fetch(url, headers)
    except Exception as exc:
        return _failed(store, repo, branch, "network_error",
                       f"连不上 GitHub（{type(exc).__name__}）。", now)

    if code == 304:
        note = "GitHub 说这个分支自上次检查以来没有变化。"
        _remember(store, repo, branch, now, status="not_modified", note=note, ok=True)
        return _result("not_modified", repo, branch, note=note,
                       sha=prior["last_sha"])

    if code != 200 or not isinstance(body, list) or not body or not isinstance(body[0], dict):
        status = _rate_status(code, response_headers) if code != 200 else "bad_response"
        note = (f"GitHub 返回 {code}。" if code != 200
                else "GitHub 返回的内容里没有提交。")
        return _failed(store, repo, branch, status, note, now)

    commit = body[0]
    sha = str(commit.get("sha") or "")
    if not sha:
        return _failed(store, repo, branch, "bad_response",
                       "GitHub 返回的提交没有 SHA。", now)
    etag = _header(response_headers, "etag") or None
    detail = commit.get("commit") or {}
    when = _moment((detail.get("author") or {}).get("date")
                   or (detail.get("committer") or {}).get("date"))
    message = str(detail.get("message") or "").strip()
    author = str((detail.get("author") or {}).get("name")
                 or (commit.get("author") or {}).get("login") or "")

    if not prior["last_sha"]:
        note = (f"第一次检查，把 {sha[:7]} 记成了起点。以后的更新都拿它比，"
                "所以这一次不报事件。")
        _remember(store, repo, branch, now, sha=sha, etag=etag, commit_at=when,
                  baseline=True, status="baseline", note=note, ok=True)
        return _result("baseline", repo, branch, note=note, sha=sha)

    if sha == prior["last_sha"]:
        note = f"还是上次看到的那条提交（{sha[:7]}）。"
        _remember(store, repo, branch, now, etag=etag, status="ok", note=note, ok=True)
        return _result("ok", repo, branch, note=note, sha=sha)

    parent = ""
    parents = commit.get("parents") or []
    if parents and isinstance(parents[0], dict):
        parent = str(parents[0].get("sha") or "")
    # 父提交不是上次看到的那条，就说明中间还有别的提交。有几条？不知道。
    # 所以留空，不写 0 —— 0 的意思是「确实没有」。
    intervening = 0 if parent and parent == prior["last_sha"] else None
    files, total, gap = _files(fetch, repo, sha)
    if intervening is None:
        missed = (f"上次看的是 {prior['last_sha'][:7]}，这条提交的父提交是 "
                  f"{parent[:7] or '拿不到'}：中间还有别的提交，本机只读得到最新那一条，"
                  "有几条看不到。")
        gap = f"{gap}{missed}" if gap else missed
    listed = len(files)
    complete = total is not None and listed >= total
    modules = group(files)
    summary = summarize(modules, listed, total, complete)
    ident = uuid.uuid4().hex
    url_html = f"https://github.com/{repo}/commit/{sha}"
    with store.db(True) as c:
        c.execute(
            "INSERT INTO algorithm_events(id,repo,sha,branch,commit_at,author,message,"
            "url,parent_sha,intervening,files_json,modules_json,files_listed,"
            "files_total,files_truncated,summary,coverage_gap,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident, repo, sha, branch, when, author, message, url_html, parent,
             intervening, json.dumps(files, ensure_ascii=False),
             json.dumps(modules, ensure_ascii=False), listed, total,
             1 if gap else 0, summary, gap, now))
        c.execute(
            "INSERT INTO algorithm_proposals(event_id,repo,sha,status,notify_status,"
            "created,updated) VALUES(?,?,?,'proposed','pending',?,?)",
            (ident, repo, sha, now, now))
    note = f"上游动了：{sha[:7]}。"
    _remember(store, repo, branch, now, sha=sha, etag=etag, commit_at=when,
              baseline=False, status="changed", note=note, ok=True)
    return _result("changed", repo, branch, note=note, sha=sha, event_id=ident,
                   summary=summary, message=message, commit_at=when, url=url_html,
                   notice={"key": f"algorithm:{repo}:{sha}",
                           "title": "上游算法动过",
                           "text": f"{repo} {sha[:7]}\n{summary}\n\n{message[:600]}\n{url_html}"})


def _decoded(row):
    """JSON 列还原成结构再交给上层：面板不该拿到一串要自己 parse 的文本。"""
    out = dict(row)
    for key in ("files_json", "modules_json"):
        try:
            out[key] = json.loads(out.get(key) or "[]")
        except (TypeError, ValueError):
            out[key] = []
    return out


def events(store, repo=None, limit=EVENT_LIMIT):
    repo = repo or repository(store)
    return [_decoded(row) for row in store.rows(
        "SELECT e.*, p.status AS proposal_status, p.notify_status, p.notified_at,"
        " p.engine, p.engine_error, p.impact, p.impact_at,"
        " (p.analysis <> '') AS has_analysis"
        " FROM algorithm_events e LEFT JOIN algorithm_proposals p ON p.event_id=e.id"
        " WHERE e.repo=? ORDER BY e.commit_at IS NULL, e.commit_at DESC, e.created DESC"
        " LIMIT ?", (repo, limit))]


def proposal(store, event_id):
    rows = store.rows(
        "SELECT e.*, p.status AS proposal_status, p.analysis, p.engine, p.engine_error,"
        " p.impact, p.impact_at, p.notify_status, p.notify_note, p.notified_at,"
        " p.created AS proposal_created, p.updated AS proposal_updated"
        " FROM algorithm_events e LEFT JOIN algorithm_proposals p ON p.event_id=e.id"
        " WHERE e.id=?", (event_id,))
    return _decoded(rows[0]) if rows else None


def by_sha(store, sha):
    rows = store.rows("SELECT id FROM algorithm_events WHERE sha=?", (sha,))
    return rows[0]["id"] if rows else ""


def attach_analysis(store, event_id, text, engine, error, now=None):
    """引擎写的那一半。失败也要写下来，否则「没分析」和「分析失败」长得一样。"""
    now = int(time.time()) if now is None else int(now)
    with store.db(True) as c:
        c.execute("UPDATE algorithm_proposals SET analysis=?,engine=?,engine_error=?,"
                  "updated=? WHERE event_id=?", (text, engine, error, now, event_id))


def mark_notified(store, event_id, status, note, now=None):
    now = int(time.time()) if now is None else int(now)
    with store.db(True) as c:
        c.execute("UPDATE algorithm_proposals SET notify_status=?,notify_note=?,"
                  "notified_at=?,updated=? WHERE event_id=?",
                  (status, note, now, now, event_id))


def judge(store, sha, *, status, impact, now=None):
    """人给的那一半：这次改动对我们意味着什么、要不要跟着改。"""
    now = int(time.time()) if now is None else int(now)
    event_id = by_sha(store, sha)
    if not event_id:
        return ""
    with store.db(True) as c:
        c.execute("UPDATE algorithm_proposals SET status=?,impact=?,impact_at=?,"
                  "updated=? WHERE event_id=?",
                  (status, impact, now, now, event_id))
    return event_id


def proposal_text(event, files):
    """交给引擎的那一段。只有已经写下来的事实，没有让它猜的部分。"""
    lines = [f"上游仓库 {event['repo']} 更新了一条提交 {event['sha'][:7]}",
             f"提交说明：{event['message'] or '（没有说明）'}",
             f"本机算出来的改动：{event['summary']}"]
    for item in files:
        lines.append(f"- {item['path']}（+{item['added']} / −{item['removed']}）")
    if event.get("coverage_gap"):
        lines.append(f"这次没看到的部分：{event['coverage_gap']}")
    return "\n".join(lines)


def pending_notify(store, repo=None, limit=5):
    """等通知的事件。发送本身在 Notifier 里，这里只挑出来。"""
    repo = repo or repository(store)
    return store.rows(
        "SELECT e.id,e.repo,e.sha,e.summary,e.message,e.url,p.notify_status"
        " FROM algorithm_events e JOIN algorithm_proposals p ON p.event_id=e.id"
        " WHERE e.repo=? AND p.notify_status='pending'"
        " ORDER BY e.created LIMIT ?", (repo, limit))
