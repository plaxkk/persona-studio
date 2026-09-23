import time
import pytest
from fastapi.testclient import TestClient
from studio.api import create_app
from studio.browser_identity import EXTENSION_ORIGIN
from studio.desktop import desktop_route


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, "http://testserver")) as c:
        result = c.post(
            "/api/v1/auth/setup", json={"password": "desktop-test-password"}
        )
        c.headers["x-csrf-token"] = result.json()["csrf"]
        yield c


def authorize(c):
    r = c.post(
        "/api/v1/desktop/login",
        json={"password": "desktop-test-password"},
        headers={"origin": EXTENSION_ORIGIN, "sec-fetch-site": "cross-site"},
    )
    assert r.status_code == 200, r.text
    return {
        "origin": EXTENSION_ORIGIN,
        "x-studio-device": r.json()["token"],
        "sec-fetch-site": "cross-site",
    }


def test_device_lifecycle(client):
    headers = authorize(client)
    client.cookies.clear()
    client.headers.pop("x-csrf-token")
    assert client.get("/api/v1/auth/session", headers=headers).status_code == 200
    r = client.post(
        "/api/v1/drafts",
        json={"text": "桌面连接测试 🌱", "kind": "post"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert (
        client.get("/api/v1/drafts", headers=headers).json()[0]["text"]
        == "桌面连接测试 🌱"
    )
    assert (
        client.post("/api/v1/pause", json={"paused": True}, headers=headers).status_code
        == 200
    )
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/drafts", headers=headers).status_code == 401


def test_source_password_and_expiry(client):
    assert (
        client.post(
            "/api/v1/desktop/login", json={"password": "desktop-test-password"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/desktop/login",
            json={"password": "wrong-password"},
            headers={"origin": EXTENSION_ORIGIN},
        ).status_code
        == 401
    )
    h = authorize(client)
    for origin in ["https://evil.test", "https://persona-studio-plaxkk.vercel.app", ""]:
        assert (
            client.get("/api/v1/overview", headers={**h, "origin": origin}).status_code
            == 403
        )
    h2 = authorize(client)
    assert client.get("/api/v1/overview", headers=h).status_code == 401
    with client.app.state.store.db() as c:
        c.execute("UPDATE sessions SET expires=0")
    assert client.get("/api/v1/overview", headers=h2).status_code == 401


@pytest.mark.parametrize(
    "method,path",
    [
        ("PUT", "/connections/x"),
        ("PUT", "/connections/telegram"),
        ("PUT", "/engines/hermes"),
        ("POST", "/auth/setup"),
        ("POST", "/auth/login"),
        ("POST", "/browser/pair"),
        ("GET", "/events/stream"),
        ("GET", "/browser/extension.zip"),
        ("POST", "/shell"),
        ("DELETE", "/engines/hermes/credentials"),
        ("POST", "/publish"),
        ("GET", "/../.env"),
    ],
)
def test_device_forbidden(client, method, path):
    headers = authorize(client)
    response = client.request(method, "/api/v1" + path, headers=headers, json={})
    assert response.status_code == 403, response.text


def test_cookie_auth_still_needs_csrf(client):
    client.headers.pop("x-csrf-token")
    assert client.post("/api/v1/pause", json={"paused": True}).status_code == 403


def test_password_rate_limit(client):
    for i in range(10):
        assert (
            client.post(
                "/api/v1/desktop/login",
                json={"password": "wrong-password"},
                headers={"origin": EXTENSION_ORIGIN},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/desktop/login",
            json={"password": "desktop-test-password"},
            headers={"origin": EXTENSION_ORIGIN},
        ).status_code
        == 429
    )


def test_extension_get_without_origin_uses_device_token(client):
    headers = authorize(client)
    token = headers["x-studio-device"]
    client.cookies.clear()
    assert (
        client.get("/api/v1/overview", headers={"x-studio-device": token}).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/pause", headers={"x-studio-device": token}, json={"paused": True}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/overview", headers={"x-studio-device": "invalid"}
        ).status_code
        == 401
    )
