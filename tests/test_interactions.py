import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from studio.api import create_app
from studio.browser_reads import BrowserReads
from studio.browser_identity import EXTENSION_ORIGIN
from studio.desktop import CLOUD_ORIGIN
from studio.interactions import reply_block
from studio.security import digest


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as c:
        setup = c.post("/api/v1/auth/setup", json={"password": "interaction-test-password"})
        c.headers["x-csrf-token"] = setup.json()["csrf"]
        yield c


def add_post(store, ident, *, author="someone", text="Codex 怎么处理上下文？", context=None, status="new"):
    now = int(time.time())
    details = {"source": "timeline", "reader": "browser", "published_at": now,
               "detail_checked": True, "parent_needed": False, "quote_needed": False,
               "media_pending": False, **(context or {})}
    with store.db(True) as db:
        db.execute("INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (str(ident), author, text, f"https://x.com/{author}/status/{ident}",
                    "timeline", json.dumps(details, ensure_ascii=False), status, "", now, now))


def test_page_search_past_200_and_topic_count(client):
    store = client.app.state.store
    for ident in range(1000, 1230):
        add_post(store, ident, text="旅行 签证 怎么办？" if ident == 1201 else "Codex API 怎么做？")
    result = client.get("/api/v1/interactions/page?view=all&offset=200&limit=25").json()
    assert result["total"] == 230 and len(result["items"]) == 25
    assert result["items"][0]["id"] == "1029"
    found = client.get("/api/v1/interactions/page?view=all&q=%E7%AD%BE%E8%AF%81&topic=travel").json()
    assert found["total"] == 1 and found["items"][0]["id"] == "1201"
    assert client.get("/api/v1/interactions/page?view=recommended").json()["total"] == 30
    assert client.get("/api/v1/interactions/page?limit=1000").status_code == 422


def test_context_gate_skills_and_review(client):
    store = client.app.state.store
    add_post(store, 6001, context={"detail_checked": False, "parent_needed": True})
    assert client.post("/api/v1/drafts", json={"kind": "reply", "post_id": "6001", "text": "草稿"}).status_code == 409
    assert client.post("/api/v1/generate", json={"kind": "reply", "post_id": "6001"}).status_code == 409
    before = client.get("/api/v1/interactions/page?view=pending").json()["items"][0]
    assert before["review"]["score_total"] is None
    assert client.put("/api/v1/interactions/6001/review", json={"context_note": "太短"}).status_code == 422
    assert client.post("/api/v1/interactions/skills", json={"name": "Codex", "level": "practiced", "evidence": "", "confirmed": True}).status_code == 422
    skill = client.post("/api/v1/interactions/skills", json={"name": "Codex", "level": "practiced", "evidence": "github.com/example/project", "confirmed": True}).json()
    assert skill["confirmed"] == 1
    reviewed = client.put("/api/v1/interactions/6001/review", json={
        "context_note": "已在 X 查看原帖及上文，提问是在讨论如何保存多轮对话的上下文。",
        "contribution": "补充自己的处理方式", "evidence": "github.com/example/project",
        "decision": "edited", "decision_reason": "删掉了过于正式的开头"})
    assert reviewed.json()["context_status"] == "ready"
    after = client.get("/api/v1/interactions/page?view=new").json()["items"][0]
    assert after["review"]["score_total"] >= 70
    assert after["review"]["score"]["contribution"]["value"] == 30
    assert after["review"]["decision_reason"] == "删掉了过于正式的开头"
    assert client.post("/api/v1/drafts", json={"kind": "reply", "post_id": "6001", "text": "一句回复"}).status_code == 200
    assert client.post("/api/v1/drafts", json={"kind": "reply", "post_id": "6001", "text": "重复"}).status_code == 409
    assert client.delete(f"/api/v1/interactions/skills/{skill['id']}").status_code == 200


def test_author_and_day_limits(client):
    store = client.app.state.store
    for ident in range(7000, 7025):
        add_post(store, ident, author=f"author{ident // 2}")
    for ident in (7000, 7001):
        assert client.post("/api/v1/drafts", json={"kind": "reply", "post_id": str(ident), "text": "草稿"}).status_code == 200
    add_post(store, 7999, author="author3500")
    assert reply_block(store, "7999") == "reply_author_limit"
    for ident in range(7002, 7020):
        assert client.post("/api/v1/drafts", json={"kind": "reply", "post_id": str(ident), "text": "草稿"}).status_code == 200
    assert reply_block(store, "7020") == "reply_daily_limit"


def test_existing_conversation_is_separate_from_new_author_limit(client):
    store = client.app.state.store
    store.set("x_username", "owner")
    for ident in (7101, 7102):
        add_post(store, ident, author="returning_reader")
        store.draft("先前的回复", "reply", str(ident))
    add_post(store, 7103, author="returning_reader", context={
        "parent_needed": True,
        "parent": {"id": "700", "author": "owner", "text": "之前我们讨论过上下文", "url": "https://x.com/owner/status/700"}})
    assert reply_block(store, "7103") == ""


def test_browser_detail_stays_pending_if_parent_or_media_missing(client):
    store = client.app.state.store
    store.set("x_username", "owner")
    add_post(store, 8001, context={"detail_checked": False})
    with store.db(True) as db:
        db.execute("INSERT INTO persona_browser_presence VALUES(1,'1.3.7',?)", (int(time.time()),))
        db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) VALUES(?,?,?,?,?,?,?)",
                   ("context-test", "context", '{"post_ids":["8001"]}', "running", int(time.time()), int(time.time()), int(time.time())))
        db.execute("INSERT INTO interaction_context_reads(task_id,post_id,author,account,status,updated) VALUES(?,?,?,?,?,?)",
                   ("context-test", "8001", "someone", "owner", "queued", int(time.time())))
    reads = BrowserReads(store)
    action = reads.next_action()
    assert action["kind"] == "context_read" and action["post_id"] == "8001"
    reads.receive(action["id"], {"state": "ready", "post_id": "wrong", "author": "someone", "account": "owner", "record": {}})
    assert store.rows("SELECT status FROM interaction_context_reads")[0]["status"] == "queued"
    with store.db(True) as db:
        db.execute("UPDATE interaction_context_reads SET status='sent' WHERE task_id='context-test'")
    reads.receive(action["id"], {"state": "ready", "post_id": "8001", "author": "someone", "account": "owner",
        "record": {"id": "8001", "author": "someone", "text": "完整正文", "created_at": "2026-10-13T01:00:00Z", "parent_needed": True,
                   "parent": None, "quote_needed": False, "media_pending": True,
                   "media": [{"kind": "img", "description": ""}]}})
    task = {"id": "context-test", "payload": {"post_ids": ["8001"]}}
    assert asyncio.run(reads.enrich(task, lambda _: True))["checked"] == 1
    post = client.get("/api/v1/interactions/page?view=pending").json()["items"][0]
    assert post["text"] == "完整正文"
    assert post["review"]["context_status"] == "pending"
    assert client.post("/api/v1/drafts", json={"kind": "reply", "post_id": "8001", "text": "不能发"}).status_code == 409


def test_extension_bridge_dispatches_context_id(client):
    store = client.app.state.store
    store.set("x_username", "owner")
    add_post(store, 8501, context={"detail_checked": False})
    token = "fixture-context-device"
    now = int(time.time())
    with store.db(True) as db:
        db.execute("INSERT INTO sessions(token_hash,csrf,expires) VALUES(?,?,?)", (digest(token), "fixture-csrf", now + 600))
        db.execute("INSERT INTO desktop_tokens(token_hash,origin) VALUES(?,?)", (digest(token), CLOUD_ORIGIN))
        db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) VALUES(?,?,?,?,?,?,?)",
                   ("context-ws", "context", '{"post_ids":["8501"]}', "running", now, now, now))
        db.execute("INSERT INTO interaction_context_reads(task_id,post_id,author,account,status,updated) VALUES(?,?,?,?,?,?)",
                   ("context-ws", "8501", "someone", "owner", "queued", now))
    with client.websocket_connect("/api/v1/persona-browser", headers={"origin": EXTENSION_ORIGIN}) as socket:
        socket.send_json({"token": token, "version": "1.3.7"})
        action = socket.receive_json()["action"]
        assert action["kind"] == "context_read" and action["id"].startswith("context:")
        socket.send_json({"type": "result", "id": action["id"], "data": {"state": "ready", "account": "owner", "post_id": "8501", "author": "someone",
            "record": {"id": "8501", "author": "someone", "text": "完整正文"}}})
        assert socket.receive_json()["action"] is None
    assert store.rows("SELECT status FROM interaction_context_reads WHERE task_id='context-ws'")[0]["status"] == "done"
