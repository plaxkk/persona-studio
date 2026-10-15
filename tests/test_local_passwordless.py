"""Local testing may skip the password without weakening hosted device access."""

from fastapi.testclient import TestClient

from studio.api import create_app


LOCAL = "http://127.0.0.1:18880"


def client_for(app, host="127.0.0.1"):
    return TestClient(app, base_url=LOCAL, client=(host, 50000))


def test_local_passwordless_requires_private_opt_in_and_keeps_csrf(tmp_path):
    marker = tmp_path / "local-passwordless"
    marker.touch()
    with client_for(create_app(tmp_path, LOCAL)) as client:
        status = client.get("/api/v1/auth/status").json()
        assert status["initialized"] is True
        assert status["local_passwordless"] is True
        assert client.get("/api/v1/auth/session").status_code == 401

        assert client.post("/api/v1/auth/local").status_code == 403
        assert client.post(
            "/api/v1/auth/local", headers={"origin": "https://evil.example"}
        ).status_code == 403
        result = client.post("/api/v1/auth/local", headers={"origin": LOCAL})
        assert result.status_code == 200
        assert result.json()["csrf"]
        assert "studio_session" in client.cookies
        assert client.get("/api/v1/auth/session").status_code == 200
        assert client.post("/api/v1/pause", json={"paused": True}).status_code == 403
        assert client.post(
            "/api/v1/pause",
            json={"paused": True},
            headers={"x-csrf-token": result.json()["csrf"], "origin": LOCAL},
        ).status_code == 200


def test_local_passwordless_does_not_accept_remote_clients(tmp_path):
    (tmp_path / "local-passwordless").touch()
    with client_for(create_app(tmp_path, LOCAL), host="192.0.2.10") as client:
        assert client.post(
            "/api/v1/auth/local", headers={"origin": LOCAL}
        ).status_code == 403


def test_local_passwordless_disabled_by_default_and_for_remote_mode(tmp_path, monkeypatch):
    with client_for(create_app(tmp_path, LOCAL)) as client:
        assert client.get("/api/v1/auth/status").json()["local_passwordless"] is False
        assert client.post(
            "/api/v1/auth/local", headers={"origin": LOCAL}
        ).status_code == 403

    (tmp_path / "local-passwordless").touch()
    monkeypatch.setenv("STUDIO_HOST", "0.0.0.0")
    with client_for(create_app(tmp_path, LOCAL)) as client:
        assert client.get("/api/v1/auth/status").json()["local_passwordless"] is False

    monkeypatch.setenv("STUDIO_HOST", "127.0.0.1")
    with TestClient(
        create_app(tmp_path, "https://studio.example.com"),
        base_url="https://studio.example.com",
        client=("127.0.0.1", 50000),
    ) as client:
        assert client.get("/api/v1/auth/status").json()["local_passwordless"] is False
        assert client.post(
            "/api/v1/auth/local", headers={"origin": "https://studio.example.com"}
        ).status_code == 403
