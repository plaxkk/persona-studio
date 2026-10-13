from __future__ import annotations
import asyncio
import json
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from .engines import Engines, EngineRequest, EngineError
from .security import Secrets, sanitize
from .xreader import XReader, ReadError
from .inspiration import collect_work
from .sync_status import failed as sync_failed
from .browser_reads import BrowserReads
from .analytics_browser_reads import AnalyticsBrowserReads
from .interactions import candidate_ids, reply_block


class JobError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Jobs:
    def __init__(self, store):
        self.store = store
        self.engines = Engines(store)
        self.secrets = Secrets(store.root)
        self.last_measurement_schedule = 0

    def enqueue(self, kind, payload=None, automatic=False, dedupe=None):
        payload = dict(payload or {})
        now = int(time.time())
        ident = uuid.uuid4().hex
        with self.store.db(True) as c:
            cfg = {
                r["key"]: json.loads(r["value"])
                for r in c.execute("SELECT * FROM settings")
            }
            if cfg["paused"] and kind not in ["chat", "probe"]:
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
            if kind in ["post", "reply", "chat", "probe"]:
                engine = payload.get("engine") or cfg["engine"]
                if engine not in self.engines.adapters:
                    raise JobError("unsupported_engine")
                row = c.execute(
                    "SELECT * FROM engines WHERE id=?", (engine,)
                ).fetchone()
                if kind != "probe" and row["status"] != "ready":
                    raise JobError("engine_not_ready")
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
                    now,
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
                    else ("queued" if row["kind"] in ("sync", "context", "metric", "profile_metric", "analytics_content") else "failed")
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
                "SELECT * FROM tasks WHERE status='queued' AND cancel=0 AND available<=? AND (?=0 OR kind IN ('chat','probe')) ORDER BY created,id LIMIT 1",
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
            and (job["kind"] in ["chat", "probe"] or not self.store.get("paused"))
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

    def schedule_measurements(self, now):
        browser = BrowserReads(self.store)
        if not browser.connected(8): return
        account = self.store.get('x_username','')
        if not account: return
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
                AnalyticsBrowserReads(self.store).read(job, self.alive) if job["kind"] == "analytics_content" else
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
