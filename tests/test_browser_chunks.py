import base64
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from studio.browser_chunks import ChunkError, MAX_CHUNK_BYTES, ResultChunks
from studio.api import create_app
from studio.browser_identity import EXTENSION_ORIGIN
from studio.desktop import CLOUD_ORIGIN
from studio.security import digest


def frames(message, size=MAX_CHUNK_BYTES):
    raw = json.dumps(message, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    parts = [raw[i:i + size] for i in range(0, len(raw), size)]
    return [{"type": "result_chunk", "id": message["id"], "index": i,
             "total": len(parts), "sha256": digest,
             "body": base64.b64encode(part).decode("ascii")}
            for i, part in enumerate(parts)]


def test_large_unicode_result_reassembles_with_digest():
    message = {"type": "result", "id": "analytics-1", "data": {"text": "中文😊" * 70_000}}
    chunks = frames(message)
    assert len(chunks) > 5
    reader = ResultChunks("analytics-1")
    for chunk in chunks[:-1]:
        assert reader.accept(chunk) is None
    assert reader.accept(chunks[-1]) == message


@pytest.mark.parametrize("change", [
    lambda chunk: chunk.update(index=2),
    lambda chunk: chunk.update(id="another-task"),
    lambda chunk: chunk.update(body="not base64"),
    lambda chunk: chunk.update(sha256="0" * 64),
])
def test_bad_or_out_of_order_chunk_is_rejected(change):
    message = {"type": "result", "id": "analytics-1", "data": {"text": "a" * 100_000}}
    chunks = frames(message, 50_000)
    reader = ResultChunks("analytics-1")
    assert reader.accept(chunks[0]) is None
    change(chunks[1])
    with pytest.raises(ChunkError):
        for chunk in chunks[1:]:
            reader.accept(chunk)


def test_missing_chunk_never_returns_complete_result():
    message = {"type": "result", "id": "analytics-1", "data": {"text": "a" * 100_000}}
    chunks = frames(message, 50_000)
    reader = ResultChunks("analytics-1")
    for chunk in chunks[:-1]:
        assert reader.accept(chunk) is None


def test_chunked_result_must_match_dispatched_action():
    message = {"type": "result", "id": "another-task", "data": {}}
    chunk = frames(message)[0]
    chunk["id"] = "analytics-1"
    with pytest.raises(ChunkError, match="result_action_mismatch"):
        ResultChunks("analytics-1").accept(chunk)


def test_authenticated_bridge_accepts_complete_chunked_result(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    store.set("x_username", "kkplayit")
    token = "fixture-chunk-device"
    now = int(time.time())
    with store.db(True) as db:
        db.execute("INSERT INTO sessions(token_hash,csrf,expires) VALUES(?,?,?)",
                   (digest(token), "csrf", now + 600))
        db.execute("INSERT INTO desktop_tokens(token_hash,origin) VALUES(?,?)",
                   (digest(token), CLOUD_ORIGIN))
        db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) VALUES(?,?,?,?,?,?,?)",
                   ("chunk-task", "sync", "{}", "running", now, now, now))
        db.execute("INSERT INTO browser_reads(task_id,source,account,status,updated) VALUES(?,?,?,'queued',?)",
                   ("chunk-task", "own", "kkplayit", now))
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/persona-browser", headers={"origin": EXTENSION_ORIGIN}) as socket:
            socket.send_json({"token": token, "version": "1.3.10"})
            action = socket.receive_json()["action"]
            message = {"type": "result", "id": action["id"], "data": {
                "state": "ready", "account": "kkplayit", "records": []}}
            for chunk in frames(message, 25):
                socket.send_json(chunk)
            assert socket.receive_json()["action"] is None
    assert store.rows("SELECT status FROM browser_reads WHERE task_id='chunk-task'")[0]["status"] == "done"
