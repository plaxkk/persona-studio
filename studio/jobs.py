from __future__ import annotations
import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .engines import Engines, EngineRequest, EngineError
from .security import Secrets, sanitize
from .xreader import XReader, ReadError
from .inspiration import collect_work
from .sync_status import failed as sync_failed
from .browser_reads import BrowserReads
from .analytics_browser_reads import AnalyticsBrowserReads
from .growth_browser_reads import GrowthBrowserReads
from .interactions import candidate_ids, reply_block
from .weekly import generate as generate_weekly, last_complete_week
from .weekly import timezone as weekly_timezone
from .deconstruct import (
    DeconstructError,
    deconstruct,
    measure as deconstruct_measure,
    request_text as deconstruct_text,
)
from . import algorithm_watch
from .notify import Notifier


class JobError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Jobs:
    def __init__(self, store):
        self.store = store
        self.engines = Engines(store)
        self.secrets = Secrets(store.root)
        # worker 在这个对象建好之后把那个 Telegram 客户端补进来（见 worker.py）。
        self.notifier = Notifier(store)
        self.last_measurement_schedule = 0
        self.last_algorithm_schedule = 0

    def enqueue(self, kind, payload=None, automatic=False, dedupe=None,
                available=None):
        payload = dict(payload or {})
        now = int(time.time())
        ident = uuid.uuid4().hex
        with self.store.db(True) as c:
            cfg = {
                r["key"]: json.loads(r["value"])
                for r in c.execute("SELECT * FROM settings")
            }
            # A weekly report and a deconstruction only read local rows; pausing
            # stops outward actions, not the diagnosis the user asked for by
            # hand. Neither one touches X.
            if cfg["paused"] and kind not in ["chat", "probe", "weekly", "deconstruct",
                                              "algorithm_check"]:
                raise JobError("paused")
            if dedupe:
                old = c.execute(
                    "SELECT id FROM tasks WHERE dedupe=? AND status IN ('queued','running')",
                    (dedupe,),
                ).fetchone()
                if old:
                    return old[0]
            if payload.get('creation_id'):
                creation = c.execute('SELECT * FROM creations WHERE id=?', (payload['creation_id'],)).fetchone()
                if not creation or creation['version'] != payload['expected_version'] or creation['draft_id']:
                    raise JobError('creation_conflict')
                if creation['task_id'] and c.execute("SELECT 1 FROM tasks WHERE id=? AND status IN ('queued','running')", (creation['task_id'],)).fetchone():
                    raise JobError('creation_busy')
                payload['idea'] = creation['idea']
                payload['candidate'] = creation['candidate']
            if kind == "reply" and not payload.get("draft_id"):
                blocked = reply_block(self.store, str(payload.get("post_id") or ""), c)
                if blocked:
                    raise JobError(blocked)
            if kind in ["post", "reply", "chat", "probe", "deconstruct"]:
                engine = payload.get("engine") or cfg["engine"]
                if engine not in self.engines.adapters:
                    raise JobError("unsupported_engine")
                row = c.execute(
                    "SELECT * FROM engines WHERE id=?", (engine,)
                ).fetchone()
                if kind != "probe" and row["status"] != "ready":
                    raise JobError("engine_not_ready")
                # A deconstruction is only ever asked for by hand, and it is not
                # a reply, so it must not consume the reply budget by default.
                if kind == "deconstruct" and automatic:
                    raise JobError("not_automatic")
                payload.update(
                    engine=engine,
                    config=json.loads(row["config"]),
                    persona=cfg["persona"],
                )
                day = datetime.now(ZoneInfo(cfg["timezone"])).date().isoformat()
                if automatic:
                    cap = cfg["auto_posts"] if kind == "post" else cfg["auto_replies"]
                    used = c.execute(
                        "SELECT count(*) FROM generation_ledger WHERE day=? AND kind=? AND automatic=1",
                        (day, kind),
                    ).fetchone()[0]
                    if used >= cap:
                        raise JobError("daily_budget")
                c.execute(
                    "INSERT INTO generation_ledger VALUES(?,?,?,?)",
                    (ident, day, kind, int(automatic)),
                )
            if kind == "algorithm_check" and payload.get("analyze"):
                # 检查本身只读一个公开仓库，没有引擎也照跑；只有「这意味着什么」那一半
                # 要引擎。所以引擎没接上只该让这一个开关失败，而不是整条检查停摆。
                engine = payload.get("engine") or cfg["engine"]
                if engine not in self.engines.adapters:
                    raise JobError("unsupported_engine")
                row = c.execute("SELECT * FROM engines WHERE id=?", (engine,)).fetchone()
                if row["status"] != "ready":
                    raise JobError("engine_not_ready")
                payload.update(engine=engine, config=json.loads(row["config"]),
                               persona=cfg["persona"])
                day = datetime.now(ZoneInfo(cfg["timezone"])).date().isoformat()
                c.execute("INSERT INTO generation_ledger VALUES(?,?,?,?)",
                          (ident, day, kind, int(automatic)))
            if kind == "sync":
                if cfg["x_status"] not in ["ready", "partial", "rate_limited"]:
                    raise JobError("x_not_ready")
                if now - cfg["last_sync_requested"] < 60:
                    raise JobError("refresh_cooldown")
                self.store.set("last_sync_requested", now, c)
            c.execute(
                "INSERT INTO tasks(id,kind,payload,status,automatic,dedupe,created,updated,available) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    "queued",
                    int(automatic),
                    dedupe,
                    now,
                    now,
                    max(now, int(available)) if available is not None else now,
                ),
            )
            self.store.event(
                "task",
                "任务已加入队列",
                {"task_id": ident, "kind": kind, "automatic": automatic},
                c,
            )
            if payload.get('creation_id'):
                c.execute("UPDATE creations SET task_id=?,stage='writing',updated=? WHERE id=?", (ident, now, payload['creation_id']))
        return ident

    def recover(self):
        with self.store.db(True) as c:
            for row in c.execute(
                "SELECT id,kind,cancel FROM tasks WHERE status='running'"
            ).fetchall():
                status = (
                    "cancelled"
                    if row["cancel"]
                    else ("queued" if row["kind"] in ("sync", "context", "metric", "profile_metric", "analytics_content", "analytics_overview", "growth_rewards", "growth_followers", "weekly", "benchmark", "deconstruct", "account_quality", "algorithm_check") else "failed")
                )
                c.execute(
                    "UPDATE tasks SET status=?,error=?,updated=? WHERE id=?",
                    (status, "worker_restarted", int(time.time()), row["id"]),
                )
            self.store.event("worker", "后台任务服务已启动", c=c)

    def claim(self):
        with self.store.db(True) as c:
            paused = json.loads(
                c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]
            )
            row = c.execute(
                "SELECT * FROM tasks WHERE status='queued' AND cancel=0 AND available<=? AND (?=0 OR kind IN ('chat','probe','weekly','deconstruct','algorithm_check')) ORDER BY created,id LIMIT 1",
                (int(time.time()), int(paused)),
            ).fetchone()
            if not row:
                return None
            c.execute(
                "UPDATE tasks SET status='running',attempts=attempts+1,updated=? WHERE id=?",
                (int(time.time()), row["id"]),
            )
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            return item

    def alive(self, job):
        rows = self.store.rows("SELECT cancel FROM tasks WHERE id=?", (job["id"],))
        return bool(
            rows
            and not rows[0]["cancel"]
            and (job["kind"] in ["chat", "probe", "weekly", "deconstruct",
                                 "account_quality", "algorithm_check"]
                 or not self.store.get("paused"))
        )

    def finish(self, job, status, result=None, error=""):
        with self.store.db(True) as c:
            c.execute(
                "UPDATE tasks SET status=?,result=?,error=?,updated=? WHERE id=?",
                (
                    status,
                    json.dumps(result or {}, ensure_ascii=False),
                    error,
                    int(time.time()),
                    job["id"],
                ),
            )
            self.store.event(
                "task",
                "任务已完成"
                if status == "succeeded"
                else ("任务已停止" if status == "cancelled" else "任务未完成"),
                {
                    "task_id": job["id"],
                    "kind": job["kind"],
                    "status": status,
                    "error": error,
                },
                c,
            )

    def schedule(self):
        if self.store.get("paused"):
            return
        now = int(time.time())
        if now - self.last_measurement_schedule >= 60:
            self.last_measurement_schedule = now
            self.schedule_measurements(now)
        if (self.store.get("algorithm_watch", True)
                and now - self.last_algorithm_schedule >= 60):
            self.last_algorithm_schedule = now
            self.schedule_algorithm(now)
        if self.store.get("x_status") in [
            "ready",
            "partial",
            "rate_limited",
        ] and now - self.store.get("last_sync_requested") >= self.store.get(
            "sync_interval"
        ):
            try:
                self.enqueue("sync", automatic=True, dedupe="sync")
            except JobError:
                pass
        # Two original drafts/day maximum; spread original generation at least six hours.
        last = (
            self.store.rows(
                "SELECT max(created) AS ts FROM tasks WHERE automatic=1 AND kind='post'"
            )[0]["ts"]
            or 0
        )
        if now - last >= 21600:
            try:
                self.enqueue(
                    "post",
                    {"text": "写一条具体、不重复的人格原创草稿"},
                    True,
                    "auto-post",
                )
            except JobError:
                pass

    def schedule_algorithm(self, now):
        """每天读一次上游仓库（06D）。

        这一条不搭 `schedule_measurements` 的车：它不碰浏览器，也就没有理由跟着插件
        在不在线一起停——上游动没动和本机插件装没装是两件事。一次读失败（连不上、
        被限流）等一小时再来，别一天只试一次；读成功就等满一天。

        间隔看的是「检查结果」而不是「任务状态」：一次被限流的检查是**跑完了**的
        （它记下了自己没读到什么），任务会写成 succeeded，而「上游没动」的任务也是
        succeeded ——两者在任务表里长得一样，分不出来。真实的结果在状态表里。
        """
        key = "algorithm-check"
        if self.store.rows(
                "SELECT id FROM tasks WHERE dedupe=? AND status IN ('queued','running')",
                (key,)):
            return
        state = algorithm_watch.upstream(self.store)
        interval = (3600 if state["last_status"] in algorithm_watch.TRANSIENT
                    else algorithm_watch.CHECK_INTERVAL)
        if state["last_checked"] and now - state["last_checked"] < interval:
            return
        try:
            self.enqueue("algorithm_check", {}, automatic=True, dedupe=key)
        except JobError:
            pass

    def schedule_measurements(self, now):
        browser = BrowserReads(self.store)
        account = self.store.get('x_username','')
        if not account: return
        # The report is assembled from rows already on disk, so it does not need
        # the browser and is not skipped when the connector is offline.
        self.schedule_weekly(now, account)
        if not browser.connected(8): return
        if browser.connected(11):
            # Refresh the most useful Analytics window once a day. A failed
            # browser read waits an hour before another automatic attempt.
            key = 'analytics-content-28'
            latest = self.store.rows(
                "SELECT status,error,created FROM tasks WHERE dedupe=? "
                "ORDER BY created DESC,id DESC LIMIT 1", (key,))
            last = latest[0] if latest else None
            transient = {'source_error', 'browser_offline', 'timeout'}
            interval = (3600 if last and last['status'] == 'failed' and
                        last['error'] in transient else 86400)
            if not last or (last['status'] not in ('queued', 'running') and
                            now - last['created'] >= interval):
                try:
                    self.enqueue('analytics_content', {'days': 28},
                                 automatic=True, dedupe=key)
                except JobError:
                    pass
        if browser.connected(13):
            key = 'analytics-overview-28'
            latest = self.store.rows(
                "SELECT status,error,created FROM tasks WHERE dedupe=? "
                "ORDER BY created DESC,id DESC LIMIT 1", (key,))
            last = latest[0] if latest else None
            transient = {'source_error', 'browser_offline', 'timeout'}
            interval = (3600 if last and last['status'] == 'failed' and
                        last['error'] in transient else 86400)
            if not last or (last['status'] not in ('queued', 'running') and
                            now - last['created'] >= interval):
                try:
                    self.enqueue('analytics_overview', {'days': 28},
                                 automatic=True, dedupe=key)
                except JobError:
                    pass
        if browser.connected((1, 4, 6)):
            key = 'growth-rewards'
            latest = self.store.rows("SELECT status,error,created FROM tasks WHERE dedupe=? ORDER BY created DESC,id DESC LIMIT 1", (key,))
            last = latest[0] if latest else None
            interval = 3600 if last and last['status'] == 'failed' else 86400
            if not last or (last['status'] not in ('queued', 'running') and now - last['created'] >= interval):
                try: self.enqueue('growth_rewards', {}, automatic=True, dedupe=key)
                except JobError: pass
        day = datetime.now(ZoneInfo(self.store.get('timezone','Asia/Shanghai'))).date().isoformat()
        if not self.store.rows("SELECT id FROM profile_snapshots WHERE account=? AND day=? LIMIT 1",(account,day)):
            key = 'profile:' + day
            recent = self.store.rows("SELECT max(created) AS ts FROM tasks WHERE dedupe=?",(key,))[0]['ts'] or 0
            if now-recent >= 3600:
                try: self.enqueue('profile_metric', {'day':day}, automatic=True, dedupe=key)
                except JobError: pass
        publications = self.store.rows("SELECT tweet_id,published_at FROM draft_publications WHERE user_confirmed=1 AND tweet_id IS NOT NULL AND lower(author)=lower(?) ORDER BY COALESCE(user_confirmed_at,0) DESC LIMIT 200",(account,))
        for publication in publications:
            tweet_id = publication['tweet_id']
            snapshots = self.store.rows("SELECT target_window FROM post_metric_snapshots WHERE tweet_id=?",(tweet_id,))
            done = {item['target_window'] for item in snapshots}
            published = publication['published_at']
            target = ('baseline' if 'baseline' not in done else
                      '24h' if published and now>=published+86400 and '24h' not in done else
                      '72h' if published and now>=published+259200 and '72h' not in done else None)
            if not target: continue
            key = f'metric:{tweet_id}:{target}'
            recent = self.store.rows("SELECT max(created) AS ts FROM tasks WHERE dedupe=?",(key,))[0]['ts'] or 0
            if now-recent < 3600: continue
            try: self.enqueue('metric', {'tweet_id':tweet_id,'target_window':target}, automatic=True, dedupe=key)
            except JobError: pass
            break

    async def sync(self, job):
        reader = XReader(self.secrets)
        try:
            browser = BrowserReads(self.store)
            # The signed-in browser remains the primary reader when connected.
            # Older connectors retain the existing read-only API path.
            use_browser = self.store.get("browser_reads_preferred", False) or browser.connected()
            result = await browser.sync(job, self.alive) if use_browser else await reader.sync(self.store.get("x_username"))
        finally:
            await reader.close()
        if not self.alive(job):
            raise EngineError("cancelled")
        now = int(time.time())
        new = []
        added_counts = {name: 0 for name in result['sources']}
        with self.store.db(True) as c:
            for p in result["posts"]:
                inserted = c.execute(
                    "INSERT OR IGNORE INTO posts VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        p["id"],
                        p["author"],
                        p["text"],
                        p["url"],
                        p["kind"],
                        json.dumps(p["context"], ensure_ascii=False),
                        p["status"],
                        p["reason"],
                        now,
                        now,
                    ),
                ).rowcount
                if inserted:
                    source_name = p['context'].get('source')
                    if source_name in added_counts:
                        added_counts[source_name] += 1
                if inserted and p["kind"] != "own" and p["status"] == "new":
                    new.append(p)
            for name, source in result["sources"].items():
                c.execute(
                    "INSERT INTO sources(name,status,last_success,last_attempt,count) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET status=excluded.status,last_success=CASE WHEN excluded.status='ok' THEN excluded.last_success ELSE sources.last_success END,last_attempt=excluded.last_attempt,count=excluded.count",
                    (name, source["status"], now if source["status"] == "ok" else 0,
                     now, added_counts[name]),
                )
            if any(s["status"] == "ok" for s in result["sources"].values()):
                self.store.set("last_sync", now, c)
            if result["complete"]:
                self.store.set("last_complete_sync", now, c)
            self.store.set("x_status", "ready" if result["complete"] else "partial", c)
            self.store.event(
                "sync",
                "互动同步完成" if result["complete"] else "部分来源未能同步",
                {"new": len(new), "sources": result["sources"]},
                c,
            )
        ordered = sorted(
            new,
            key=lambda p: {"reply": 0, "mention": 1, "quote": 2, "timeline": 3}.get(
                p["kind"], 4
            ),
        )
        for p in ordered[: self.store.get("per_round")]:
            # Browser snapshots may omit a parent, quoted post or media. These
            # records are available for review but need enrichment before drafting.
            if p['context'].get('reader') == 'browser':
                continue
            try:
                self.enqueue(
                    "reply",
                    {"post_id": p["id"], "judge": True},
                    True,
                    "auto-reply:" + p["id"],
                )
            except JobError:
                break
        if browser.connected(7):
            pending = []
            for post_id in candidate_ids(self.store):
                item = self.store.rows("SELECT context FROM posts WHERE id=?", (post_id,))[0]
                if not json.loads(item['context']).get('detail_checked'):
                    pending.append(post_id)
            if pending:
                try:
                    self.enqueue("context", {"post_ids": pending}, automatic=True, dedupe="context")
                except JobError:
                    pass
        return {"new": len(new), "complete": result["complete"]}

    def schedule_weekly(self, now, account):
        """One report per finished week, on Monday morning.

        Generating at Monday 00:00 would race the day's own content read, which
        is what picks up Sunday evening; three hours of slack, then one retry a
        day until the week rolls over. The dedupe key carries the week, so a
        hand-made report and this one never both run.
        """
        # The same resolver the report itself uses, so the week this schedules is
        # the week the report will name even if the setting is empty or invalid.
        zone = weekly_timezone(self.store)
        week = last_complete_week(int(now * 1000), zone)
        ready = int((datetime.combine(week + timedelta(days=7), datetime.min.time(), zone)
                     + timedelta(hours=3)).timestamp())
        if now < ready:
            return
        key = 'weekly:' + week.isoformat()
        latest = self.store.rows(
            "SELECT status,created FROM tasks WHERE dedupe=? "
            "ORDER BY created DESC,id DESC LIMIT 1", (key,))
        last = latest[0] if latest else None
        if last and (last['status'] in ('queued', 'running') or now - last['created'] < 86400):
            return
        try:
            self.enqueue('weekly', {'week_start': week.isoformat()},
                         automatic=True, dedupe=key)
        except JobError:
            pass

    async def weekly(self, job):
        """Assemble one weekly diagnosis page from rows already stored locally."""
        account = str(self.store.get("x_username", "") or "")
        if not account:
            raise JobError("account_missing")
        if not self.alive(job):
            raise JobError("cancelled")
        return generate_weekly(self.store, account=account,
                               week_start=job["payload"].get("week_start") or None)

    async def deconstruct(self, job):
        """Explain one benchmark post, then store the document (06B-2).

        Two halves, deliberately not merged: the measured half is computed from
        rows already in the database and is always present, and the interpreted
        half needs the user's own engine. An engine that is down or that answers
        with prose costs the interpretation and nothing else -- the document is
        still written, marked as having no judgement in it, rather than filled in
        with something plausible.
        """
        post_id = str(job["payload"].get("post_id") or "")
        try:
            measured = deconstruct_measure(self.store, post_id)
        except DeconstructError:
            raise JobError("post_missing")
        if not self.alive(job):
            raise JobError("cancelled")
        payload = job["payload"]
        engine = payload["engine"]
        adapter = self.engines.adapters[engine]
        request = EngineRequest(
            "deconstruct",
            deconstruct_text(measured[0], measured[1]),
            payload["persona"],
        )
        raw = ""
        failure = ""
        try:
            result = await adapter.execute(request, payload["config"], job["id"])
            # The document is stored and rendered, so anything the engine echoed
            # back has to be scrubbed the same way every other engine output is.
            raw = sanitize(result.text, self.secrets.all().values())
        except EngineError as exc:
            if exc.code == "cancelled":
                raise
            failure = f"引擎返回 {exc.code}"
        except Exception:
            # The engine is a separate process; its failure costs the
            # interpretation, and the document records that it happened instead
            # of losing the measured half too.
            failure = "引擎执行出错"
        if not self.alive(job):
            raise JobError("cancelled")
        return deconstruct(self.store, post_id, raw=raw, engine=engine,
                           failure=failure, measured=measured)

    async def algorithm_check(self, job):
        """读一次上游仓库（06D）。

        自动那一天一次的那趟不带 payload，所以这个方法空着手也得能跑。`analyze` 是
        人从面板上按的「顺便让引擎读一下」，所以引擎那一半失败只该让应答里少一段话，
        不该把已经读到的事实一起丢掉——事实已经写进表里了。
        """
        if not self.alive(job):
            raise JobError("cancelled")
        # httpx 是同步的，直接 await 会把单线程的 worker 循环按住最多 20 秒。
        # 读浏览器状态那几处也是这么让出去的。
        result = await asyncio.to_thread(algorithm_watch.check, self.store)
        if not self.alive(job):
            raise JobError("cancelled")
        notice = result.get("notice")
        notify = None
        if notice:
            # 「上游动了」和「这一次没查成」都要送出去：查不到上游如果不出声，
            # 页面会一直安静地显示上一次的状态，看起来像「上游一直没动」。
            notify = await self.notifier.send(notice["key"], notice["text"],
                                              notice.get("title"))
            if result.get("event_id"):
                algorithm_watch.mark_notified(
                    self.store, result["event_id"], notify["status"],
                    f"Telegram：{notify['telegram']}；桌面：{notify['desktop']}")
        out = {key: value for key, value in result.items() if key != "notice"}
        out["notify"] = notify
        if job["payload"].get("analyze") and result.get("event_id"):
            out["analysis"] = await self.analyze_algorithm(job, result["event_id"])
        return out

    async def analyze_algorithm(self, job, event_id):
        """让引擎读一条已经记下的改动。读不出来就把那次失败写进去。

        引擎给的是「这可能意味着什么」，不是事实；所以它答不上来不该改事实那半张表。
        """
        row = algorithm_watch.proposal(self.store, event_id)
        if not row:
            return {"written": False, "engine": "", "failure": "事件不在了"}
        payload = job["payload"]
        engine = payload.get("engine") or self.store.get("engine")
        if engine not in self.engines.adapters:
            return {"written": False, "engine": "", "failure": "引擎没接上"}
        files = row["files_json"] if isinstance(row["files_json"], list) else []
        request = EngineRequest("algorithm",
                                algorithm_watch.proposal_text(row, files),
                                payload.get("persona") or {})
        raw = ""
        failure = ""
        try:
            result = await self.engines.adapters[engine].execute(
                request, payload["config"], job["id"])
            raw = sanitize(result.text, self.secrets.all().values())
        except EngineError as exc:
            if exc.code == "cancelled":
                raise
            failure = f"引擎返回 {exc.code}"
        except Exception:
            failure = "引擎执行出错"
        if not self.alive(job):
            raise JobError("cancelled")
        # 引擎这一栏只在这里写：留空就是「还没让引擎分析」，写了名字才是「它答了」，
        # 而「让了但它没答」靠 failure 认——三样不能混成两样。
        algorithm_watch.attach_analysis(self.store, event_id, raw,
                                        engine if raw else "", failure)
        return {"written": bool(raw), "engine": engine if raw else "",
                "failure": failure}

    async def generate(self, job):
        payload = job["payload"]
        kind = job["kind"]
        engine = self.engines.adapters[payload["engine"]]
        if kind == "probe":
            request = EngineRequest("chat", "只回复：连接成功", payload["persona"])
            await engine.execute(request, payload["config"], job["id"])
            with self.store.db(True) as c:
                current = c.execute(
                    "SELECT config FROM engines WHERE id=?", (payload["engine"],)
                ).fetchone()[0]
                if json.loads(current) == payload["config"]:
                    c.execute(
                        "UPDATE engines SET status='ready',checked=? WHERE id=?",
                        (int(time.time()), payload["engine"]),
                    )
            return {"verified": True, "engine": payload["engine"]}
        context = {}
        post = None
        if kind == "reply":
            if not payload.get('draft_id'):
                blocked = reply_block(self.store, str(payload.get('post_id') or ''))
                if blocked: raise EngineError(blocked)
            rows = self.store.rows(
                "SELECT * FROM posts WHERE id=?", (payload["post_id"],)
            )
            if not rows:
                raise EngineError("post_missing")
            post = rows[0]
            if post["status"] in ["skipped", "ignored"]:
                raise EngineError("risk_skipped")
            context = {
                "post": post["text"],
                "author": post["author"],
                "details": json.loads(post["context"]),
            }
            parent = context["details"].get("parent_id")
            if parent:
                parents = self.store.rows(
                    "SELECT text,author FROM posts WHERE id=?", (parent,)
                )
                context["parent"] = parents[0] if parents else {"unavailable": True}
        feedback = [
            r["text"]
            for r in self.store.rows(
                "SELECT text FROM feedback ORDER BY id DESC LIMIT 6"
            )
        ]
        query = (payload.get("text") or (post["text"] if post else ""))[:200]
        terms = query.split()[:8]
        search = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        relevant = (
            self.store.rows(
                "SELECT memories.text FROM memory_fts JOIN memories ON memories.id=memory_fts.rowid WHERE memory_fts MATCH ? ORDER BY rank LIMIT 5",
                (search,),
            )
            if search
            else []
        )
        memory = list(
            dict.fromkeys(
                [r["text"] for r in relevant]
                + [
                    r["text"]
                    for r in self.store.rows(
                        "SELECT text FROM memories ORDER BY id DESC LIMIT 8"
                    )
                ]
            )
        )[:8]
        channel = payload.get("channel", "web")
        history = (
            list(
                reversed(
                    self.store.rows(
                        "SELECT role,text FROM messages WHERE channel=? ORDER BY id DESC LIMIT 12",
                        (channel,),
                    )
                )
            )
            if kind == "chat"
            else []
        )
        recent = [
            r["text"]
            for r in self.store.rows(
                "SELECT text FROM drafts ORDER BY updated DESC LIMIT 12"
            )
        ]
        context["recent_output"] = recent
        if kind == "post":
            work = await asyncio.to_thread(collect_work, self.store.root, payload["engine"])
            if work:
                context["work_inspiration"] = work
        if kind == "chat":
            summaries = self.store.rows(
                "SELECT text FROM conversation_summaries WHERE channel=?", (channel,)
            )
            if summaries:
                context["earlier_conversation_excerpt"] = summaries[0]["text"]
        request_text = payload.get('text', '')
        if payload.get('creation_id'):
            context['writing_workspace'] = {'original_idea': payload['idea'], 'current_candidate': payload['candidate']}
            instruction = ('你是推文创作搭档。根据原始灵感、当前候选稿和此前对话回应本轮要求。不要虚构经历或数据。'
                '脑暴时提出有区别的角度、指出缺失证据，并用简短追问帮助用户选择，不要擅自定稿。')
            if payload['mode'] == 'draft':
                instruction = ('根据原始灵感、当前候选稿和此前对话的选择及修改意见，生成一版推文候选稿。'
                    '只输出推文正文，不要解释、标题标签或多份备选，不虚构经历和数据。由用户最终确认定稿。')
            request_text = instruction + '\n\n本轮要求：' + request_text
        request = EngineRequest(
            kind,
            request_text,
            payload["persona"],
            context,
            history,
            memory,
            feedback,
        )
        if payload.get("judge"):
            judge = EngineRequest(
                "judge",
                "判断是否值得回复或互动",
                payload["persona"],
                context,
                [],
                memory,
                feedback,
            )
            judged = await engine.execute(judge, payload["config"], job["id"])
            try:
                raw = (
                    judged.text.strip()
                    .removeprefix("```json")
                    .removeprefix("```")
                    .removesuffix("```")
                    .strip()
                )
                decision = json.loads(raw)
                if decision["action"] not in [
                    "reply",
                    "like",
                    "repost",
                    "quote",
                    "follow",
                    "skip",
                ]:
                    raise ValueError()
                reason = str(decision["reason"])[:500]
            except (ValueError, KeyError, TypeError):
                raise EngineError("judgment_invalid")
            if not self.alive(job):
                raise EngineError("cancelled")
            with self.store.db(True) as c:
                context["details"]["suggestion"] = decision["action"]
                c.execute(
                    "UPDATE posts SET reason=?,context=? WHERE id=?",
                    (
                        reason,
                        json.dumps(context["details"], ensure_ascii=False),
                        post["id"],
                    ),
                )
            if decision["action"] != "reply":
                return {"suggestion": decision["action"], "reason": reason}
        result = await engine.execute(request, payload["config"], job["id"])
        if not self.alive(job):
            raise EngineError("cancelled")
        text = sanitize(result.text, self.secrets.all().values()).strip()
        if not text or len(text) > 12000:
            raise EngineError("invalid_output")
        from .safety import is_high_risk

        if is_high_risk(text):
            raise EngineError("risk_skipped")
        if kind != "chat" and (text == "[SKIP]" or text in recent):
            return {"skipped": "no_natural_or_duplicate_reply"}
        with self.store.db(True) as c:
            task = c.execute(
                "SELECT cancel FROM tasks WHERE id=?", (job["id"],)
            ).fetchone()
            paused = json.loads(
                c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]
            )
            if task["cancel"] or (paused and kind != "chat"):
                raise EngineError("cancelled")
            if kind == "chat":
                if payload.get('creation_id'):
                    creation = c.execute('SELECT * FROM creations WHERE id=?', (payload['creation_id'],)).fetchone()
                    if not creation or creation['version'] != payload['expected_version'] or creation['draft_id']:
                        raise EngineError('creation_conflict')
                    c.execute('UPDATE creations SET candidate=?,version=version+1,updated=? WHERE id=?',
                        (text if payload['mode'] == 'draft' else creation['candidate'], int(time.time()), payload['creation_id']))
                for role, value in [
                    ("user", sanitize(payload["text"], self.secrets.all().values())),
                    ("assistant", text),
                ]:
                    c.execute(
                        "INSERT INTO messages(channel,role,text,created) VALUES(?,?,?,?)",
                        (channel, role, value, int(time.time())),
                    )
                older = c.execute(
                    "SELECT id,role,text FROM messages WHERE channel=? ORDER BY id DESC LIMIT 24 OFFSET 12",
                    (channel,),
                ).fetchall()
                if older:
                    excerpt = "\n".join(
                        r["role"] + ": " + r["text"][:160] for r in reversed(older)
                    )
                    c.execute(
                        "INSERT INTO conversation_summaries VALUES(?,?,?,?) ON CONFLICT(channel) DO UPDATE SET through_id=excluded.through_id,text=excluded.text,updated=excluded.updated",
                        (channel, older[0]["id"], excerpt, int(time.time())),
                    )
                return {"text": text, "usage": result.usage}
            target = payload.get("draft_id")
            if target:
                row = c.execute("SELECT * FROM drafts WHERE id=?", (target,)).fetchone()
                if (
                    not row
                    or row["version"] != payload["expected_version"]
                    or row["status"] != "draft"
                ):
                    # Keep result in task history, never overwrite a newer human edit.
                    return {
                        "conflict": True,
                        "text": text,
                        "reason": "草稿已被修改，新生成的版本未覆盖原文",
                    }
                version = row["version"] + 1
                c.execute(
                    "UPDATE drafts SET text=?,version=?,engine=?,persona_version=?,updated=? WHERE id=?",
                    (
                        text,
                        version,
                        result.engine,
                        payload["persona"]["version"],
                        int(time.time()),
                        target,
                    ),
                )
                c.execute(
                    "INSERT INTO draft_versions(draft_id,version,text,created) VALUES(?,?,?,?)",
                    (target, version, text, int(time.time())),
                )
            else:
                if post:
                    blocked = reply_block(self.store, post["id"], c)
                    if blocked:
                        return {"skipped": blocked}
                target = self.store.draft(
                    text, kind, post["id"] if post else None, result.engine, c
                )
                c.execute(
                    "UPDATE drafts SET persona_version=? WHERE id=?",
                    (payload["persona"]["version"], target),
                )
            self.store.event(
                "draft",
                "新草稿已准备好",
                {"draft_id": target, "engine": result.engine},
                c,
            )
            return {"draft_id": target, "text": text, "usage": result.usage}

    async def run(self, job):
        try:
            result = await (
                self.sync(job) if job["kind"] == "sync" else
                BrowserReads(self.store).enrich(job, self.alive) if job["kind"] == "context" else
                BrowserReads(self.store).metric(job, self.alive) if job["kind"] == "metric" else
                BrowserReads(self.store).profile(job, self.alive) if job["kind"] == "profile_metric" else
                BrowserReads(self.store).benchmark(job, self.alive) if job["kind"] == "benchmark" else
                BrowserReads(self.store).quality(job, self.alive) if job["kind"] == "account_quality" else
                AnalyticsBrowserReads(self.store).read(job, self.alive) if job["kind"] in ("analytics_content", "analytics_overview") else
                GrowthBrowserReads(self.store).read(job, self.alive) if job["kind"] in ("growth_rewards", "growth_followers") else
                self.weekly(job) if job["kind"] == "weekly" else
                self.deconstruct(job) if job["kind"] == "deconstruct" else
                self.algorithm_check(job) if job["kind"] == "algorithm_check" else
                self.generate(job)
            )
            self.finish(job, "succeeded", result)
        except (ReadError, EngineError, JobError) as exc:
            code = exc.code
            if isinstance(exc, ReadError) and code != 'cancelled' and job['kind'] == 'sync':
                sync_failed(self.store, code)
                self.store.set(
                    "x_status",
                    code if code in ["needs_login", "rate_limited"] else "partial",
                )
                if code == "needs_login":
                    self.store.set("paused", True)
                if (
                    code in ["timeout", "source_error", "network_error", "rate_limited"]
                    and job["attempts"] < 2
                    and self.alive(job)
                ):
                    with self.store.db(True) as c:
                        c.execute(
                            "UPDATE tasks SET status='queued',available=?,error=? WHERE id=?",
                            (int(time.time()) + (300 if code == "rate_limited" else 30) * 2 ** job["attempts"], code, job["id"]),
                        )
                    return
            if job["kind"] == "probe":
                with self.store.db() as c:
                    c.execute(
                        "UPDATE engines SET status=?,checked=? WHERE id=?",
                        (code, int(time.time()), job["payload"]["engine"]),
                    )
            self.finish(
                job, "cancelled" if code == "cancelled" else "failed", error=code
            )
        except Exception:
            if job["kind"] == "sync":
                sync_failed(self.store, "source_error")
                self.store.set("x_status", "partial")
            self.finish(job, "failed", error="internal_error")
