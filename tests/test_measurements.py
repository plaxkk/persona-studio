import asyncio
import sqlite3
import time
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from studio.api import create_app
from studio.browser_reads import BrowserReads
from studio.store import Store


def test_v3_confirmed_draft_migrates_without_inventing_timestamps(tmp_path):
    path = tmp_path / 'studio.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE migrations(version INTEGER PRIMARY KEY,applied INTEGER NOT NULL)')
        db.execute('INSERT INTO migrations VALUES(3,1)')
        db.execute("CREATE TABLE drafts(id TEXT PRIMARY KEY,kind TEXT NOT NULL,text TEXT NOT NULL,post_id TEXT,status TEXT NOT NULL DEFAULT 'draft',version INTEGER NOT NULL DEFAULT 1,edited INTEGER NOT NULL DEFAULT 0,persona_version INTEGER NOT NULL,engine TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL,updated INTEGER NOT NULL,result_url TEXT NOT NULL DEFAULT '')")
        db.execute("CREATE TABLE draft_versions(draft_id TEXT NOT NULL,version INTEGER NOT NULL,text TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(draft_id,version))")
        db.execute("INSERT INTO drafts(id,kind,text,status,persona_version,created,updated,result_url) VALUES('old','post','旧草稿','confirmed',1,100,200,'https://x.com/owner/status/123')")
        db.execute("INSERT INTO draft_versions VALUES('old',1,'旧草稿',100)")
    store = Store(tmp_path)
    row = store.rows("SELECT * FROM draft_publications WHERE draft_id='old'")[0]
    assert row['user_confirmed'] == 1 and row['tweet_id'] == '123'
    assert row['user_confirmed_at'] is None and row['platform_observed_at'] is None and row['published_at'] is None
    assert store.rows("SELECT reason FROM draft_versions WHERE draft_id='old'")[0]['reason'] == ''
    assert store.rows("SELECT max(version) AS version FROM migrations")[0]['version'] == 7


def test_confirmed_publication_stays_unverified_until_browser_read(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as client:
        setup = client.post("/api/v1/auth/setup", json={"password": "testing-password-123"}).json()
        client.headers["x-csrf-token"] = setup["csrf"]
        store = app.state.store
        store.set("x_username", "owner")
        draft = client.post("/api/v1/drafts", json={"text": "一个想法"}).json()
        base = "/api/v1/drafts/" + draft["id"]
        assert client.put(base, json={"text": "一个更具体的想法", "version": 1, "reason": "信息不足"}).status_code == 200
        versions = client.get(base + "/versions").json()
        assert versions[0]["reason"] == "信息不足"
        assert versions[1]["text"] == "一个想法"
        assert client.post(base + "/confirm", json={"result_url": "https://x.com/other/status/123"}).status_code == 422
        assert client.post(base + "/confirm", json={"result_url": "https://x.com/owner/status/123", "publication_kind": "quote"}).json()["verified"] is False
        publication = client.get(base + "/metrics").json()["publication"]
        assert publication["tweet_id"] == "123"
        assert publication["user_confirmed_at"] is not None
        assert publication["platform_observed_at"] is None
        assert client.get(base + "/metrics").json()["snapshots"] == []
        assert client.put("/api/v1/growth/goal", json={"target_followers": 1000}).json()["remaining"] is None

        store.set("paused", False)
        with store.db() as db:
            db.execute("INSERT INTO persona_browser_presence VALUES(1,'1.3.8',?)", (int(time.time()),))
        reader = BrowserReads(store)

        async def collect():
            job = {"id": "fixture-metric", "payload": {"tweet_id": "123", "target_window": "24h"}}
            with store.db() as db:
                db.execute("INSERT INTO tasks(id,kind,payload,status,created,updated,available) VALUES(?,?,?,'running',?,?,?)",
                           (job["id"], "metric", '{}', int(time.time()), int(time.time()), int(time.time())))
            work = asyncio.create_task(reader.metric(job, lambda _: True))
            for _ in range(100):
                action = reader.next_action()
                if action: break
                await asyncio.sleep(0.01)
            assert action["kind"] == "metric_read"
            published = datetime.fromtimestamp(time.time() - 3 * 86400, timezone.utc).isoformat()
            reader.receive(action["id"], {"state": "ready", "account": "owner", "post_id": "123", "author": "owner",
                                          "record": {"created_at": published, "kind": "quote", "views": 230, "likes": 12,
                                                     "replies": None, "reposts": 3}})
            return await work

        result = asyncio.run(collect())
        assert result["window_status"] == "late"
        record = client.get(base + "/metrics").json()
        assert record["publication"]["platform_observed_at"] is not None
        assert record["snapshots"][0]["target_window"] == "24h"
        assert record["snapshots"][0]["window_status"] == "late"
        assert record["snapshots"][0]["views"] == 230
        assert record["snapshots"][0]["replies"] is None
        assert record["snapshots"][0]["age_seconds"] > 2 * 86400
