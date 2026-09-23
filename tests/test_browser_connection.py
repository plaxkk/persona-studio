import asyncio
import hashlib
import base64
import io
import json
import time
import zipfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from studio.api import create_app
from studio.browser_identity import EXTENSION_ID, EXTENSION_ORIGIN
from studio.security import Secrets
from studio.xreader import ReadError


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/setup", json={"password": "browser-test-password"})
        c.headers["x-csrf-token"] = r.json()["csrf"]
        yield c


@pytest.fixture
def verified(monkeypatch):
    async def verify(self, expected=""):
        assert self.secrets.get("X_AUTH_TOKEN") == "fixture-auth"
        assert self.secrets.get("X_CT0") == "fixture-csrf"
        if expected and expected != "fixture_owner":
            raise ReadError("identity_mismatch")
        return {
            "id": "1234567890123456789",
            "username": "fixture_owner",
            "name": "Fixture",
        }

    monkeypatch.setattr("studio.browser_connection.XReader.verify", verify)


def pair(c):
    r = c.post("/api/v1/browser/pair", json={})
    assert r.status_code == 200, r.text
    return r.json()


def complete(c, p, **kw):
    headers = {"origin": EXTENSION_ORIGIN, "sec-fetch-site": "cross-site"}
    headers.update(kw.pop("headers", {}))
    return c.post(
        "/api/v1/browser/complete",
        json={"token": p["token"], "auth_token": "fixture-auth", "ct0": "fixture-csrf"},
        headers=headers,
        **kw,
    )


def test_success_write_only_replay(client, verified):
    store = client.app.state.store
    p = pair(client)
    assert complete(client, p).json() == {"ok": True, "username": "fixture_owner"}
    assert Secrets(store.root).get("X_AUTH_TOKEN") == "fixture-auth"
    assert Secrets(store.root).path.stat().st_mode & 0o777 == 0o600
    assert store.get("paused") is True
    status = client.get("/api/v1/browser/pair/" + p["id"]).json()
    assert status["status"] == "connected"
    assert "token" not in str(status)
    assert complete(client, p).status_code == 403
    assert "fixture-auth" not in client.get("/api/v1/connections").text
    assert "fixture-auth" not in str(store.rows("SELECT * FROM events"))
    assert "fixture-auth" not in str(store.rows("SELECT * FROM browser_pairs"))


def test_unchanged_cookie_does_not_pause(client, verified):
    assert complete(client, pair(client)).status_code == 200
    client.app.state.store.set("paused", False)
    assert complete(client, pair(client)).status_code == 200
    assert client.app.state.store.get("paused") is False


@pytest.mark.parametrize(
    "mode",
    [
        "expired",
        "cancelled",
        "replaced",
        "logged_out",
        "revision",
        "wrong_origin",
        "wrong_host",
        "missing_origin",
    ],
)
def test_rejected_pair_preserves_existing(client, verified, mode):
    store = client.app.state.store
    vault = Secrets(store.root)
    vault.update({"X_AUTH_TOKEN": "existing", "X_CT0": "existing"})
    p = pair(client)
    kwargs = {}
    if mode == "expired":
        with store.db() as c:
            c.execute("UPDATE browser_pairs SET expires=0")
    elif mode == "cancelled":
        assert client.delete("/api/v1/browser/pair/" + p["id"]).status_code == 200
    elif mode == "replaced":
        pair(client)
    elif mode == "logged_out":
        client.post("/api/v1/auth/logout", json={})
    elif mode == "revision":
        store.set("x_revision", "newer-config")
    elif mode == "wrong_origin":
        kwargs["headers"] = {"origin": "https://evil.example"}
    elif mode == "wrong_host":
        kwargs["headers"] = {"host": "localhost:18880"}
    elif mode == "missing_origin":
        kwargs["headers"] = {"origin": ""}
    assert complete(client, p, **kwargs).status_code in (403, 409)
    assert vault.get("X_AUTH_TOKEN") == "existing"


@pytest.mark.parametrize("error", ["needs_login", "network_error", "identity_mismatch"])
def test_verification_failure_keeps_credentials(client, monkeypatch, error):
    async def fail(self, expected=""):
        raise ReadError(error)

    monkeypatch.setattr("studio.browser_connection.XReader.verify", fail)
    store = client.app.state.store
    Secrets(store.root).update({"X_AUTH_TOKEN": "existing", "X_CT0": "existing"})
    p = pair(client)
    assert complete(client, p).json()["ok"] is False
    assert client.get("/api/v1/browser/pair/" + p["id"]).json()["status"] == "failed"
    assert Secrets(store.root).get("X_AUTH_TOKEN") == "existing"
    assert complete(client, p).status_code == 403


def test_scope_csrf_session_and_cors(client):
    old = client.headers.pop("x-csrf-token")
    assert client.post("/api/v1/browser/pair", json={}).status_code == 403
    client.headers["x-csrf-token"] = old
    p = pair(client)
    assert (
        client.put(
            "/api/v1/settings", json={}, headers={"origin": EXTENSION_ORIGIN}
        ).status_code
        == 403
    )
    r = client.options(
        "/api/v1/browser/complete",
        headers={"origin": EXTENSION_ORIGIN, "access-control-request-method": "POST"},
    )
    assert r.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    client.cookies.clear()
    assert client.get("/api/v1/browser/pair/" + p["id"]).status_code == 401
    assert client.post("/api/v1/browser/pair", json={}).status_code == 401


def test_other_admin_session_cannot_read_pair(client):
    p = pair(client)
    client.post("/api/v1/auth/login", json={"password": "browser-test-password"})
    assert client.get("/api/v1/browser/pair/" + p["id"]).status_code == 404


def test_validation_no_echo(client):
    p = pair(client)
    r = client.post(
        "/api/v1/browser/complete",
        headers={"origin": EXTENSION_ORIGIN},
        json={"token": p["token"], "auth_token": "secret\nunsafe", "ct0": "x"},
    )
    assert r.status_code == 422 and "secret" not in r.text


def test_archive_and_identity(client):
    r = client.get("/api/v1/browser/extension.zip")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert set(z.namelist()) == {
            "manifest.json",
            "desktop.js",
            "persona-browser.js",
            "persona-page.js",
            "background.js",
            "popup.js",
            "popup.html",
            "popup.css",
            "README.md",
        }
        manifest = json.loads(z.read("manifest.json"))
    actual = "".join(
        chr(97 + int(c, 16))
        for c in hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    )
    assert actual == EXTENSION_ID
    assert manifest["permissions"] == ["cookies", "storage", "alarms", "nativeMessaging", "scripting"]
    assert "https://x.com/*" in manifest["host_permissions"]
    assert "*://*/*" not in manifest["host_permissions"]


def test_pair_rate_limit(client):
    for _ in range(5):
        pair(client)
    assert client.post("/api/v1/browser/pair", json={}).status_code == 429
