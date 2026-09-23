import asyncio
import json
import time
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from studio.store import Store
from studio.persona_imports import PersonaImports, FIELDS
from studio.codex_persona import CodexPersonaRunner, command, DISABLED
from studio.desktop import Desktop, desktop_route
from studio.api import create_app
from scripts.persona_distill import normalize_text


@pytest.fixture
def imports(tmp_path):
    value = PersonaImports(Store(tmp_path))
    value.presence("1.3.0")
    return value


def job(imports):
    ident = imports.create("example", 20, "test-model", "medium")
    with imports.store.db() as c:
        c.execute("UPDATE persona_imports SET status='collecting' WHERE id=?", (ident,))
    return ident


def evidence(imports, ident, count=20, **extra):
    action = imports.enqueue_action(ident, 0, "snapshot")
    imports.next_action()
    data = {
        "state": "ready",
        "account": "example",
        "records": [
            {
                "id": str(90071992547409930 + i),
                "author": "example",
                "kind": "reply" if i % 2 else "post",
                "text": f"中文 😀 第 {i} 条\n第二行",
                "created_at": "2026-09-23",
            }
            for i in range(count)
        ],
        **extra,
    }
    return action, imports.receive(action, ident, 0, data)


def candidate(imports, ident):
    return {
        "persona": {k: "示例 AI 人格" for k in FIELDS},
        "observations": [
            {
                "type": "observation",
                "statement": "换行短句",
                "source_ids": [imports.sources(ident)[0]["id"]],
            }
        ],
        "limitations": "模拟样本，不代表完整历史",
        "examples": ["新生成一", "新生成二", "新生成三"],
    }


def test_dedupe_pause_late_results(imports):
    ident = job(imports)
    assert imports.create("example", 20, "test-model", "medium") == ident
    action, _ = evidence(imports, ident)
    assert imports.get(ident)["counts"] == {
        "read": 20,
        "usable": 20,
        "post": 10,
        "reply": 10,
    }
    assert imports.sources(ident)[0]["id"].isdigit()
    assert "\n第二行" in imports.sources(ident)[0]["text"]
    assert imports.receive(
        action, ident, 0, {"state": "ready", "account": "example"}
    ) == {"accepted": False}
    evidence(imports, ident)
    assert imports.get(ident)["duplicates"] == 20
    a = imports.enqueue_action(ident, 0, "scroll")
    imports.next_action()
    imports.transition(ident, "pause")
    assert not imports.receive(a, ident, 0, {"state": "ready", "account": "example"})[
        "accepted"
    ]
    imports.transition(ident, "resume")
    assert imports.get(ident)["generation"] == 2


def test_identity_and_exclusions(imports):
    ident = job(imports)
    evidence(imports, ident, account="other")
    assert imports.get(ident)["status"] == "account_mismatch"
    assert imports.get(ident)["counts"]["read"] == 0
    imports.transition(ident, "cancel")
    ident = job(imports)
    evidence(
        imports,
        ident,
        records=[
            {
                "id": "90071992547409939",
                "author": "example",
                "kind": "post",
                "text": "quote",
                "translated": True,
            },
            {
                "id": "90071992547409940",
                "author": "another",
                "kind": "post",
                "text": "not me",
            },
        ],
    )
    assert imports.get(ident)["counts"]["usable"] == 0
    assert imports.get(ident)["excluded"] == {"translated": 1}


def test_preview_conflict_transaction_idempotency(imports):
    ident = job(imports)
    evidence(imports, ident)
    original = imports.store.get("persona")
    with imports.store.db() as c:
        c.execute("UPDATE persona_imports SET status='distilling' WHERE id=?", (ident,))
    result = candidate(imports, ident)
    imports.save_candidate(ident, 0, result)
    assert imports.store.get("persona") == original
    with pytest.raises(HTTPException):
        imports.apply(ident, result["persona"], 999)
    assert not imports.store.rows("SELECT * FROM memories WHERE category='style'")
    applied = imports.apply(ident, result["persona"], original["version"])
    assert applied["version"] == original["version"] + 1
    assert (
        len(imports.store.rows("SELECT * FROM memories WHERE category='style'")) == 20
    )
    assert (
        imports.apply(ident, result["persona"], original["version"])["version"]
        == applied["version"]
    )
    assert (
        len(imports.store.rows("SELECT * FROM memories WHERE category='style'")) == 20
    )
    imports.delete(ident)
    assert imports.store.get("persona")["version"] == applied["version"]


def test_fabricated_citation_rejected(imports):
    ident = job(imports)
    evidence(imports, ident)
    result = candidate(imports, ident)
    result["observations"][0]["source_ids"] = ["fake"]
    with pytest.raises(HTTPException):
        imports.save_candidate(ident, 0, result)


def test_stall_recovery_and_secret_redaction(imports):
    from studio.security import Secrets

    Secrets(imports.store.root).update({"X_AUTH_TOKEN": "never-show-this-token"})
    ident = job(imports)
    evidence(
        imports,
        ident,
        records=[
            {
                "id": "90071992547409939",
                "author": "example",
                "kind": "post",
                "text": "never-show-this-token\n换行",
            }
        ],
    )
    assert "never-show-this-token" not in json.dumps(imports.sources(ident))
    for _ in range(3):
        a = imports.enqueue_action(ident, 0, "scroll")
        imports.next_action()
        imports.receive(
            a, ident, 0, {"state": "ready", "account": "example", "records": []}
        )
    assert imports.get(ident)["status"] == "stalled"
    imports.transition(ident, "resume")
    CodexPersonaRunner(imports.store).recover()
    assert imports.get(ident)["status"] == "interrupted"


def test_paused_manual_import_and_cleanup(imports):
    assert imports.store.get("paused")
    ident = job(imports)
    evidence(imports, ident)
    imports.pause_all()
    assert imports.get(ident)["status"] == "paused"
    with imports.store.db() as c:
        c.execute(
            "UPDATE persona_imports SET created=?", (int(time.time()) - 8 * 86400,)
        )
    imports.cleanup()
    assert not imports.sources(ident)
    assert imports.get(ident)["counts"]["usable"] == 20


def test_browser_transport_not_in_page_allowlist():
    assert not desktop_route("/api/v1/persona-browser", "POST")
    assert desktop_route("/api/v1/persona-imports", "POST")
    assert not desktop_route("/api/v1/persona-imports/123/browser", "POST")


def test_no_website_evidence(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as c:
        with pytest.raises(Exception):
            with c.websocket_connect(
                "/api/v1/persona-browser",
                headers={"origin": "https://persona-studio-plaxkk.vercel.app"},
            ):
                pass


def test_distill_preserves_lines():
    assert normalize_text("中文  😀\r\n 第二行") == "中文 😀\n第二行"


def test_runner_command_contract(tmp_path, monkeypatch):
    monkeypatch.setattr("studio.codex_persona.model_metadata", lambda _: {"models": []})
    import sys

    if sys.platform != "darwin":
        with pytest.raises(ValueError):
            command(
                tmp_path,
                {"model": "test", "reasoning": "medium"},
                tmp_path / "schema",
                tmp_path / "out",
            )
        return
    args = command(
        tmp_path,
        {"model": "gpt-6-astra", "reasoning": "medium", "id": "a", "generation": 0},
        tmp_path / "schema",
        tmp_path / "out",
        tmp_path,
    )
    assert (
        "--ignore-user-config" in args
        and "--ignore-rules" in args
        and "read-only" in args
    )
    assert not any("dangerously" in x for x in args)
    assert "shell_tool" in DISABLED and "hooks" in DISABLED and "plugins" in DISABLED
    assert 'web_search="disabled"' in args


def test_backup_excludes_temporary_raw_evidence(imports):
    import sqlite3

    ident = job(imports)
    evidence(imports, ident)
    path = imports.store.backup()
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM persona_sources").fetchone()[0] == 0
        assert (
            c.execute("SELECT count(*) FROM persona_browser_actions").fetchone()[0] == 0
        )
    assert len(imports.sources(ident)) == 20


def test_rebalances_after_first_source_fills_target(imports):
    ident = job(imports)
    records = [
        {
            "id": str(90071992547409930 + i),
            "author": "example",
            "kind": "post",
            "text": f"post {i}",
        }
        for i in range(20)
    ]
    evidence(imports, ident, records=records)
    replies = [
        {
            "id": str(90071992547410030 + i),
            "author": "example",
            "kind": "reply",
            "text": f"reply {i}",
        }
        for i in range(10)
    ]
    evidence(imports, ident, records=replies)
    counts = imports.get(ident)["counts"]
    assert counts["usable"] == 20 and counts["post"] == counts["reply"] == 10
