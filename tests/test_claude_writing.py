import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from studio import claude_code
from studio.api import create_app
from studio.desktop import desktop_route
from studio.engines import ClaudeEngine, EngineError, EngineRequest, EngineResult
from studio.store import Store


@pytest.fixture
def local_claude(monkeypatch):
    info = dict(
        installed=True,
        ready=True,
        model="test-model",
        version="2.1.296 (Claude Code)",
        auth="oauth_token",
        message="test login",
    )
    monkeypatch.setattr(claude_code, "preflight", lambda: info)
    return info


def result(**fields):
    data = {"type": "result", "subtype": "success", "is_error": False,
            "terminal_reason": "completed", "permission_denials": [],
            "result": "", "usage": {"output_tokens": 5}}
    data.update(fields)
    return json.dumps(data)


def test_verify_select_and_task_snapshot(tmp_path, local_claude, monkeypatch):
    app = create_app(tmp_path, "http://testserver")
    calls = []

    async def execute(self, request, config, task_id):
        calls.append((request, config))
        return EngineResult("连接成功", "claude")

    monkeypatch.setattr(ClaudeEngine, "execute", execute)
    with TestClient(app) as client:
        response = client.post('/api/v1/auth/setup', json={'password': 'test-writing-password'})
        client.headers['x-csrf-token'] = response.json()['csrf']
        assert client.post('/api/v1/engines/claude/select').status_code == 409
        listed = [e for e in client.get('/api/v1/engines').json() if e['id'] == 'claude'][0]
        assert listed['supported'] is True and listed['installed'] is True
        assert listed['local_model'] == 'test-model'
        result_body = client.post('/api/v1/engines/claude/verify').json()
        assert client.post('/api/v1/engines/claude/verify').json() == result_body
        asyncio.run(app.state.jobs.run(app.state.jobs.claim()))
        assert calls[0][1]['auth'] == 'claude-code'
        assert calls[0][1]['model'] == 'test-model'
        assert client.post('/api/v1/engines/claude/select').status_code == 200
        assert app.state.store.get('engine') == 'claude'
        assert client.post('/api/v1/generate', json={'kind': 'chat', 'text': 'hello'}).status_code == 200
        # Switching the default never reroutes queued work to another provider.
        app.state.store.set('engine', 'hermes')
        job = app.state.jobs.claim()
        assert job['payload']['engine'] == 'claude'
        assert job['payload']['config']['auth'] == 'claude-code'
        asyncio.run(app.state.jobs.run(job))
        assert len(calls) == 2
        # The local login is not this product's to delete, and no key can be written.
        assert client.delete('/api/v1/engines/claude/credentials').status_code == 422
        assert client.put('/api/v1/engines/claude',
                          json={'model': 'x', 'base_url': 'https://example.com'}).status_code == 422


def test_login_failure_does_not_select_or_fallback(tmp_path, local_claude):
    store = Store(tmp_path)
    local_claude['ready'] = False
    with pytest.raises(EngineError, match='claude_not_ready'):
        ClaudeEngine(store).validate({'model': 'test-model', 'auth': 'claude-code'})
    assert store.get('engine') == 'hermes'


@pytest.mark.parametrize('bad', ['error', 'missing', 'denial', 'empty', 'prose'])
def test_claude_output_contract(tmp_path, local_claude, monkeypatch, bad):
    store = Store(tmp_path)
    engine = ClaudeEngine(store)
    captured = {}

    def command(schema):
        captured['schema'] = schema
        return ['synthetic-command']

    async def process(*args):
        if bad == 'missing':
            return '{"type":"system","subtype":"init"}'
        if bad == 'error':
            return result(is_error=True, result='Credit balance is too low')
        if bad == 'denial':
            return result(permission_denials=[{"tool_name": "Bash"}], structured_output={"text": "草稿正文"})
        if bad == 'empty':
            return result(structured_output={"text": "   "})
        if bad == 'prose':
            return result(result='草稿正文')
        return result(structured_output={"text": "草稿正文"})

    monkeypatch.setattr(claude_code, 'command', command)
    monkeypatch.setattr(engine, 'process', process)
    run = engine.execute(EngineRequest('post', 'test', {}), engine.local_config(), 'test')
    if bad in ('error',):
        with pytest.raises(EngineError, match='claude_failed'):
            asyncio.run(run)
    elif bad:
        with pytest.raises(EngineError, match='invalid_output'):
            asyncio.run(run)
    else:
        output = asyncio.run(run)
        assert output.text == '草稿正文' and output.engine == 'claude'
        assert output.usage['output_tokens'] == 5
        assert captured['schema']['required'] == ['text']


def test_result_json_is_accepted_when_the_schema_field_is_absent(tmp_path, local_claude, monkeypatch):
    """A release that ignores --json-schema still answers in the same shape."""
    engine = ClaudeEngine(Store(tmp_path))
    monkeypatch.setattr(claude_code, 'command', lambda schema: ['synthetic-command'])
    monkeypatch.setattr(engine, 'process',
                        lambda *args: _async(result(result=json.dumps({"text": "草稿正文"}))))
    output = asyncio.run(engine.execute(EngineRequest('post', 'test', {}),
                                        engine.local_config(), 'test'))
    assert output.text == '草稿正文'


def _async(value):
    async def inner():
        return value
    return inner()


def test_command_keeps_the_isolation_contract(local_claude, monkeypatch):
    monkeypatch.setattr(claude_code, 'executable', lambda: '/synthetic/claude')
    args = claude_code.command({"type": "object"})
    assert args[args.index('--tools') + 1] == ''
    for flag in ('--safe-mode', '--strict-mcp-config', '--no-session-persistence',
                 '--disable-slash-commands', '--json-schema', '--print'):
        assert flag in args
    # --bare skips credentials and --restricted drops this machine's own login and
    # base-url settings; either one turns every run into a plain auth failure.
    assert '--bare' not in args and '--restricted' not in args
    # The machine's own model choice is never overridden.
    assert '--model' not in args


def test_route_scope_covers_claude(tmp_path):
    assert desktop_route('/api/v1/engines/claude/verify', 'POST')
    assert desktop_route('/api/v1/engines/claude/select', 'POST')
    assert not desktop_route('/api/v1/engines/claude/credentials', 'DELETE')
    assert not desktop_route('/api/v1/engines/claude', 'PUT')


def test_preflight_reports_an_unverified_flag_set(monkeypatch):
    monkeypatch.setattr(claude_code, 'executable', lambda: '/synthetic/claude')
    monkeypatch.setattr(claude_code, 'flags_supported', lambda binary: False)
    info = claude_code.preflight()
    assert info['installed'] and not info['ready']
    assert '隔离参数' in info['message']


# 2.1.296 prints this object across several lines. Treating the "{" that opens it
# as the whole body parses nothing, and a logged-in machine then reported
# "无法核对" instead of a detected login.
def test_login_status_reads_the_pretty_printed_body():
    text = ('{\n  "loggedIn": true,\n  "authMethod": "oauth_token",\n'
            '  "configDirectory": "/Users/someone/.claude"\n}\n')
    assert claude_code.login_status(text) == {
        "loggedIn": True, "authMethod": "oauth_token",
        "configDirectory": "/Users/someone/.claude",
    }


def test_login_status_keeps_a_compact_body_and_skips_unparsable_lines():
    assert claude_code.login_status('{"loggedIn": true}') == {"loggedIn": True}
    assert claude_code.login_status('{"loggedIn": true,}\n{"loggedIn": true}')['loggedIn'] is True
    assert claude_code.login_status('not json at all') == {}


def test_preflight_reads_a_real_pretty_printed_login(monkeypatch):
    """The end-to-end shape: a logged-in CLI on this version must read as ready."""
    class Run:
        def __init__(self, stdout):
            self.stdout, self.returncode = stdout, 0

    def fake_run(args, **kwargs):
        if args[1:] == ['--help']:
            return Run(' '.join(claude_code.REQUIRED_FLAGS))
        if args[1:] == ['--version']:
            return Run('2.1.296 (Claude Code)')
        return Run('{\n  "loggedIn": true,\n  "authMethod": "oauth_token"\n}\n')

    monkeypatch.setattr(claude_code, 'executable', lambda: '/synthetic/claude')
    monkeypatch.setattr(claude_code, 'local_model', lambda: 'a-model')
    monkeypatch.setattr(claude_code.subprocess, 'run', fake_run)
    info = claude_code.preflight()
    assert info['ready'] is True, info
    assert info['auth'] == 'oauth_token' and info['model'] == 'a-model'
