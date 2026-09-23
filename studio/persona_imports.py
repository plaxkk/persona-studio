"""Durable, owner-scoped browser evidence and candidate-persona lifecycle."""

from __future__ import annotations
import json
import re
import secrets
import time
from fastapi import HTTPException
from .security import sanitize, Secrets

ACTIVE = ("queued", "collecting", "distilling")
WAITING = (
    "paused",
    "waiting_browser",
    "waiting_login",
    "challenge",
    "account_mismatch",
    "stalled",
    "interrupted",
)
FIELDS = ("name", "identity", "voice", "interests", "boundaries")
ACTIONS = (
    "snapshot",
    "profile",
    "posts",
    "replies",
    "scroll",
    "expand",
    "original",
    "detail",
)


def error(message, code=409):
    raise HTTPException(code, detail={"message": message})


class PersonaImports:
    def __init__(self, store):
        self.store = store
        with store.db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS persona_imports(
              id TEXT PRIMARY KEY, account TEXT NOT NULL, target INTEGER NOT NULL,
              status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'collect',
              base_version INTEGER NOT NULL, profile TEXT NOT NULL DEFAULT '{}',
              candidate TEXT NOT NULL DEFAULT '{}', message TEXT NOT NULL DEFAULT '',
              actions INTEGER NOT NULL DEFAULT 0, duplicates INTEGER NOT NULL DEFAULT 0,
              stalls INTEGER NOT NULL DEFAULT 0, elapsed REAL NOT NULL DEFAULT 0,
              created INTEGER NOT NULL, updated INTEGER NOT NULL, applied_version INTEGER,
              generation INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL DEFAULT '',
              reasoning TEXT NOT NULL DEFAULT '', usage TEXT NOT NULL DEFAULT '{}');
            CREATE UNIQUE INDEX IF NOT EXISTS one_persona_import ON persona_imports(account)
              WHERE status IN ('queued','collecting','distilling','paused','waiting_browser','waiting_login','challenge','account_mismatch','stalled','interrupted');
            CREATE TABLE IF NOT EXISTS persona_sources(
              import_id TEXT NOT NULL REFERENCES persona_imports(id) ON DELETE CASCADE,
              post_id TEXT NOT NULL, body TEXT NOT NULL, usable INTEGER NOT NULL,
              reason TEXT NOT NULL, PRIMARY KEY(import_id,post_id));
            CREATE TABLE IF NOT EXISTS persona_browser_actions(
              id INTEGER PRIMARY KEY AUTOINCREMENT, import_id TEXT NOT NULL REFERENCES persona_imports(id) ON DELETE CASCADE,
              generation INTEGER NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'queued', result TEXT NOT NULL DEFAULT '{}',
              created INTEGER NOT NULL, updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS persona_browser_presence(
              id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL, seen INTEGER NOT NULL);
            """)

    def get(self, ident):
        rows = self.store.rows("SELECT * FROM persona_imports WHERE id=?", (ident,))
        if not rows:
            error("找不到此采集任务。", 404)
        item = rows[0]
        for k in ("profile", "candidate", "usage"):
            item[k] = json.loads(item[k])
        stats = self.store.rows(
            "SELECT usable,reason,body FROM persona_sources WHERE import_id=?", (ident,)
        )
        item["counts"] = {
            "read": len(stats),
            "usable": sum(r["usable"] for r in stats),
            "post": 0,
            "reply": 0,
        }
        item["excluded"] = {}
        for r in stats:
            if r["usable"]:
                kind = json.loads(r["body"])["kind"]
                item["counts"][kind] += 1
            else:
                item["excluded"][r["reason"]] = item["excluded"].get(r["reason"], 0) + 1
        retained = self.store.get("persona_import_stats:" + ident)
        if not stats and retained:
            item.update(retained)
        item["current_version"] = self.store.get("persona")["version"]
        item["current_persona"] = self.store.get("persona")
        item["browser_connected"] = self.connected()
        return item

    def connected(self):
        rows = self.store.rows("SELECT * FROM persona_browser_presence WHERE id=1")
        return bool(
            rows
            and rows[0]["seen"] > time.time() - 45
            and rows[0]["version"].startswith("1.3.")
        )

    def presence(self, version):
        if not re.fullmatch(r"1\.3\.\d+", version):
            error("请更新连接助手至 1.3。")
        with self.store.db() as c:
            c.execute(
                "INSERT OR REPLACE INTO persona_browser_presence VALUES(1,?,?)",
                (version, int(time.time())),
            )

    def create(self, account, target, model, reasoning):
        account = account.lstrip("@").lower()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", account):
            error("请先连接并核对你的 X 账号。", 422)
        if target not in (20, 100, 300):
            error("读取数量不支持。", 422)
        if not self.connected():
            error("请打开 Chrome，更新并授权连接助手 1.3。")
        with self.store.db(True) as c:
            old = c.execute(
                "SELECT id FROM persona_imports WHERE account=? AND status NOT IN ('preview','applied','cancelled','failed','insufficient')",
                (account,),
            ).fetchone()
            if old:
                return old[0]
            ident = secrets.token_hex(16)
            now = int(time.time())
            c.execute(
                "INSERT INTO persona_imports(id,account,target,status,base_version,created,updated,model,reasoning) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    account,
                    target,
                    "queued",
                    self.store.get("persona")["version"],
                    now,
                    now,
                    model,
                    reasoning,
                ),
            )
            self.store.event(
                "persona_import",
                "已开始 X 人设采集",
                {"id": ident, "target": target},
                c,
            )
        return ident

    def transition(self, ident, action):
        with self.store.db(True) as c:
            row = c.execute(
                "SELECT * FROM persona_imports WHERE id=?", (ident,)
            ).fetchone()
            if not row:
                error("任务不存在。", 404)
            status = row["status"]
            if action == "cancel":
                if status == "applied":
                    error("此人设已应用，可删除采集材料或回退版本。")
                new = "cancelled"
            elif action == "pause":
                if status not in ACTIVE + WAITING:
                    error("当前任务不能暂停。")
                new = "paused"
            elif action == "resume":
                if status not in WAITING + ("failed",):
                    error("当前任务不能继续。")
                if row["elapsed"] >= 1800 or row["actions"] >= 240:
                    error("已达到执行上限，请使用已有样本生成或重新创建任务。")
                if not self.connected():
                    error("浏览器未连接，请先打开并授权连接助手。")
                conflict = c.execute(
                    "SELECT id FROM persona_imports WHERE account=? AND id<>? AND status IN ('queued','collecting','distilling','paused','waiting_browser','waiting_login','challenge','account_mismatch','stalled','interrupted')",
                    (row["account"], ident),
                ).fetchone()
                if conflict:
                    error("此账号已有其他采集任务，请先结束它。")
                new = "queued"
            elif action == "analyze":
                if status not in WAITING + ("failed", "insufficient"):
                    error("请先暂停采集再使用现有样本。")
                if self.get(ident)["counts"]["usable"] < 20:
                    error("有效文字少于 20 条，暂不能生成完整人设。")
                conflict = c.execute(
                    "SELECT id FROM persona_imports WHERE account=? AND id<>? AND status IN ('queued','collecting','distilling','paused','waiting_browser','waiting_login','challenge','account_mismatch','stalled','interrupted')",
                    (row["account"], ident),
                ).fetchone()
                if conflict:
                    error("此账号已有其他任务。")
                c.execute(
                    "UPDATE persona_imports SET phase='analyze' WHERE id=?",
                    (ident,),
                )
                new = "queued"
            else:
                error("不支持的操作。", 422)
            c.execute(
                "UPDATE persona_imports SET status=?,message=?,generation=generation+1,updated=? WHERE id=?",
                (new, "", int(time.time()), ident),
            )
            c.execute(
                "UPDATE persona_browser_actions SET status='cancelled' WHERE import_id=? AND status IN ('queued','sent')",
                (ident,),
            )
            self.store.event(
                "persona_import",
                {
                    "pause": "已暂停人设采集",
                    "cancel": "已取消人设采集",
                    "resume": "已继续人设采集",
                    "analyze": "已请求使用现有样本生成",
                }[action],
                {"id": ident},
                c,
            )
        return self.get(ident)

    def pause_all(self):
        for r in self.store.rows(
            "SELECT id FROM persona_imports WHERE status IN ('queued','collecting','distilling')"
        ):
            self.transition(r["id"], "pause")

    def wait(self, ident, status, message):
        with self.store.db(True) as c:
            c.execute(
                "UPDATE persona_imports SET status=?,message=?,updated=? WHERE id=? AND status IN ('queued','collecting','distilling')",
                (status, message, int(time.time()), ident),
            )
            c.execute(
                "UPDATE persona_browser_actions SET status='cancelled' WHERE import_id=? AND status IN ('queued','sent')",
                (ident,),
            )

    def enqueue_action(self, ident, generation, kind, target=""):
        if kind not in ACTIONS or not isinstance(target, str) or len(target) > 200:
            error("浏览器动作不支持。", 422)
        with self.store.db(True) as c:
            row = c.execute(
                "SELECT * FROM persona_imports WHERE id=?", (ident,)
            ).fetchone()
            if (
                not row
                or row["generation"] != generation
                or row["status"] != "collecting"
            ):
                error("任务已停止。")
            if row["actions"] >= 240 or row["elapsed"] >= 1800:
                error("任务达到执行上限。")
            old = c.execute(
                "SELECT id FROM persona_browser_actions WHERE import_id=? AND status IN ('queued','sent')",
                (ident,),
            ).fetchone()
            if old:
                error("上一步尚未完成。")
            now = int(time.time())
            cursor = c.execute(
                "INSERT INTO persona_browser_actions(import_id,generation,kind,target,created,updated) VALUES(?,?,?,?,?,?)",
                (ident, generation, kind, target, now, now),
            )
            c.execute(
                "UPDATE persona_imports SET actions=actions+1 WHERE id=?", (ident,)
            )
            return cursor.lastrowid

    def next_action(self):
        with self.store.db(True) as c:
            row = c.execute(
                "SELECT a.*,p.account FROM persona_browser_actions a JOIN persona_imports p ON p.id=a.import_id WHERE a.status='queued' AND p.status='collecting' AND a.generation=p.generation ORDER BY a.id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            c.execute(
                "UPDATE persona_browser_actions SET status='sent',updated=? WHERE id=?",
                (int(time.time()), row["id"]),
            )
            return {
                k: row[k]
                for k in ("id", "import_id", "generation", "kind", "target", "account")
            }

    def receive(self, action_id, ident, generation, data):
        if not isinstance(data, dict) or len(json.dumps(data)) > 900000:
            error("页面数据无效。", 422)
        # Credentials never enter model context, records, or audit logs.
        data = json.loads(
            sanitize(
                json.dumps(data, ensure_ascii=False),
                Secrets(self.store.root).all().values(),
            )
        )
        with self.store.db(True) as c:
            a = c.execute(
                "SELECT * FROM persona_browser_actions WHERE id=? AND import_id=? AND generation=?",
                (action_id, ident, generation),
            ).fetchone()
            p = c.execute(
                "SELECT * FROM persona_imports WHERE id=?", (ident,)
            ).fetchone()
            if (
                not a
                or not p
                or a["status"] != "sent"
                or p["status"] != "collecting"
                or p["generation"] != generation
            ):
                return {"accepted": False}
            account = str(data.get("account", "")).lstrip("@").lower()
            state = data.get("state", "ready")
            if state == "ready" and account != p["account"].lower():
                state = "account_mismatch"
            if state != "ready":
                state = (
                    state
                    if state
                    in (
                        "waiting_login",
                        "challenge",
                        "account_mismatch",
                        "waiting_browser",
                    )
                    else "waiting_browser"
                )
                messages = {
                    "waiting_login": "请在任务标签页登录 X，登录后点击继续。",
                    "challenge": "请在 X 完成验证后点击继续。",
                    "account_mismatch": "浏览器登录账号与目标账号不一致，请切换后继续。",
                    "waiting_browser": "页面未准备好或结构发生变化，请检查任务标签页后继续。",
                }
                c.execute(
                    "UPDATE persona_imports SET status=?,message=? WHERE id=?",
                    (state, messages[state], ident),
                )
                result = {"state": state, "message": messages[state]}
            else:
                profile = data.get("profile")
                if isinstance(profile, dict) and profile:
                    safe = {
                        k: str(profile.get(k, ""))[:6000]
                        for k in ("name", "bio", "location", "joined", "url")
                    }
                    c.execute(
                        "UPDATE persona_imports SET profile=? WHERE id=?",
                        (json.dumps(safe, ensure_ascii=False), ident),
                    )
                added = duplicates = 0
                for item in data.get("records", [])[:60]:
                    if not isinstance(item, dict):
                        continue
                    post_id = str(item.get("id", ""))
                    author = str(item.get("author", "")).lstrip("@")
                    if (
                        not re.fullmatch(r"\d{10,25}", post_id)
                        or author.lower() != p["account"].lower()
                    ):
                        continue
                    text = str(item.get("text", ""))[:20000]
                    kind = item.get("kind")
                    if kind not in ("post", "reply"):
                        continue
                    reason = next(
                        (
                            flag
                            for flag in (
                                "advertisement",
                                "repost",
                                "translated",
                                "truncated",
                            )
                            if item.get(flag)
                        ),
                        "",
                    )
                    if len(str(item.get("text", ""))) > 20000:
                        reason = "truncated"
                    if not text.strip():
                        reason = "no_text"
                    safe = {
                        "id": post_id,
                        "author": author,
                        "text": text,
                        "kind": kind,
                        "created_at": str(item.get("created_at", ""))[:50],
                        "url": f"https://x.com/{author}/status/{post_id}",
                        "context": str(item.get("context", ""))[:4000],
                        "truncated": bool(item.get("truncated")),
                        "translated": bool(item.get("translated")),
                    }
                    old = c.execute(
                        "SELECT usable FROM persona_sources WHERE import_id=? AND post_id=?",
                        (ident, post_id),
                    ).fetchone()
                    if old:
                        duplicates += 1
                        if old["usable"] or reason:
                            continue
                    usable = int(not reason)
                    if usable:
                        total = c.execute(
                            "SELECT count(*) FROM persona_sources WHERE import_id=? AND usable=1",
                            (ident,),
                        ).fetchone()[0]
                        if total >= p["target"]:
                            own_count = c.execute(
                                "SELECT count(*) FROM persona_sources WHERE import_id=? AND usable=1 AND json_extract(body,'$.kind')=?",
                                (ident, kind),
                            ).fetchone()[0]
                            if own_count >= p["target"] // 2:
                                continue
                            other = c.execute(
                                "SELECT post_id FROM persona_sources WHERE import_id=? AND usable=1 AND json_extract(body,'$.kind')<>? ORDER BY length(post_id),post_id LIMIT 1",
                                (ident, kind),
                            ).fetchone()
                            if not other:
                                continue
                            c.execute(
                                "UPDATE persona_sources SET usable=0,reason='quota' WHERE import_id=? AND post_id=?",
                                (ident, other[0]),
                            )
                    c.execute(
                        "INSERT OR REPLACE INTO persona_sources VALUES(?,?,?,?,?)",
                        (
                            ident,
                            post_id,
                            json.dumps(safe, ensure_ascii=False),
                            usable,
                            reason,
                        ),
                    )
                    added += usable
                stalls = (
                    p["stalls"] + 1
                    if a["kind"] == "scroll" and not added
                    else (0 if added else p["stalls"])
                )
                c.execute(
                    "UPDATE persona_imports SET duplicates=duplicates+?,stalls=?,updated=? WHERE id=?",
                    (duplicates, stalls, int(time.time()), ident),
                )
                # Only compact, observed evidence is made available to the agent.
                result = {
                    "state": "ready",
                    "snapshot": str(data.get("snapshot", ""))[:20000],
                    "targets": data.get("targets", [])[:80],
                    "new_records": added,
                }
                if stalls >= 3:
                    c.execute(
                        "UPDATE persona_imports SET status='stalled',message='连续三次滚动没有新记录。可继续检查页面，或使用已有样本生成。' WHERE id=?",
                        (ident,),
                    )
            c.execute(
                "UPDATE persona_browser_actions SET status='done',result=?,updated=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), int(time.time()), action_id),
            )
        return {"accepted": True}

    def sources(self, ident):
        return [
            json.loads(r["body"])
            for r in self.store.rows(
                "SELECT body FROM persona_sources WHERE import_id=? AND usable=1 ORDER BY post_id DESC",
                (ident,),
            )
        ]

    def save_candidate(self, ident, generation, result):
        if not isinstance(result, dict) or set(result) != {
            "persona",
            "observations",
            "limitations",
            "examples",
        }:
            error("Codex 返回的人设格式不正确。", 422)
        persona = result["persona"]
        self.validate_persona(persona)
        source_ids = {r["id"] for r in self.sources(ident)}
        if not isinstance(result["observations"], list) or not result["observations"]:
            error("缺少原帖依据。", 422)
        for row in result["observations"]:
            if (
                not isinstance(row, dict)
                or row.get("type") not in ("profile", "observation", "inference")
                or not isinstance(row.get("statement"), str)
            ):
                error("依据格式无效。", 422)
            ids = row.get("source_ids")
            if not isinstance(ids, list) or any(x not in source_ids for x in ids):
                error("人设引用了未采集的帖子。", 422)
            if row["type"] != "profile" and not ids:
                error("此判断没有原帖依据。", 422)
        if (
            not isinstance(result["examples"], list)
            or len(result["examples"]) != 3
            or any(not isinstance(x, str) for x in result["examples"])
        ):
            error("需要三条仿写草稿。", 422)
        if (
            not isinstance(result["limitations"], str)
            or len(result["limitations"]) > 12000
            or len(result["observations"]) > 30
            or any(len(x) > 6000 for x in result["examples"])
        ):
            error("预览过长或格式无效。", 422)
        clean = json.loads(
            sanitize(
                json.dumps(result, ensure_ascii=False),
                Secrets(self.store.root).all().values(),
            )
        )
        with self.store.db(True) as c:
            cur = c.execute(
                "UPDATE persona_imports SET candidate=?,status='preview',phase='analyze',message='',updated=? WHERE id=? AND generation=? AND status='distilling'",
                (
                    json.dumps(clean, ensure_ascii=False),
                    int(time.time()),
                    ident,
                    generation,
                ),
            )
            if cur.rowcount:
                self.store.event(
                    "persona_import", "人设预览已生成，等待本人应用", {"id": ident}, c
                )

    @staticmethod
    def validate_persona(persona):
        if (
            not isinstance(persona, dict)
            or set(persona) != set(FIELDS)
            or any(
                not isinstance(persona[k], str)
                or not persona[k].strip()
                or len(persona[k]) > (60 if k == "name" else 6000)
                for k in FIELDS
            )
        ):
            error("请完整填写五项人设信息。", 422)

    def apply(self, ident, persona, expected_version):
        self.validate_persona(persona)
        self.store.backup()
        with self.store.db(True) as c:
            row = c.execute(
                "SELECT * FROM persona_imports WHERE id=?", (ident,)
            ).fetchone()
            if not row:
                error("任务不存在。", 404)
            if row["status"] == "applied":
                return json.loads(
                    c.execute(
                        "SELECT body FROM persona_versions WHERE version=?",
                        (row["applied_version"],),
                    ).fetchone()[0]
                )
            if row["status"] != "preview":
                error("尚未生成可应用的预览。")
            current = json.loads(
                c.execute("SELECT value FROM settings WHERE key='persona'").fetchone()[
                    0
                ]
            )
            if current["version"] != expected_version:
                error("人设已被修改，请刷新比较后再应用。")
            clean = {
                k: sanitize(v, Secrets(self.store.root).all().values())
                for k, v in persona.items()
            }
            clean["version"] = current["version"] + 1
            self.store.set("persona", clean, c)
            c.execute(
                "INSERT INTO persona_versions VALUES(?,?,?)",
                (
                    clean["version"],
                    json.dumps(clean, ensure_ascii=False),
                    int(time.time()),
                ),
            )
            for source in self.sources(ident):
                label = "persona-import:" + ident + ":" + source["url"]
                if not c.execute(
                    "SELECT 1 FROM memories WHERE source=?", (label,)
                ).fetchone():
                    c.execute(
                        "INSERT INTO memories(category,text,source,created) VALUES(?,?,?,?)",
                        (
                            "style",
                            __import__(
                                "scripts.persona_distill", fromlist=["normalize_text"]
                            ).normalize_text(source["text"]),
                            label,
                            int(time.time()),
                        ),
                    )
            c.execute(
                "UPDATE persona_imports SET status='applied',applied_version=?,updated=? WHERE id=?",
                (clean["version"], int(time.time()), ident),
            )
            self.store.event(
                "persona_import",
                "已应用从 X 生成的人设",
                {"id": ident, "version": clean["version"]},
                c,
            )
        return clean

    def delete(self, ident):
        row = self.get(ident)
        if row["status"] in ACTIVE:
            error("请先取消正在执行的任务。")
        with self.store.db(True) as c:
            c.execute("DELETE FROM persona_imports WHERE id=?", (ident,))
            self.store.event(
                "persona_import",
                "已删除采集材料；已应用的人设与风格记忆保留",
                {"id": ident},
                c,
            )
        with self.store.db() as c:
            c.execute(
                "DELETE FROM settings WHERE key=?", ("persona_import_stats:" + ident,)
            )
        folder = self.store.root / "persona-imports" / ident
        if folder.is_dir():
            import shutil

            shutil.rmtree(folder)

    def cleanup(self):
        cutoff = int(time.time()) - 7 * 86400
        with self.store.db(True) as c:
            ids = [
                r[0]
                for r in c.execute(
                    "SELECT id FROM persona_imports WHERE created<? AND status NOT IN ('queued','collecting','distilling')",
                    (cutoff,),
                )
            ]
            for ident in ids:
                current = self.get(ident)
                self.store.set(
                    "persona_import_stats:" + ident,
                    {"counts": current["counts"], "excluded": current["excluded"]},
                    c,
                )
                c.execute("DELETE FROM persona_sources WHERE import_id=?", (ident,))
                c.execute(
                    "DELETE FROM persona_browser_actions WHERE import_id=?", (ident,)
                )
                c.execute(
                    "UPDATE persona_imports SET profile='{}',message='原始采集材料已按 7 天保留期限清理。',status=CASE WHEN status='applied' THEN status ELSE 'cancelled' END WHERE id=?",
                    (ident,),
                )
        for ident in ids:
            folder = self.store.root / "persona-imports" / ident
            if folder.is_dir():
                import shutil

                shutil.rmtree(folder)

    def markdown(self, ident):
        p = self.get(ident)
        cand = p["candidate"]
        if not cand:
            error("暂无预览。")
        lines = [
            "# " + cand["persona"]["name"],
            "",
            "来源：https://x.com/" + p["account"],
            f"采样：{p['counts']['usable']} 条有效文字；并非完整历史。",
            "",
        ]
        for k in FIELDS[1:]:
            lines.extend(
                [
                    "## "
                    + {
                        "identity": "身份",
                        "voice": "表达",
                        "interests": "话题",
                        "boundaries": "边界",
                    }[k],
                    cand["persona"][k],
                    "",
                ]
            )
        lines += ["## 依据"]
        for obs in cand["observations"]:
            lines.append("- " + obs["statement"])
            lines.extend(
                "  https://x.com/" + p["account"] + "/status/" + i
                for i in obs["source_ids"]
            )
        lines += [
            "",
            "## 采样限制",
            str(cand["limitations"]),
            "",
            "## 仿写草稿（非原帖）",
            *cand["examples"],
        ]
        return "\n".join(lines)
