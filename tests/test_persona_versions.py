import pytest
from fastapi.testclient import TestClient
from studio.api import create_app


@pytest.fixture
def workspace(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/auth/setup", json={"password": "testing-password-123"}
        )
        client.headers["x-csrf-token"] = result.json()["csrf"]
        yield client, app.state.store


def editable(body):
    return {key: value for key, value in body.items() if key != "version"}


def versions(client):
    rows = client.get("/api/v1/persona/versions").json()
    return {row["version"]: row["body"] for row in rows}


def test_rollback_restores_the_target_body_as_a_new_version(workspace):
    c, _ = workspace
    first = c.get("/api/v1/persona").json()
    assert first["version"] == 1

    # The page edits a field and saves: an ordinary new version.
    edited = c.put(
        "/api/v1/persona",
        json={**first, "voice": "更直接，少用感叹号。\n第二行也一样保留。🎯", "version": 1},
    )
    assert edited.status_code == 200
    assert edited.json()["version"] == 2

    # Rolling back is the same request shape: the target body carrying the
    # current version number. It must not delete or rewrite history.
    restored = c.put("/api/v1/persona", json={**first, "version": 2})
    assert restored.status_code == 200
    assert restored.json()["version"] == 3

    saved = versions(c)
    assert sorted(saved) == [1, 2, 3]
    assert editable(saved[3]) == editable(saved[1])
    assert saved[3]["voice"] == first["voice"]
    assert saved[2]["voice"] == "更直接，少用感叹号。\n第二行也一样保留。🎯"
    assert saved[1] == first
    assert c.get("/api/v1/persona").json() == restored.json()


def test_stale_saves_are_rejected_and_write_nothing(workspace):
    c, store = workspace
    first = c.get("/api/v1/persona").json()
    rejected = c.put("/api/v1/persona", json={**first, "voice": "抢先写入", "version": 1})
    assert rejected.status_code == 200
    stale = c.put("/api/v1/persona", json={**first, "voice": "基于旧版本的回退", "version": 1})
    assert stale.status_code == 409
    assert store.rows("SELECT version FROM persona_versions ORDER BY version") == [
        {"version": 1},
        {"version": 2},
    ]


def test_rollback_reverts_exactly_the_fields_that_had_drifted(workspace):
    c, _ = workspace
    fields = ("name", "identity", "voice", "interests", "boundaries")
    first = c.get("/api/v1/persona").json()

    drifted = c.put(
        "/api/v1/persona",
        json={**first, "voice": "试探性的语气", "interests": "露营", "version": 1},
    ).json()
    rolled = c.put("/api/v1/persona", json={**first, "version": 2}).json()
    assert rolled["version"] == 3

    # Compared with the version that drifted, exactly the two edited fields are
    # back to their v1 text — nothing else moves.
    assert [key for key in fields if rolled[key] != drifted[key]] == ["voice", "interests"]
    assert [key for key in fields if rolled[key] != first[key]] == []
