import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.api import create_app
from studio.engines import CodexEngine, EngineError, EngineRequest, EngineResult
from studio.store import Store
from studio.desktop import desktop_route
from studio.inspiration import collect_work


@pytest.fixture
def local_codex(monkeypatch):
    from studio import codex_persona
    info = dict(installed=True, ready=True, model="test-model", reasoning="medium", message="test login")
    monkeypatch.setattr(codex_persona, "preflight", lambda: info)
    return info


def test_verify_select_and_task_snapshot(tmp_path, local_codex, monkeypatch):
    app = create_app(tmp_path, "http://testserver")
    calls = []

    async def execute(self, request, config, task_id):
        calls.append((request, config))
        return EngineResult("连接成功", "codex")

    monkeypatch.setattr(CodexEngine, "execute", execute)
    with TestClient(app) as client:
        response = client.post('/api/v1/auth/setup', json={'password': 'test-writing-password'})
        client.headers['x-csrf-token'] = response.json()['csrf']
        assert client.post('/api/v1/engines/codex/select').status_code == 409
        result = client.post('/api/v1/engines/codex/verify').json()
        assert client.post('/api/v1/engines/codex/verify').json() == result
        asyncio.run(app.state.jobs.run(app.state.jobs.claim()))
        assert calls[0][1]['auth'] == 'chatgpt'
        assert calls[0][1]['model'] == 'test-model'
        assert client.post('/api/v1/engines/codex/select').status_code == 200
        assert app.state.store.get('engine') == 'codex'
        assert client.post('/api/v1/generate', json={'kind': 'chat', 'text': 'hello'}).status_code == 200
        # Switching the default never reroutes queued work to another provider.
        app.state.store.set('engine', 'hermes')
        job = app.state.jobs.claim()
        assert job['payload']['engine'] == 'codex'
        assert job['payload']['config']['model'] == 'test-model'
        asyncio.run(app.state.jobs.run(job))
        assert len(calls) == 2
        assert client.delete('/api/v1/engines/codex/credentials').status_code == 422
        assert client.put('/api/v1/engines/codex', json={'model': 'x', 'base_url': 'https://example.com'}).status_code == 422


def test_login_failure_does_not_select_or_fallback(tmp_path, local_codex):
    store = Store(tmp_path)
    local_codex['ready'] = False
    with pytest.raises(EngineError, match='codex_not_ready'):
        CodexEngine(store).validate({'model': 'test-model', 'auth': 'chatgpt'})
    assert store.get('engine') == 'hermes'


@pytest.mark.parametrize('bad', [None, 'tool', 'incomplete', 'invalid'])
def test_codex_output_contract(tmp_path, local_codex, monkeypatch, bad):
    from studio import codex_persona
    store = Store(tmp_path)
    engine = CodexEngine(store)
    captured = {}

    def command(folder, config, schema, output, **kwargs):
        captured['kwargs'] = kwargs
        captured['schema'] = json.loads(schema.read_text())
        output.write_text('not json' if bad == 'invalid' else '{"text":"草稿正文"}')
        return ['synthetic-command']

    async def process(*args):
        if bad == 'incomplete':
            return '{"type":"turn.failed"}'
        events = [dict(type='turn.completed', usage={'output_tokens': 5})]
        if bad == 'tool':
            events.append(dict(type='item.completed', item={'type': 'command_execution'}))
        return '\n'.join(json.dumps(e) for e in events)

    monkeypatch.setattr(codex_persona, 'command', command)
    monkeypatch.setattr(engine, 'process', process)
    run = engine.execute(EngineRequest('post', 'test', {}), engine.local_config(), 'test')
    if bad:
        with pytest.raises(EngineError, match='invalid_output'):
            asyncio.run(run)
    else:
        result = asyncio.run(run)
        assert result.text == '草稿正文' and result.engine == 'codex'
        assert result.usage['output_tokens'] == 5
        assert captured['schema']['required'] == ['text']
        assert 'state' not in captured['kwargs']  # no persona browser MCP


def test_codex_route_and_work_source_scope(tmp_path):
    assert desktop_route('/api/v1/engines/codex/verify', 'POST')
    assert desktop_route('/api/v1/engines/codex/select', 'POST')
    assert not desktop_route('/api/v1/engines/codex/credentials', 'DELETE')
    (tmp_path / 'work-inspiration.json').write_text('{"enabled":true,"allowed_engines":["codex"]}')
    assert collect_work(tmp_path, 'codex')
    assert collect_work(tmp_path, 'hermes') == {}
    assert collect_work(tmp_path, 'openclaw') == {}
