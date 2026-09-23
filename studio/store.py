from __future__ import annotations
import json
import os
import sqlite3
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DEFAULTS = dict(
    brand="人格工作室",
    paused=True,
    engine="hermes",
    sync_interval=1800,
    timezone="Asia/Shanghai",
    owner_id="",
    telegram_enabled=False,
    x_username="",
    x_status="not_connected",
    x_verified_at=0,
    last_sync=0,
    last_sync_requested=0,
    auto_replies=12,
    auto_posts=2,
    per_round=3,
    worker_heartbeat=0,
    telegram_status="not_connected",
)
PERSONA = dict(
    name="未命名的角色",
    identity="我是一个明确标注的虚构 AI 人格。",
    voice="自然、简洁、有自己的观点，不模仿真人身份。",
    interests="",
    boundaries="",
    version=1,
)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / "studio.sqlite3"
        self.migrate()

    @contextmanager
    def db(self, immediate=False):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=15000")
        try:
            if immediate:
                c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def migrate(self):
        with self.db() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS persona_versions(version INTEGER PRIMARY KEY,body TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS engines(id TEXT PRIMARY KEY,config TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'not_configured',checked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(name TEXT PRIMARY KEY,status TEXT NOT NULL,last_success INTEGER NOT NULL DEFAULT 0,last_attempt INTEGER NOT NULL DEFAULT 0,count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,author TEXT NOT NULL,text TEXT NOT NULL,url TEXT NOT NULL,kind TEXT NOT NULL,context TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'new',reason TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL,seen INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,kind TEXT NOT NULL,text TEXT NOT NULL,post_id TEXT REFERENCES posts(id),status TEXT NOT NULL DEFAULT 'draft',version INTEGER NOT NULL DEFAULT 1,edited INTEGER NOT NULL DEFAULT 0,persona_version INTEGER NOT NULL,engine TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL,updated INTEGER NOT NULL,result_url TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS draft_versions(draft_id TEXT NOT NULL,version INTEGER NOT NULL,text TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(draft_id,version));
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,automatic INTEGER NOT NULL DEFAULT 0,dedupe TEXT,attempts INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL,updated INTEGER NOT NULL,available INTEGER NOT NULL,result TEXT NOT NULL DEFAULT '{}',error TEXT NOT NULL DEFAULT '',cancel INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS active_dedupe ON tasks(dedupe) WHERE status IN ('queued','running') AND dedupe IS NOT NULL;
            CREATE TABLE IF NOT EXISTS generation_ledger(task_id TEXT PRIMARY KEY,day TEXT NOT NULL,kind TEXT NOT NULL,automatic INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,label TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '{}',created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,channel TEXT NOT NULL,role TEXT NOT NULL,text TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS conversation_summaries(channel TEXT PRIMARY KEY,through_id INTEGER NOT NULL,text TEXT NOT NULL,updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT,category TEXT NOT NULL,text TEXT NOT NULL,source TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(text,content='memories',content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS memory_insert AFTER INSERT ON memories BEGIN INSERT INTO memory_fts(rowid,text) VALUES(new.id,new.text); END;
            CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS telegram_updates(id INTEGER PRIMARY KEY,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS notices(draft_id TEXT PRIMARY KEY,status TEXT NOT NULL,created INTEGER NOT NULL);
            """)
            for k, v in DEFAULTS.items():
                c.execute(
                    "INSERT OR IGNORE INTO settings VALUES(?,?)", (k, json.dumps(v))
                )
            c.execute(
                "INSERT OR IGNORE INTO settings VALUES(?,?)",
                ("persona", json.dumps(PERSONA, ensure_ascii=False)),
            )
            c.execute(
                "INSERT OR IGNORE INTO persona_versions VALUES(1,?,?)",
                (json.dumps(PERSONA, ensure_ascii=False), int(time.time())),
            )
            for engine in ["hermes", "openclaw"]:
                c.execute(
                    "INSERT OR IGNORE INTO engines(id,config) VALUES(?,?)",
                    (engine, json.dumps({"model": "", "base_url": ""})),
                )
            c.execute(
                "INSERT OR IGNORE INTO migrations VALUES(1,?)", (int(time.time()),)
            )
        self.path.chmod(0o600)

    def get(self, key, default=None):
        with self.db() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value, c=None):
        if c is None:
            with self.db() as conn:
                self.set(key, value, conn)
            return
        c.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def settings(self):
        return {k: self.get(k, v) for k, v in DEFAULTS.items()}

    def rows(self, sql, args=()):
        with self.db() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def event(self, kind, label, detail=None, c=None):
        if c is None:
            with self.db() as conn:
                self.event(kind, label, detail, conn)
            return
        c.execute(
            "INSERT INTO events(kind,label,detail,created) VALUES(?,?,?,?)",
            (
                kind,
                label,
                json.dumps(detail or {}, ensure_ascii=False),
                int(time.time()),
            ),
        )

    def backup(self):
        folder = self.root / "backups"
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / f"studio-{time.time_ns()}.sqlite3"
        with sqlite3.connect(self.path) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            # Transient browser material must not outlive its seven-day policy
            # through routine studio backups. Applied memories/versions remain.
            if dst.execute(
                "SELECT 1 FROM sqlite_master WHERE name='persona_imports'"
            ).fetchone():
                dst.execute("PRAGMA secure_delete=ON")
                dst.execute("DELETE FROM persona_sources")
                dst.execute("DELETE FROM persona_browser_actions")
                dst.execute(
                    "UPDATE persona_imports SET profile='{}', status=CASE WHEN status='applied' THEN status ELSE 'cancelled' END,message='备份不包含临时采集材料。'"
                )
                dst.commit()
                dst.execute("VACUUM")
        target.chmod(0o600)
        return target

    def import_legacy(self):
        old = self.root / "factory.sqlite3"
        if not old.exists() or self.get("legacy_imported", False):
            return
        folder = self.root / "backups"
        folder.mkdir(mode=0o700, exist_ok=True)
        snapshot = folder / f"factory-before-studio-{time.time_ns()}.sqlite3"
        with sqlite3.connect(old) as src, sqlite3.connect(snapshot) as dst:
            src.backup(dst)
        snapshot.chmod(0o600)
        legacy = sqlite3.connect(snapshot)
        legacy.row_factory = sqlite3.Row
        now = int(time.time())
        from .security import Secrets, sanitize

        vault = Secrets(self.root)
        secret_values = vault.all().values()
        for name in [".env", "factory.json", "openclaw.json"]:
            source = self.root / name
            if source.is_file():
                dest = folder / (snapshot.stem + "-" + name.lstrip("."))
                shutil.copyfile(source, dest)
                dest.chmod(0o600)
        with self.db(True) as c:
            if c.execute(
                "SELECT 1 FROM settings WHERE key='legacy_imported'"
            ).fetchone():
                legacy.close()
                return
            tables = {
                r[0]
                for r in legacy.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "personas" in tables:
                selected = legacy.execute(
                    "SELECT * FROM personas WHERE enabled=1 ORDER BY created_at LIMIT 1"
                ).fetchone()
                if selected:
                    persona = dict(PERSONA)
                    persona["name"] = selected["name"]
                    persona["version"] = 2
                    location = Path(selected["path"]).expanduser()
                    voice = location / "voice.md"
                    if voice.is_file():
                        persona["voice"] = sanitize(
                            voice.read_text()[:6000], secret_values
                        )
                    self.set("persona", persona, c)
                    c.execute(
                        "INSERT OR IGNORE INTO persona_versions VALUES(?,?,?)",
                        (2, json.dumps(persona, ensure_ascii=False), now),
                    )
                    archive = folder / (snapshot.stem + "-persona")
                    if location.is_dir():
                        shutil.copytree(location, archive, symlinks=True)
                        for item in archive.rglob("*"):
                            if not item.is_symlink():
                                item.chmod(0o700 if item.is_dir() else 0o600)
                    for corpus in [
                        location / "data/sanitized_corpus.jsonl",
                        location / "data/synthetic.json",
                    ]:
                        if not corpus.is_file():
                            continue
                        try:
                            content = corpus.read_text()
                            records = (
                                json.loads(content)
                                if corpus.suffix == ".json"
                                else [json.loads(line) for line in content.splitlines()]
                            )
                            if isinstance(records, dict):
                                records = records.get("records", [])
                            for record in records[:500]:
                                text = (
                                    record.get("text", "")
                                    if isinstance(record, dict)
                                    else str(record)
                                )
                                if text:
                                    c.execute(
                                        "INSERT INTO memories(category,text,source,created) VALUES(?,?,?,?)",
                                        (
                                            "style",
                                            sanitize(text, secret_values),
                                            "legacy_corpus",
                                            now,
                                        ),
                                    )
                        except (ValueError, TypeError):
                            self.event(
                                "migration", "旧版部分语料格式无法导入，已保留备份", c=c
                            )
            if "memory" in tables:
                for r in legacy.execute("SELECT * FROM memory"):
                    c.execute(
                        "INSERT INTO memories(category,text,source,created) VALUES(?,?,?,?)",
                        (
                            r["category"],
                            sanitize(r["content"], secret_values),
                            "legacy",
                            r["ts"],
                        ),
                    )
            if "pending_actions" in tables:
                for r in legacy.execute(
                    "SELECT * FROM pending_actions WHERE text != ''"
                ):
                    # No reliable original context: import as an archived draft, never claim posted.
                    c.execute(
                        "INSERT INTO drafts(id,kind,text,status,persona_version,created,updated) VALUES(?,?,?,?,?,?,?)",
                        (
                            "legacy-" + str(r["id"]),
                            "reply" if r["action"] == "reply" else "post",
                            sanitize(r["text"], secret_values),
                            "archived" if r["status"] != "pending" else "draft",
                            1,
                            r["ts"],
                            now,
                        ),
                    )
                    c.execute(
                        "INSERT INTO draft_versions VALUES(?,?,?,?)",
                        (
                            "legacy-" + str(r["id"]),
                            1,
                            sanitize(r["text"], secret_values),
                            now,
                        ),
                    )
            if "audit" in tables:
                for r in legacy.execute("SELECT id,action,ts,sent,shadow FROM audit"):
                    self.event(
                        "legacy",
                        "已保留旧版活动记录",
                        {
                            "legacy_id": r["id"],
                            "action": r["action"],
                            "legacy_sent": bool(r["sent"]),
                            "legacy_shadow": bool(r["shadow"]),
                        },
                        c,
                    )
            self.set("legacy_imported", True, c)
            self.set("paused", True, c)
            self.event("migration", "已迁移旧版数据；同步与生成保持暂停", c=c)
        legacy.close()

    def draft(self, text="", kind="post", post_id=None, engine="", c=None):
        if c is None:
            with self.db(True) as conn:
                return self.draft(text, kind, post_id, engine, conn)
        ident = uuid.uuid4().hex
        now = int(time.time())
        persona = json.loads(
            c.execute("SELECT value FROM settings WHERE key='persona'").fetchone()[0]
        )
        c.execute(
            "INSERT INTO drafts(id,kind,text,post_id,persona_version,engine,created,updated) VALUES(?,?,?,?,?,?,?,?)",
            (ident, kind, text, post_id, persona["version"], engine, now, now),
        )
        c.execute("INSERT INTO draft_versions VALUES(?,?,?,?)", (ident, 1, text, now))
        return ident
