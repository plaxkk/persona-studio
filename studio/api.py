from __future__ import annotations
import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from .browser_connection import BrowserConnection, PATH as BROWSER_COMPLETE
from .browser_identity import EXTENSION_ORIGIN
from .desktop import Desktop, desktop_route
from .store import Store
from .security import (
    Secrets,
    password_hash,
    password_check,
    digest,
    canonical_x_url,
    sanitize,
)
from .engines import Engines, EngineError
from .jobs import Jobs, JobError
from .xreader import XReader, ReadError

ROOT = Path(__file__).resolve().parents[1]
ERRORS = {
    "paused": "已暂停。请先在首页恢复同步与生成。",
    "engine_not_ready": "请先在设置中完成引擎连接测试。",
    "unsupported_engine": "此引擎尚未接入。",
    "not_installed": "尚未安装该引擎，请查看部署指引。",
    "not_configured": "请填写模型名称、接口地址与 API Key。",
    "x_not_ready": "请先连接并验证 X 账号。",
    "refresh_cooldown": "刷新较频繁，请至少间隔 60 秒。",
    "daily_budget": "已达到今天的自动生成预算。",
    "needs_login": "X 登录已失效或需要验证，请重新提供凭据。",
    "identity_mismatch": "实际登录的 X 账号与填写的用户名不一致。",
    "rate_limited": "X 暂时限制了读取频率，稍后会再试。",
    "timeout": "连接超时，请稍后重试。",
    "engine_failed": "引擎调用失败，请检查模型和连接配置。",
    "invalid_output": "引擎没有返回有效结果。",
    "internal_error": "任务未能完成，请查看诊断或重试。",
    "cancelled": "任务已取消。",
    "client_incompatible": "X 网页或读取接口已变化，当前客户端解析失败；这不代表 cookie 无效。",
    "network_error": "服务器无法连接 X，请检查服务器网络或代理。",
    "source_error": "部分读取来源暂时不可用。",
    "judgment_invalid": "引擎未给出可用判断，已停止本次建议。",
    "worker_restarted": "服务重启中断了任务，请手动重试。",
    "not_connected": "请填写连接凭据。",
    "post_missing": "原帖不存在。",
    "risk_skipped": "内容不适合自动生成，已跳过。",
}


def fail(code, status=400):
    raise HTTPException(
        status, detail={"code": code, "message": ERRORS.get(code, code)}
    )


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    password: str = Field(min_length=10, max_length=256)
    setup_token: str = ""


class Persona(Input):
    name: str = Field(min_length=1, max_length=60)
    identity: str = Field(max_length=3000)
    voice: str = Field(max_length=6000)
    interests: str = Field(default="", max_length=3000)
    boundaries: str = Field(default="", max_length=3000)
    version: int = Field(ge=1)


class Settings(Input):
    brand: str = Field(default="人格工作室", min_length=1, max_length=40)
    sync_interval: int = Field(default=1800, ge=300, le=86400)
    timezone: str = "Asia/Shanghai"
    auto_replies: int = Field(default=12, ge=0, le=100)
    auto_posts: int = Field(default=2, ge=0, le=20)
    per_round: int = Field(default=3, ge=0, le=10)


class EngineConfig(Input):
    model: str = Field(max_length=200)
    base_url: str = Field(max_length=1000)
    api_key: str = Field(default="", max_length=4096)


class XConfig(Input):
    username: str = Field(max_length=15)
    auth_token: str = Field(default="", max_length=4096)
    ct0: str = Field(default="", max_length=4096)
    cookies: list | None = None


class TelegramConfig(Input):
    owner_id: str
    token: str = Field(default="", max_length=4096)
    enabled: bool = False


class Generate(Input):
    kind: str
    text: str = Field(default="", max_length=12000)
    post_id: str | None = None
    draft_id: str | None = None
    expected_version: int | None = None


class DraftIn(Input):
    text: str = Field(default="", max_length=12000)
    kind: str = "post"
    post_id: str | None = None


class DraftEdit(Input):
    text: str = Field(max_length=12000)
    version: int = Field(ge=1)


class Completion(Input):
    result_url: str = Field(default="", max_length=1000)


class Text(Input):
    text: str = Field(min_length=1, max_length=12000)


class Toggle(Input):
    paused: bool


class Corpus(Input):
    filename: str = Field(max_length=100)
    content: str = Field(max_length=1_000_000)


def create_app(state_dir=None, public_url=None):
    root = Path(
        state_dir or os.environ.get("STUDIO_STATE_DIR", ROOT / ".local/state")
    ).resolve()
    store = Store(root)
    store.import_legacy()
    vault = Secrets(root)
    desktop = Desktop(store)
    engines = Engines(store)
    jobs = Jobs(store)
    origin = (
        public_url
        or os.environ.get(
            "STUDIO_PUBLIC_URL",
            "http://127.0.0.1:" + os.environ.get("STUDIO_PORT", "18880"),
        )
    ).rstrip("/")
    parsed = urlparse(origin)
    secure = parsed.scheme == "https"
    local = parsed.hostname in ["127.0.0.1", "localhost", "::1", "testserver"]
    if not local and not secure:
        raise ValueError("Remote deployment requires STUDIO_PUBLIC_URL=https://...")
    allowed_hosts = {parsed.netloc}
    if local:
        allowed_hosts |= {
            "localhost:" + str(parsed.port or 18880),
            "127.0.0.1:" + str(parsed.port or 18880),
        }
    app = FastAPI(
        title="人格工作室",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.state.jobs = jobs
    app.state.engines = engines
    with store.db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS login_attempts(ip TEXT,ts INTEGER)")
    # Existing manual model key may be imported into Hermes only, never exposed to the client.
    if vault.get("MODEL_API_KEY") and not vault.get("ENGINE_HERMES_API_KEY"):
        vault.update({"ENGINE_HERMES_API_KEY": vault.get("MODEL_API_KEY")})
    if not local and not store.get("password_hash"):
        setup = root / "setup-token"
        if not setup.exists():
            setup.write_text(secrets.token_urlsafe(24))
            setup.chmod(0o600)

    @app.middleware("http")
    async def boundaries(request, call_next):
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse(
                {"detail": {"message": "Host 不被允许"}}, status_code=400
            )
        if not request.headers.get("content-length", "0").isdigit():
            return JSONResponse(
                {"detail": {"message": "请求长度无效"}}, status_code=400
            )
        if int(request.headers.get("content-length", "0")) > 1_200_000:
            return JSONResponse({"detail": {"message": "内容过大"}}, status_code=413)
        browser_import = (
            local
            and request.url.path == BROWSER_COMPLETE
            and request.headers.get("origin") == EXTENSION_ORIGIN
        )
        if (
            local
            and request.method == "OPTIONS"
            and request.headers.get("origin") == EXTENSION_ORIGIN
            and (
                request.url.path == "/api/v1/desktop/login"
                or desktop_route(
                    request.url.path,
                    request.headers.get("access-control-request-method", ""),
                )
            )
        ):
            return JSONResponse(
                {},
                headers={
                    "Access-Control-Allow-Origin": EXTENSION_ORIGIN,
                    "Access-Control-Allow-Methods": "GET, POST, PUT",
                    "Access-Control-Allow-Headers": "content-type, x-studio-device",
                },
            )
        device_request = (
            local
            and (
                request.headers.get("origin") == EXTENSION_ORIGIN
                or (request.method == "GET" and request.headers.get("origin") is None)
            )
            and (
                request.url.path == "/api/v1/desktop/login"
                or bool(request.headers.get("x-studio-device"))
            )
        )
        if device_request and request.url.path != "/api/v1/desktop/login":
            try:
                desktop.authenticate(request)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if request.method == "OPTIONS" and browser_import:
            return JSONResponse(
                {},
                headers={
                    "Access-Control-Allow-Origin": EXTENSION_ORIGIN,
                    "Access-Control-Allow-Methods": "POST",
                    "Access-Control-Allow-Headers": "content-type",
                },
            )
        if request.method in ["POST", "PUT", "PATCH", "DELETE"] and not (
            browser_import or device_request
        ):
            request_origin = request.headers.get("origin")
            if request_origin and request_origin not in {
                parsed.scheme + "://" + h for h in allowed_hosts
            }:
                return JSONResponse(
                    {"detail": {"message": "请求来源不被允许"}}, status_code=403
                )
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse(
                    {"detail": {"message": "跨站请求已拒绝"}}, status_code=403
                )
        response = await call_next(request)
        if browser_import or device_request:
            response.headers["Access-Control-Allow-Origin"] = EXTENSION_ORIGIN
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {"detail": {"message": "输入格式不正确，请检查字段与长度。"}},
            status_code=422,
        )

    @app.exception_handler(JobError)
    async def job_error(request, exc):
        return JSONResponse(
            {"detail": {"code": exc.code, "message": ERRORS.get(exc.code, exc.code)}},
            status_code=409,
        )

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(
            {"detail": {"message": "输入格式不正确，请检查后重试。"}}, status_code=422
        )

    @app.exception_handler(EngineError)
    async def engine_error(request, exc):
        return JSONResponse(
            {"detail": {"code": exc.code, "message": ERRORS.get(exc.code, exc.code)}},
            status_code=409,
        )

    def session(request: Request):
        if request.headers.get("x-studio-device"):
            return desktop.authenticate(request)
        token = request.cookies.get("studio_session", "")
        rows = store.rows(
            "SELECT * FROM sessions WHERE token_hash=? AND expires>?",
            (digest(token), int(time.time())),
        )
        if not rows:
            fail("请先登录", 401)
        if request.method not in ["GET", "HEAD"] and not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), rows[0]["csrf"]
        ):
            fail("页面会话已更新，请刷新后重试。", 403)
        return rows[0]

    browser = BrowserConnection(app, store, vault, session, local, ERRORS)

    def login_response():
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        expiry = int(time.time()) + 86400
        with store.db() as c:
            c.execute("DELETE FROM sessions WHERE expires<?", (int(time.time()),))
            c.execute(
                "INSERT INTO sessions VALUES(?,?,?)", (digest(token), csrf, expiry)
            )
        response = JSONResponse({"authenticated": True, "csrf": csrf})
        response.set_cookie(
            "studio_session",
            token,
            httponly=True,
            secure=secure,
            samesite="strict",
            max_age=86400,
            path="/",
        )
        return response

    @app.get("/api/v1/auth/status")
    def auth_status():
        return {
            "initialized": bool(store.get("password_hash")),
            "needs_setup_token": not local,
            "brand": store.get("brand"),
        }

    @app.post("/api/v1/auth/setup")
    def setup(body: Login):
        if not local:
            expected = (
                os.environ.get("STUDIO_SETUP_TOKEN")
                or (root / "setup-token").read_text()
            )
            if not secrets.compare_digest(body.setup_token, expected):
                fail("初始化口令不正确", 403)
        with store.db(True) as c:
            if c.execute("SELECT 1 FROM settings WHERE key='password_hash'").fetchone():
                fail("管理员已创建，请登录", 409)
            store.set("password_hash", password_hash(body.password), c)
            store.event("setup", "已创建工作室管理员", c=c)
        return login_response()

    def check_login(body: Login, request: Request):
        ip = digest(request.client.host if request.client else "unknown")
        now = int(time.time())
        with store.db(True) as c:
            c.execute("DELETE FROM login_attempts WHERE ts<?", (now - 900,))
            if (
                c.execute(
                    "SELECT count(*) FROM login_attempts WHERE ip=?", (ip,)
                ).fetchone()[0]
                >= 10
            ):
                fail("尝试次数较多，请 15 分钟后重试。", 429)
            c.execute("INSERT INTO login_attempts VALUES(?,?)", (ip, now))
        if not password_check(body.password, store.get("password_hash", "")):
            fail("密码不正确", 401)
        with store.db() as c:
            c.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))

    @app.post("/api/v1/auth/login")
    def login(body: Login, request: Request):
        check_login(body, request)
        return login_response()

    @app.post("/api/v1/desktop/login")
    def desktop_login(body: Login, request: Request):
        if not local or request.headers.get("origin") != EXTENSION_ORIGIN:
            fail("请使用本机浏览器连接助手授权。", 403)
        if not store.get("password_hash"):
            fail("请先打开本机工作室设置管理员密码。", 409)
        check_login(body, request)
        return desktop.issue()

    @app.get("/api/v1/auth/session")
    def whoami(auth=Depends(session)):
        return {"authenticated": True, "csrf": auth["csrf"]}

    @app.post("/api/v1/auth/logout")
    def logout(auth=Depends(session)):
        with store.db() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (auth["token_hash"],))
        response = JSONResponse({"ok": True})
        response.delete_cookie("studio_session")
        return response

    @app.get("/api/v1/health")
    def health():
        return {"service": "alive", "version": "1.0.0", "mode": "human_published"}

    def decode(rows, fields):
        for row in rows:
            for field in fields:
                if field in row:
                    row[field] = json.loads(row[field])
        return rows

    def safe_task(row):
        return {
            k: row[k]
            for k in [
                "id",
                "kind",
                "status",
                "automatic",
                "created",
                "updated",
                "attempts",
                "error",
            ]
        } | {
            "message": ERRORS.get(row["error"], ""),
            "result": json.loads(row["result"]),
        }

    @app.get("/api/v1/overview", dependencies=[Depends(session)])
    def overview():
        cfg = store.settings()
        now = int(time.time())
        return {
            "settings": cfg,
            "persona": store.get("persona"),
            "engines": engines.list(),
            "worker_alive": now - cfg["worker_heartbeat"] < 30,
            "sources": store.rows("SELECT * FROM sources"),
            "counts": {
                "new": store.rows(
                    "SELECT count(*) AS n FROM posts WHERE status='new' AND kind!='own'"
                )[0]["n"],
                "drafts": store.rows(
                    "SELECT count(*) AS n FROM drafts WHERE status='draft'"
                )[0]["n"],
                "confirmed": store.rows(
                    "SELECT count(*) AS n FROM drafts WHERE status='confirmed'"
                )[0]["n"],
            },
            "usage": store.rows(
                "SELECT day,kind,automatic,count(*) AS count FROM generation_ledger GROUP BY day,kind,automatic ORDER BY day DESC LIMIT 20"
            ),
            "tasks": [
                safe_task(r)
                for r in store.rows(
                    "SELECT * FROM tasks ORDER BY created DESC LIMIT 12"
                )
            ],
            "events": decode(
                store.rows("SELECT * FROM events ORDER BY id DESC LIMIT 15"), ["detail"]
            ),
        }

    @app.get("/api/v1/settings", dependencies=[Depends(session)])
    def settings():
        return store.settings()

    @app.put("/api/v1/settings", dependencies=[Depends(session)])
    def update_settings(body: Settings):
        try:
            ZoneInfo(body.timezone)
        except ZoneInfoNotFoundError:
            fail("时区名称无效", 422)
        with store.db(True) as c:
            for key, value in body.model_dump().items():
                store.set(key, value, c)
            store.event("settings", "已保存工作室设置", c=c)
        return store.settings()

    @app.post("/api/v1/pause", dependencies=[Depends(session)])
    def pause(body: Toggle):
        if not body.paused:
            current = engines.list()
            active = next(e for e in current if e["id"] == store.get("engine"))
            if active["status"] != "ready":
                fail("engine_not_ready", 409)
            if store.get("x_username") and store.get("x_status") not in [
                "ready",
                "partial",
            ]:
                fail("x_not_ready", 409)
        with store.db(True) as c:
            store.set("paused", body.paused, c)
            if body.paused:
                c.execute(
                    "UPDATE tasks SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE status IN ('queued','running') AND kind NOT IN ('chat','probe')"
                )
            store.event(
                "pause", "已暂停同步与生成" if body.paused else "已恢复同步与生成", c=c
            )
        return {"paused": body.paused}

    @app.get("/api/v1/persona", dependencies=[Depends(session)])
    def persona():
        return store.get("persona")

    @app.put("/api/v1/persona", dependencies=[Depends(session)])
    def update_persona(body: Persona):
        data = {
            k: sanitize(v, vault.all().values()) if isinstance(v, str) else v
            for k, v in body.model_dump().items()
        }
        with store.db(True) as c:
            current = json.loads(
                c.execute("SELECT value FROM settings WHERE key='persona'").fetchone()[
                    0
                ]
            )
            if body.version != current["version"]:
                fail("人格资料已更新，请刷新后重试。", 409)
            data["version"] += 1
            store.set("persona", data, c)
            c.execute(
                "INSERT INTO persona_versions VALUES(?,?,?)",
                (
                    data["version"],
                    json.dumps(data, ensure_ascii=False),
                    int(time.time()),
                ),
            )
            store.event(
                "persona", "已保存新的人格版本", {"version": data["version"]}, c
            )
        return data

    @app.get("/api/v1/persona/versions", dependencies=[Depends(session)])
    def persona_versions():
        return decode(
            store.rows("SELECT * FROM persona_versions ORDER BY version DESC"), ["body"]
        )

    @app.post("/api/v1/persona/feedback", dependencies=[Depends(session)])
    def feedback(body: Text):
        with store.db() as c:
            c.execute(
                "INSERT INTO feedback(text,created) VALUES(?,?)",
                (sanitize(body.text, vault.all().values()), int(time.time())),
            )
        return {"ok": True}

    @app.post("/api/v1/persona/corpus", dependencies=[Depends(session)])
    async def corpus(body: Corpus):
        extension = Path(body.filename).suffix.lower()
        if extension not in [".txt", ".md", ".json", ".jsonl", ".csv"]:
            fail("请选择 TXT、Markdown、JSON、JSONL 或 CSV 文件", 422)
        folder = root / "corpora" / secrets.token_hex(8)
        folder.mkdir(parents=True, mode=0o700)
        path = folder / ("input" + extension)
        path.write_text(sanitize(body.content, vault.all().values()))
        path.chmod(0o600)
        cmd = [
            sys.executable,
            str(ROOT / "scripts/persona_distill.py"),
            "--input",
            str(path),
            "--output",
            str(folder / "distilled"),
            "--persona-name",
            store.get("persona")["name"],
            "--slug",
            "persona",
            "--min-records",
            "1",
        ]

        def distill():
            return subprocess.run(cmd, capture_output=True, text=True, timeout=90)

        try:
            result = await asyncio.to_thread(distill)
        except subprocess.TimeoutExpired:
            fail("语料处理超时，请缩小文件后重试。", 422)
        if result.returncode:
            fail("语料无法解析，请检查文件格式。", 422)
        data = folder / "distilled/persona/data/sanitized_corpus.jsonl"
        entries = []
        for line in data.read_text().splitlines():
            row = json.loads(line)
            if row.get("text"):
                entries.append(row["text"])
        with store.db(True) as c:
            for text in entries[:500]:
                c.execute(
                    "INSERT INTO memories(category,text,source,created) VALUES(?,?,?,?)",
                    ("style", text, "corpus", int(time.time())),
                )
            store.event("corpus", "已净化并导入人设语料", {"count": len(entries)}, c)
        return {"imported": min(500, len(entries)), "synthetic": True}

    @app.get("/api/v1/memory", dependencies=[Depends(session)])
    def memory(q: str = ""):
        if q:
            phrase = '"' + q.replace('"', '""') + '"'
            return store.rows(
                "SELECT memories.* FROM memory_fts JOIN memories ON memories.id=memory_fts.rowid WHERE memory_fts MATCH ? LIMIT 30",
                (phrase,),
            )
        return store.rows("SELECT * FROM memories ORDER BY id DESC LIMIT 30")

    @app.get("/api/v1/engines", dependencies=[Depends(session)])
    def engine_list():
        return engines.list()

    @app.put("/api/v1/engines/{engine}", dependencies=[Depends(session)])
    def engine_config(engine: str, body: EngineConfig):
        if engine not in engines.adapters:
            fail("unsupported_engine", 404)
        url = urlparse(body.base_url)
        if url.username or url.password or url.query or url.fragment:
            fail("模型地址不能包含凭据、查询参数或片段。", 422)
        if url.scheme != "https" and not (
            url.scheme == "http" and url.hostname in ["127.0.0.1", "localhost", "::1"]
        ):
            fail("远程模型接口必须使用 HTTPS。", 422)
        if not url.hostname or not body.model.strip():
            fail("请填写有效的模型与地址。", 422)
        config = {
            "model": body.model.strip(),
            "base_url": body.base_url.rstrip("/"),
            "revision": secrets.token_hex(8),
        }
        if body.api_key:
            vault.update({f"ENGINE_{engine.upper()}_API_KEY": body.api_key})
        with store.db() as c:
            c.execute(
                "UPDATE engines SET config=?,status='not_verified',checked=0 WHERE id=?",
                (json.dumps(config), engine),
            )
        return {"saved": True}

    @app.post("/api/v1/engines/{engine}/verify", dependencies=[Depends(session)])
    def engine_verify(engine: str):
        if engine not in engines.adapters:
            fail("unsupported_engine", 404)
        engines.adapters[engine].validate(engines.config(engine))
        ident = jobs.enqueue("probe", {"engine": engine}, dedupe="probe:" + engine)
        return {"task_id": ident}

    @app.post("/api/v1/engines/{engine}/select", dependencies=[Depends(session)])
    def engine_select(engine: str):
        rows = store.rows("SELECT * FROM engines WHERE id=?", (engine,))
        if not rows or rows[0]["status"] != "ready":
            fail("engine_not_ready", 409)
        store.set("engine", engine)
        store.event("engine", "已切换写作引擎", {"engine": engine})
        return {"engine": engine}

    @app.delete("/api/v1/engines/{engine}/credentials", dependencies=[Depends(session)])
    def engine_delete(engine: str):
        if engine not in engines.adapters:
            fail("unsupported_engine", 404)
        vault.delete(engine)
        with store.db() as c:
            config = engines.config(engine)
            config["revision"] = secrets.token_hex(8)
            c.execute(
                "UPDATE engines SET status='not_configured',checked=0,config=? WHERE id=?",
                (json.dumps(config), engine),
            )
        return {"ok": True}

    @app.get("/api/v1/connections", dependencies=[Depends(session)])
    def connections():
        return {
            "x": {
                "username": store.get("x_username"),
                "status": store.get("x_status"),
                "credentials": vault.present("x"),
                "verified_at": store.get("x_verified_at"),
            },
            "telegram": {
                "owner_id": store.get("owner_id"),
                "enabled": store.get("telegram_enabled"),
                "status": store.get("telegram_status"),
                "credentials": vault.present("telegram"),
            },
        }

    @app.put("/api/v1/connections/x", dependencies=[Depends(session)])
    def connect_x(body: XConfig):
        with browser.lock:
            if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", body.username):
                fail("X 用户名不正确，不需要 @。", 422)
            # A different identity would mix one-account records; disallow until an explicit future migration.
            if (
                store.get("x_verified_at")
                and store.get("x_username").lower() != body.username.lower()
            ):
                fail("此工作室已绑定其他账号，请使用独立数据目录连接新账号。", 409)
            if body.cookies is not None:
                vault.cookies(body.cookies)
            changes = {
                k: v
                for k, v in {"X_AUTH_TOKEN": body.auth_token, "X_CT0": body.ct0}.items()
                if v
            }
            if changes:
                vault.update(changes)
            store.set("x_revision", secrets.token_hex(8))
            store.set("x_username", body.username)
            store.set("x_status", "not_verified")
            store.set("paused", True)
            return {"saved": True}

    @app.post("/api/v1/connections/x/verify", dependencies=[Depends(session)])
    async def verify_x():
        revision = store.get("x_revision")
        reader = XReader(vault)
        try:
            identity = await reader.verify(store.get("x_username"))
        except ReadError as exc:
            with browser.lock:
                if revision == store.get("x_revision"):
                    store.set("x_status", exc.code)
            fail(exc.code, 409)
        finally:
            await reader.close()
        with browser.lock:
            if revision != store.get("x_revision"):
                fail("连接资料已变化，请重新验证。", 409)
            store.set("x_username", identity["username"])
            store.set("x_status", "ready")
            store.set("x_verified_at", int(time.time()))
            store.event(
                "connection", "X 账号身份已验证", {"username": identity["username"]}
            )
            return identity

    @app.delete("/api/v1/connections/x", dependencies=[Depends(session)])
    def delete_x():
        with browser.lock:
            vault.delete("x")
            store.set("x_revision", secrets.token_hex(8))
            store.set("x_status", "not_connected")
            store.set("paused", True)
            return {"ok": True}

    @app.put("/api/v1/connections/telegram", dependencies=[Depends(session)])
    def connect_telegram(body: TelegramConfig):
        if body.owner_id and not re.fullmatch(r"[1-9][0-9]{0,19}", body.owner_id):
            fail("Owner 必须是 Telegram 数字 ID。", 422)
        if body.enabled and not body.owner_id:
            fail("启用前必须填写 owner ID。", 422)
        if body.token:
            vault.update({"TELEGRAM_BOT_TOKEN": body.token})
        store.set("owner_id", body.owner_id)
        store.set("telegram_enabled", body.enabled)
        store.set("telegram_status", "not_verified")
        return {"saved": True}

    @app.delete("/api/v1/connections/telegram", dependencies=[Depends(session)])
    def delete_telegram():
        vault.delete("telegram")
        store.set("telegram_enabled", False)
        store.set("telegram_status", "not_connected")
        return {"ok": True}

    @app.post("/api/v1/sync", dependencies=[Depends(session)])
    def sync():
        return {"task_id": jobs.enqueue("sync", dedupe="sync")}

    @app.get("/api/v1/interactions", dependencies=[Depends(session)])
    def interactions():
        rows = decode(
            store.rows(
                "SELECT * FROM posts WHERE kind!='own' ORDER BY created DESC,id DESC LIMIT 200"
            ),
            ["context"],
        )
        with store.db() as c:
            for row in rows:
                parent = row["context"].get("parent_id")
                if parent:
                    previous = c.execute(
                        "SELECT text,author,url FROM posts WHERE id=?", (parent,)
                    ).fetchone()
                    row["context"]["parent"] = (
                        dict(previous) if previous else {"unavailable": True}
                    )
        return rows

    @app.post("/api/v1/interactions/{post_id}/ignore", dependencies=[Depends(session)])
    def ignore(post_id: str):
        with store.db() as c:
            c.execute("UPDATE posts SET status='ignored' WHERE id=?", (post_id,))
        return {"ok": True}

    @app.post("/api/v1/generate", dependencies=[Depends(session)])
    def generate(body: Generate):
        if body.kind not in ["post", "reply", "chat"]:
            fail("任务类型无效", 422)
        if body.kind == "reply" and not store.rows(
            "SELECT id FROM posts WHERE id=?", (body.post_id,)
        ):
            fail("post_missing", 404)
        if body.kind == "chat" and not body.text.strip():
            fail("请输入聊天内容", 422)
        if body.draft_id:
            rows = store.rows("SELECT * FROM drafts WHERE id=?", (body.draft_id,))
            if (
                not rows
                or rows[0]["version"] != body.expected_version
                or rows[0]["status"] != "draft"
            ):
                fail("草稿已变化，请刷新后重试。", 409)
        if body.draft_id and (
            rows[0]["kind"] != body.kind or rows[0]["post_id"] != body.post_id
        ):
            fail("草稿与原帖不匹配。", 409)
        payload = body.model_dump()
        payload["text"] = sanitize(payload["text"], vault.all().values())
        key = (
            "manual:" + str(body.draft_id or body.post_id)
            if body.draft_id or body.post_id
            else None
        )
        return {"task_id": jobs.enqueue(body.kind, payload, dedupe=key)}

    @app.get("/api/v1/messages", dependencies=[Depends(session)])
    def messages():
        return store.rows(
            "SELECT * FROM messages WHERE channel='web' ORDER BY id DESC LIMIT 100"
        )[::-1]

    @app.get("/api/v1/tasks", dependencies=[Depends(session)])
    def tasks():
        return [
            safe_task(r)
            for r in store.rows("SELECT * FROM tasks ORDER BY created DESC LIMIT 100")
        ]

    @app.get("/api/v1/tasks/{task_id}", dependencies=[Depends(session)])
    def task(task_id: str):
        rows = store.rows("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            fail("任务不存在", 404)
        return safe_task(rows[0])

    @app.post("/api/v1/tasks/{task_id}/cancel", dependencies=[Depends(session)])
    def cancel(task_id: str):
        with store.db() as c:
            c.execute(
                "UPDATE tasks SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=? AND status IN ('queued','running')",
                (task_id,),
            )
        return {"ok": True}

    @app.get("/api/v1/drafts", dependencies=[Depends(session)])
    def drafts():
        return store.rows(
            "SELECT drafts.*,posts.url AS source_url,posts.text AS source_text,posts.author AS source_author FROM drafts LEFT JOIN posts ON posts.id=drafts.post_id ORDER BY drafts.updated DESC LIMIT 200"
        )

    @app.post("/api/v1/drafts", dependencies=[Depends(session)])
    def create_draft(body: DraftIn):
        if body.kind not in ["post", "reply"]:
            fail("草稿类型无效", 422)
        if body.kind == "reply" and not store.rows(
            "SELECT id FROM posts WHERE id=?", (body.post_id,)
        ):
            fail("post_missing", 404)
        ident = store.draft(
            sanitize(body.text, vault.all().values()), body.kind, body.post_id
        )
        return store.rows("SELECT * FROM drafts WHERE id=?", (ident,))[0]

    @app.put("/api/v1/drafts/{draft_id}", dependencies=[Depends(session)])
    def edit_draft(draft_id: str, body: DraftEdit):
        text = sanitize(body.text, vault.all().values())
        now = int(time.time())
        with store.db(True) as c:
            row = c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if not row:
                fail("草稿不存在", 404)
            if row["version"] != body.version or row["status"] != "draft":
                fail("草稿已更新或归档，请刷新后重试。", 409)
            c.execute(
                "UPDATE drafts SET text=?,version=version+1,edited=1,updated=? WHERE id=?",
                (text, now, draft_id),
            )
            c.execute(
                "INSERT INTO draft_versions VALUES(?,?,?,?)",
                (draft_id, body.version + 1, text, now),
            )
        return store.rows("SELECT * FROM drafts WHERE id=?", (draft_id,))[0]

    @app.get("/api/v1/drafts/{draft_id}/versions", dependencies=[Depends(session)])
    def versions(draft_id: str):
        return store.rows(
            "SELECT * FROM draft_versions WHERE draft_id=? ORDER BY version DESC",
            (draft_id,),
        )

    @app.post("/api/v1/drafts/{draft_id}/opened", dependencies=[Depends(session)])
    def opened(draft_id: str):
        if not store.rows("SELECT id FROM drafts WHERE id=?", (draft_id,)):
            fail("草稿不存在", 404)
        store.event("opened", "已打开 X，尚未确认发布", {"draft_id": draft_id})
        return {"status": "opened", "verified": False}

    @app.post("/api/v1/drafts/{draft_id}/confirm", dependencies=[Depends(session)])
    def confirm(draft_id: str, body: Completion):
        url = canonical_x_url(body.result_url) if body.result_url else ""
        with store.db(True) as c:
            row = c.execute(
                "SELECT status,post_id FROM drafts WHERE id=?", (draft_id,)
            ).fetchone()
            if not row:
                fail("草稿不存在", 404)
            if row["status"] == "archived":
                fail("已归档的草稿不能确认完成", 409)
            c.execute(
                "UPDATE drafts SET status='confirmed',result_url=?,updated=? WHERE id=?",
                (url, int(time.time()), draft_id),
            )
            if row["post_id"]:
                c.execute(
                    "UPDATE posts SET status='handled' WHERE id=?", (row["post_id"],)
                )
            store.event(
                "confirmed", "用户确认已完成（未经平台验证）", {"draft_id": draft_id}, c
            )
        return {"status": "confirmed", "verified": False}

    @app.post("/api/v1/drafts/{draft_id}/archive", dependencies=[Depends(session)])
    def archive(draft_id: str):
        with store.db() as c:
            c.execute("UPDATE drafts SET status='archived' WHERE id=?", (draft_id,))
        return {"ok": True}

    @app.get("/api/v1/events", dependencies=[Depends(session)])
    def events():
        return decode(
            store.rows("SELECT * FROM events ORDER BY id DESC LIMIT 200"), ["detail"]
        )

    @app.get("/api/v1/events/stream")
    async def stream(request: Request, auth=Depends(session)):
        async def feed():
            for _ in range(15):
                if await request.is_disconnected() or time.time() > auth["expires"]:
                    break
                yield "event: refresh\ndata: {}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            feed(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.post("/api/v1/backup", dependencies=[Depends(session)])
    def backup():
        path = store.backup()
        store.event("backup", "已创建本机数据备份")
        return {"created": True, "filename": path.name, "credentials_included": False}

    @app.get("/api/v1/diagnostics", dependencies=[Depends(session)])
    def diagnostics():
        return {
            "version": "1.0.0",
            "mode": "human_published",
            "engines": engines.list(),
            "worker_alive": time.time() - store.get("worker_heartbeat") < 30,
            "x_status": store.get("x_status"),
            "telegram_status": store.get("telegram_status"),
            "sources": store.rows("SELECT * FROM sources"),
            "schema_version": 1,
            "live_x_writes": False,
            "intent_prefill_verified": False,
        }

    static = ROOT / "assets/web-admin/dist"
    if (static / "assets").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            fail("接口不存在", 404)
        if not (static / "index.html").exists():
            fail("请先构建前端", 503)
        return FileResponse(static / "index.html")

    return app
