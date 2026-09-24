"""Origin-bound device sessions for the browser extension; never expose a shell."""

import re
import secrets
import time
from fastapi import HTTPException
from .browser_identity import EXTENSION_ORIGIN
from .security import digest

CLOUD_ORIGIN = "https://persona-studio-plaxkk.vercel.app"


def desktop_route(path, method):
    if (
        re.fullmatch(
            r"/api/v1/persona-imports(?:/(?:preflight|[a-f0-9]{32}(?:/export)?))?", path
        )
        and method == "GET"
    ):
        return True
    if (
        re.fullmatch(
            r"/api/v1/persona-imports(?:/[a-f0-9]{32}/(?:pause|resume|cancel|analyze|apply|delete))?",
            path,
        )
        and method == "POST"
    ):
        return True
    if method == "GET":
        return bool(
            re.fullmatch(
                r"/api/v1/(auth/(status|session)|health|overview|settings|persona(/versions)?|memory|engines|connections|interactions|messages|tasks(/[a-f0-9-]+)?|drafts(/[a-f0-9-]+/versions)?|events|diagnostics)",
                path,
            )
        )
    if method == "POST":
        return bool(
            re.fullmatch(
                r"/api/v1/(auth/logout|pause|persona/(feedback|corpus)|engines/(hermes|openclaw|codex)/(verify|select)|sync|interactions/[0-9]+/ignore|generate|tasks/[a-f0-9-]+/cancel|drafts(/[a-f0-9-]+/(opened|confirm|archive))?|backup)",
                path,
            )
        )
    return method == "PUT" and bool(
        re.fullmatch(r"/api/v1/(settings|persona|drafts/[a-f0-9-]+)", path)
    )


class Desktop:
    def __init__(self, store):
        self.store = store
        with store.db() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS desktop_tokens(token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE, origin TEXT NOT NULL)"
            )

    def issue(self):
        token = secrets.token_urlsafe(32)
        expires = int(time.time()) + 86400
        with self.store.db(True) as c:
            # One authorized browser session per studio. New pairing revokes the old one.
            c.execute(
                "DELETE FROM sessions WHERE token_hash IN (SELECT token_hash FROM desktop_tokens)"
            )
            c.execute(
                "INSERT INTO sessions VALUES(?,?,?)",
                (digest(token), secrets.token_urlsafe(24), expires),
            )
            c.execute(
                "INSERT INTO desktop_tokens VALUES(?,?)", (digest(token), CLOUD_ORIGIN)
            )
            self.store.event(
                "desktop", "已授权云端面板连接本机", {"origin": CLOUD_ORIGIN}, c
            )
        return {"token": token, "expires": expires, "origin": CLOUD_ORIGIN}

    def authenticate(self, request):
        origin = request.headers.get("origin")
        origin_ok = origin == EXTENSION_ORIGIN or (
            origin is None and request.method == "GET"
        )
        if not origin_ok or not desktop_route(request.url.path, request.method):
            raise HTTPException(403, detail={"message": "此操作需在本机工作室中完成。"})
        rows = self.store.rows(
            """SELECT s.* FROM sessions s JOIN desktop_tokens d ON s.token_hash=d.token_hash
            WHERE s.token_hash=? AND s.expires>? AND d.origin=?""",
            (
                digest(request.headers.get("x-studio-device", "")),
                int(time.time()),
                CLOUD_ORIGIN,
            ),
        )
        if not rows:
            raise HTTPException(
                401, detail={"message": "本机连接授权已过期，请在扩展中重新授权。"}
            )
        return rows[0]
