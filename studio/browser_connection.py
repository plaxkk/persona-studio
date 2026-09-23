"""Single-use, session-bound local browser pairing. Credentials never reach page JS."""

import io
import zipfile
from pathlib import Path
from fastapi.responses import Response, JSONResponse
import secrets
import threading
import time
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from .browser_identity import EXTENSION_ID, EXTENSION_ORIGIN
from .security import digest
from .xreader import XReader, ReadError

PATH = "/api/v1/browser/complete"


class ImportCookies(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=128)
    auth_token: str = Field(min_length=1, max_length=4096, pattern=r"^[^\s\x00-\x1f]+$")
    ct0: str = Field(min_length=1, max_length=4096, pattern=r"^[^\s\x00-\x1f]+$")


class BrowserConnection:
    def __init__(self, app, store, vault, session, local, errors):
        self.lock = threading.RLock()
        with store.db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS browser_pairs(
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, session_hash TEXT,
                revision TEXT, origin TEXT, created INTEGER, expires INTEGER,
                status TEXT, message TEXT DEFAULT '', username TEXT DEFAULT '')""")

        def reject(message, code=409):
            raise HTTPException(code, detail={"message": message})

        def valid_pair(c, token_hash, status):
            return c.execute(
                """SELECT p.* FROM browser_pairs p JOIN sessions s
                ON s.token_hash=p.session_hash WHERE p.token_hash=? AND p.status=?
                AND p.expires>? AND s.expires>?""",
                (token_hash, status, int(time.time()), int(time.time())),
            ).fetchone()

        @app.get("/api/v1/browser/info", dependencies=[Depends(session)])
        def info():
            return {"extension_id": EXTENSION_ID, "supported": local}

        @app.get("/api/v1/browser/extension.zip", dependencies=[Depends(session)])
        def extension_archive():
            folder = Path(__file__).resolve().parents[1] / "browser-extension"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for name in [
                    "manifest.json",
                    "background.js",
                    "popup.html",
                    "popup.js",
                    "popup.css",
                    "README.md",
                ]:
                    archive.write(folder / name, name)
            return Response(
                buffer.getvalue(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": 'attachment; filename="persona-studio-browser.zip"'
                },
            )

        @app.post("/api/v1/browser/pair")
        def pair(request: Request, owner=Depends(session)):
            if not local:
                reject("浏览器自动连接目前仅支持本机部署。", 403)
            now = int(time.time())
            token, ident = secrets.token_urlsafe(32), secrets.token_hex(16)
            with self.lock, store.db(True) as c:
                count = c.execute(
                    "SELECT count(*) FROM browser_pairs WHERE created>?", (now - 60,)
                ).fetchone()[0]
                if count >= 5:
                    reject("连接尝试较频繁，请一分钟后重试。", 429)
                c.execute(
                    "UPDATE browser_pairs SET status='cancelled' WHERE status IN ('waiting','verifying')"
                )
                c.execute("DELETE FROM browser_pairs WHERE created<?", (now - 86400,))
                c.execute(
                    "INSERT INTO browser_pairs(id,token_hash,session_hash,revision,origin,created,expires,status) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        ident,
                        digest(token),
                        owner["token_hash"],
                        store.get("x_revision"),
                        str(request.base_url).rstrip("/"),
                        now,
                        now + 300,
                        "waiting",
                    ),
                )
            return {"id": ident, "token": token, "expires": now + 300}

        @app.get("/api/v1/browser/pair/{ident}")
        def status(ident: str, owner=Depends(session)):
            rows = store.rows(
                "SELECT status,message,username,expires FROM browser_pairs WHERE id=? AND session_hash=?",
                (ident, owner["token_hash"]),
            )
            if not rows:
                reject("连接请求不存在。", 404)
            row = rows[0]
            if row["expires"] <= time.time() and row["status"] in (
                "waiting",
                "verifying",
            ):
                row["status"] = "expired"
                row["message"] = "连接等待已过期，请重新连接。"
            return row

        @app.delete("/api/v1/browser/pair/{ident}")
        def cancel(ident: str, owner=Depends(session)):
            with self.lock, store.db() as c:
                c.execute(
                    "UPDATE browser_pairs SET status='cancelled' WHERE id=? AND session_hash=? AND status IN ('waiting','verifying')",
                    (ident, owner["token_hash"]),
                )
            return {"ok": True}

        @app.post(PATH)
        async def complete(body: ImportCookies, request: Request):
            if not local or request.headers.get("origin") != EXTENSION_ORIGIN:
                reject("请求来源不被允许。", 403)
            token_hash = digest(body.token)
            with self.lock, store.db(True) as c:
                row = valid_pair(c, token_hash, "waiting")
                if not row or row["origin"] != str(request.base_url).rstrip("/"):
                    reject("连接请求已过期或已经使用。", 403)
                c.execute(
                    "UPDATE browser_pairs SET status='verifying' WHERE token_hash=?",
                    (token_hash,),
                )
            # Verify staged values, preserving the existing vault and status on failure.
            values = {"X_AUTH_TOKEN": body.auth_token, "X_CT0": body.ct0}
            reader = XReader(values)
            try:
                identity = await reader.verify(
                    store.get("x_username") if store.get("x_verified_at") else ""
                )
            except Exception as exc:
                code = exc.code if isinstance(exc, ReadError) else "connection_error"
                message = errors.get(code, "暂时无法验证 X 账号，请稍后重新连接。")
                with store.db() as c:
                    c.execute(
                        "UPDATE browser_pairs SET status='failed',message=? WHERE token_hash=? AND status='verifying'",
                        (message, token_hash),
                    )
                return {"ok": False, "code": code, "message": message}
            finally:
                await reader.close()

            def stopped(c, message):
                c.execute(
                    "UPDATE browser_pairs SET status='failed',message=? WHERE token_hash=? AND status='verifying'",
                    (message, token_hash),
                )
                return JSONResponse({"detail": {"message": message}}, status_code=409)

            with self.lock, store.db(True) as c:
                row = valid_pair(c, token_hash, "verifying")
                if not row or row["revision"] != store.get("x_revision"):
                    return stopped(c, "连接已取消或账号配置已经变化，请重新连接。")
                # Bound studio identity is checked again after network I/O.
                if (
                    store.get("x_verified_at")
                    and store.get("x_username").lower() != identity["username"].lower()
                ):
                    return stopped(c, "当前浏览器登录的账号与此工作室绑定账号不同。")
                changed = any(vault.get(k) != v for k, v in values.items())
                vault.update(values)
                for key, value in {
                    "x_revision": secrets.token_hex(8),
                    "x_username": identity["username"],
                    "x_status": "ready",
                    "x_verified_at": int(time.time()),
                }.items():
                    store.set(key, value, c)
                if changed:
                    store.set("paused", True, c)
                c.execute(
                    "UPDATE browser_pairs SET status='connected',username=?,token_hash=NULL WHERE id=?",
                    (identity["username"], row["id"]),
                )
                store.event(
                    "connection",
                    "已通过浏览器连接 X",
                    {"username": identity["username"]},
                    c,
                )
            return {"ok": True, "username": identity["username"]}
