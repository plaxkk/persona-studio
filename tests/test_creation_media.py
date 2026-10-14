"""Attachments travel in chunks; the probes below are deliberately small.

CHUNK is 384 KiB, so a real two-chunk upload would mean a ~400 KB fixture in the
test file. Instead every case here pins the staging contract (sidecar metadata,
declared size, sha256, append order) and one case drives the full two-chunk path
with CHUNK patched down, so the arithmetic is exercised without the payload.
"""
import base64
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from studio.api import create_app
from studio import creation_media
from studio.desktop import desktop_route


@pytest.fixture
def workspace(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/auth/setup", json={"password": "testing-password-123"}
        )
        client.headers["x-csrf-token"] = result.json()["csrf"]
        yield client, app.state.store


@pytest.fixture
def creation(workspace):
    client, store = workspace
    ident = client.post("/api/v1/creations", json={"idea": "配图说明短链"}).json()["id"]
    return client, store, ident


def upload(client, ident, name, mime, payload, chunk=None):
    """Drive the three-step handshake the panel uses; returns the final response."""
    chunk = chunk or creation_media.CHUNK
    digest = hashlib.sha256(payload).hexdigest()
    started = client.post(
        f"/api/v1/creations/{ident}/media",
        json={"filename": name, "mime": mime, "bytes": len(payload), "sha256": digest},
    )
    assert started.status_code == 200, started.text
    meta = started.json()
    for index in range(0, len(payload), chunk):
        block = payload[index : index + chunk]
        r = client.post(
            f"/api/v1/creations/{ident}/media/{meta['id']}/chunks",
            json={"index": index // chunk, "data": base64.b64encode(block).decode()},
        )
        assert r.status_code == 200, r.text
    return meta, client.post(f"/api/v1/creations/{ident}/media/{meta['id']}/complete")


def test_upload_stores_verifies_and_serves_back_the_same_bytes(creation):
    c, store, ident = creation
    payload = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    meta, done = upload(c, ident, "架构图.png", "image/png", payload)
    assert done.status_code == 200, done.text
    row = done.json()
    assert row["bytes"] == len(payload)
    assert row["sha256"] == hashlib.sha256(payload).hexdigest()
    assert row["kind"] == "image" and row["filename"] == "架构图.png"
    assert "path" not in row  # the local file path never leaves the machine
    stored = store.rows("SELECT * FROM creation_media WHERE id=?", (meta["id"],))[0]
    assert (store.root / stored["path"]).read_bytes() == payload
    assert not (store.root / "media" / ident / (meta["id"] + ".part")).exists()
    # Reading back is chunked too, and reassembles to the original.
    first = c.get(f"/api/v1/creations/{ident}/media/{meta['id']}/chunks/0").json()
    assert first["chunks"] == 1 and first["mime"] == "image/png"
    assert base64.b64decode(first["data"]) == payload
    assert c.get(f"/api/v1/creations/{ident}/media/{meta['id']}/chunks/1").status_code == 404
    assert [item["id"] for item in c.get(f"/api/v1/creations/{ident}/media").json()] == [meta["id"]]


def test_multi_chunk_upload_reassembles_in_order(creation, monkeypatch):
    monkeypatch.setattr(creation_media, "CHUNK", 8)
    c, store, ident = creation
    payload = bytes(range(40))
    meta, done = upload(c, ident, "clip.webm", "video/webm", payload)
    # `start` reports the chunk count from the module constant, so it must agree.
    assert done.status_code == 200, done.text
    assert done.json()["bytes"] == 40
    stored = store.rows("SELECT * FROM creation_media WHERE id=?", (meta["id"],))[0]
    assert (store.root / stored["path"]).read_bytes() == payload
    last = c.get(f"/api/v1/creations/{ident}/media/{meta['id']}/chunks/4").json()
    assert base64.b64decode(last["data"]) == payload[32:40]
    assert last["chunks"] == 5


def test_out_of_order_and_corrupt_chunks_are_refused(creation):
    c, store, ident = creation
    payload = b"x" * 64
    started = c.post(
        f"/api/v1/creations/{ident}/media",
        json={"filename": "a.png", "mime": "image/png", "bytes": 64},
    ).json()
    url = f"/api/v1/creations/{ident}/media/{started['id']}/chunks"
    assert c.post(url, json={"index": 1, "data": "eA=="}).status_code == 409
    assert c.post(url, json={"index": 0, "data": "not base64!"}).status_code == 422
    assert c.post(url, json={"index": 0, "data": "eA=="}).status_code == 200
    # 64 bytes declared, 63 written: complete must refuse and leave nothing behind.
    over = c.post(url, json={"index": 0, "data": base64.b64encode(b"y" * 63).decode()})
    assert over.status_code == 200
    assert len(store.rows("SELECT * FROM creation_media")) == 0
    assert c.get(f"/api/v1/creations/{ident}/media/{started['id']}/chunks/0").status_code == 404


def test_short_upload_and_wrong_digest_leave_no_attachment(creation):
    c, store, ident = creation
    payload = b"z" * 32
    started = c.post(
        f"/api/v1/creations/{ident}/media",
        json={
            "filename": "a.png",
            "mime": "image/png",
            "bytes": 64,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    ).json()
    c.post(
        f"/api/v1/creations/{ident}/media/{started['id']}/chunks",
        json={"index": 0, "data": base64.b64encode(payload).decode()},
    )
    short = c.post(f"/api/v1/creations/{ident}/media/{started['id']}/complete")
    assert short.status_code == 422 and "只收到 32 字节" in short.json()["detail"]
    assert store.rows("SELECT * FROM creation_media") == []
    # Right size, wrong digest (a corrupted body that happens to be long enough).
    started = c.post(
        f"/api/v1/creations/{ident}/media",
        json={
            "filename": "a.png",
            "mime": "image/png",
            "bytes": 32,
            "sha256": "0" * 64,
        },
    ).json()
    c.post(
        f"/api/v1/creations/{ident}/media/{started['id']}/chunks",
        json={"index": 0, "data": base64.b64encode(payload).decode()},
    )
    assert (
        c.post(f"/api/v1/creations/{ident}/media/{started['id']}/complete").status_code
        == 422
    )
    assert store.rows("SELECT * FROM creation_media") == []


def test_unsupported_types_and_limits_are_reported_by_name(creation):
    c, store, ident = creation
    def start(name, mime, size):
        return c.post(
            f"/api/v1/creations/{ident}/media",
            json={"filename": name, "mime": mime, "bytes": size},
        )
    assert start("a.pdf", "application/pdf", 10).status_code == 422
    assert start("a.svg", "image/svg+xml", 10).status_code == 422
    assert start("a.png", "image/png", 0).status_code == 422
    assert start("big.mp4", "video/mp4", creation_media.MAX_BYTES + 1).status_code == 422
    # Aliases are normalised, so x-png counts as a PNG. This stages a file, which
    # holds one of the four slots, so it gets its own record.
    scratch = c.post("/api/v1/creations", json={"idea": "别名的创作"}).json()["id"]
    alias = c.post(
        f"/api/v1/creations/{scratch}/media",
        json={"filename": "a.png", "mime": "image/x-png", "bytes": 10},
    )
    assert alias.json()["mime"] == "image/png"
    # Four attachments fit; the fifth is refused before a byte is sent.
    for index in range(4):
        meta, done = upload(c, ident, f"{index}.png", "image/png", b"p" * 8)
        assert done.status_code == 200, done.text
    fifth = start("4.png", "image/png", 8)
    assert fifth.status_code == 422 and "最多 4 个附件" in fifth.json()["detail"]
    # A second video is refused even though a slot is free.
    second = c.post("/api/v1/creations", json={"idea": "带视频的创作"}).json()["id"]
    meta, done = upload(c, second, "clip.mp4", "video/mp4", b"v" * 8)
    assert done.status_code == 200 and done.json()["kind"] == "video"
    dup = c.post(
        f"/api/v1/creations/{second}/media",
        json={"filename": "two.mp4", "mime": "video/mp4", "bytes": 8},
    )
    assert dup.status_code == 422 and "只放一个视频" in dup.json()["detail"]


def test_abandoned_uploads_are_swept_and_never_claim_to_be_attachments(creation):
    c, store, ident = creation
    started = c.post(
        f"/api/v1/creations/{ident}/media",
        json={"filename": "a.png", "mime": "image/png", "bytes": 8},
    ).json()
    folder = store.root / "media" / ident
    assert (folder / (started["id"] + ".json")).exists()
    assert store.rows("SELECT * FROM creation_media") == []
    assert c.get(f"/api/v1/creations/{ident}/media").json() == []
    # A stale window (the sweep age) removes both staging files.
    media = creation_media.Media(store)
    media.sweep(ident, now=(folder / (started["id"] + ".json")).stat().st_mtime + creation_media.STALE_AFTER + 1)
    assert not (folder / (started["id"] + ".json")).exists()
    assert not (folder / (started["id"] + ".part")).exists()
    late = c.post(
        f"/api/v1/creations/{ident}/media/{started['id']}/chunks",
        json={"index": 0, "data": base64.b64encode(b"p" * 8).decode()},
    )
    assert late.status_code == 404 and "已过期" in late.json()["detail"]


def test_delete_removes_row_and_file_but_keeps_other_attachments(creation):
    c, store, ident = creation
    first, _ = upload(c, ident, "one.png", "image/png", b"a" * 8)
    second, _ = upload(c, ident, "two.png", "image/png", b"b" * 8)
    path = store.root / store.rows(
        "SELECT path FROM creation_media WHERE id=?", (first["id"],)
    )[0]["path"]
    assert c.delete(f"/api/v1/creations/{ident}/media/{first['id']}").status_code == 200
    assert not path.exists()
    assert [item["id"] for item in c.get(f"/api/v1/creations/{ident}/media").json()] == [
        second["id"]
    ]
    assert c.delete(f"/api/v1/creations/{ident}/media/{first['id']}").status_code == 404


def test_attachments_follow_the_draft_and_stay_editable_after_finalize(creation):
    c, store, ident = creation
    meta, _ = upload(c, ident, "配图.png", "image/png", b"p" * 16)
    assert store.rows("SELECT draft_id FROM creation_media WHERE id=?", (meta["id"],))[0][
        "draft_id"
    ] is None
    c.put(f"/api/v1/creations/{ident}", json={"version": 1, "idea": "配图说明短链", "candidate": "候选正文"})
    draft_id = c.post(f"/api/v1/creations/{ident}/finalize", json={"version": 2}).json()["draft_id"]
    assert store.rows("SELECT draft_id FROM creation_media WHERE id=?", (meta["id"],))[0][
        "draft_id"
    ] == draft_id
    # Finalizing pinned the attachment to the draft; the record is still editable,
    # and a late attachment joins the same draft rather than being orphaned.
    extra, _ = upload(c, ident, "补充.png", "image/png", b"q" * 16)
    linked = store.rows("SELECT draft_id FROM creation_media WHERE id=?", (extra["id"],))
    assert linked[0]["draft_id"] == draft_id
    assert c.get("/api/v1/drafts").json()[0]["id"] == draft_id


def test_attachment_routes_require_an_existing_creation(creation):
    c, store, ident = creation
    media, _ = upload(c, ident, "a.png", "image/png", b"p" * 8)
    ghost = "f" * 32
    assert c.get(f"/api/v1/creations/{ghost}/media").status_code == 404
    assert c.post(
        f"/api/v1/creations/{ghost}/media",
        json={"filename": "a.png", "mime": "image/png", "bytes": 8},
    ).status_code == 404
    # Another creation's id does not unlock this attachment.
    other = c.post("/api/v1/creations", json={"idea": "另一条"}).json()["id"]
    assert c.get(f"/api/v1/creations/{other}/media/{media['id']}/chunks/0").status_code == 404
    assert c.delete(f"/api/v1/creations/{other}/media/{media['id']}").status_code == 404
    assert len(store.rows("SELECT * FROM creation_media")) == 1


def test_attachment_routes_pass_the_device_gate_and_neighbours_do_not():
    ident, media = "a" * 32, "b" * 32
    allowed = [
        ("GET", f"/api/v1/creations/{ident}/media"),
        ("POST", f"/api/v1/creations/{ident}/media"),
        ("POST", f"/api/v1/creations/{ident}/media/{media}/chunks"),
        ("POST", f"/api/v1/creations/{ident}/media/{media}/complete"),
        ("GET", f"/api/v1/creations/{ident}/media/{media}/chunks/3"),
        ("DELETE", f"/api/v1/creations/{ident}/media/{media}"),
    ]
    for method, path in allowed:
        assert desktop_route(path, method), path
    denied = [
        ("GET", f"/api/v1/creations/{ident}/media/{media}"),
        ("GET", f"/api/v1/creations/{ident}/media/{media}/chunks"),
        ("GET", f"/api/v1/creations/{ident}/media/{media}/complete"),
        ("POST", f"/api/v1/creations/{ident}/media/{media}/chunks/3"),
        ("DELETE", f"/api/v1/creations/{ident}/media/{media}/chunks/3"),
        ("POST", f"/api/v1/creations/{ident}/media/{media}"),
        ("GET", f"/api/v1/creations/{ident}/media/{media.upper()}/chunks/0"),
        ("GET", "/api/v1/media"),
    ]
    for method, path in denied:
        assert not desktop_route(path, method), path


def test_chunk_size_fits_every_cap_on_the_path_to_the_panel():
    """The chunk size is load-bearing: three separate limits sit on this route.

    A chunk travels as base64 inside JSON, first through the connection
    assistant (payload cap) and then through the local HTTP API (body cap), and
    the request model caps the field itself. Raising CHUNK without raising them
    would make every large attachment fail after the whole file was read.
    """
    encoded = 4 * ((creation_media.CHUNK + 2) // 3)  # base64 length of one chunk
    assert encoded + 512 < 1_100_000, "超过连接助手的请求上限"
    assert encoded + 512 < 1_200_000, "超过本机接口的请求体上限"
    assert encoded < 600_000, "超过 MediaChunk.data 的字段上限"


def test_staging_metadata_is_not_world_readable(creation, tmp_path):
    c, store, ident = creation
    started = c.post(
        f"/api/v1/creations/{ident}/media",
        json={"filename": "a.png", "mime": "image/png", "bytes": 8},
    ).json()
    sidecar = store.root / "media" / ident / (started["id"] + ".json")
    assert json.loads(sidecar.read_text())["bytes"] == 8
    assert sidecar.stat().st_mode & 0o077 == 0
