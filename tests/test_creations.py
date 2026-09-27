import asyncio
import pytest
from fastapi.testclient import TestClient
from studio.api import create_app
from studio.engines import EngineResult, EngineError
from studio.desktop import desktop_route


@pytest.fixture
def workspace(tmp_path):
    app = create_app(tmp_path, 'http://testserver')
    with TestClient(app) as client:
        result = client.post('/api/v1/auth/setup', json={'password': 'testing-password-123'})
        client.headers['x-csrf-token'] = result.json()['csrf']
        store, jobs = app.state.store, app.state.jobs
        with store.db() as c:
            c.execute("UPDATE engines SET status='ready' WHERE id='hermes'")
        class Engine:
            requests = []
            failure = False
            async def execute(self, request, config, task_id):
                self.requests.append(request)
                if self.failure:
                    raise EngineError('engine_failed')
                return EngineResult('候选正文' if '只输出推文正文' in request.text else '三个角度，请选择一个。', 'hermes')
        engine = Engine()
        jobs.engines.adapters['hermes'] = engine
        yield client, store, jobs, engine


def test_capture_conversation_revision_finalize_and_reopen(workspace):
    c, store, jobs, engine = workspace
    ident = c.post('/api/v1/creations', json={'idea':'今天做了一个提醒工具'}).json()['id']
    path = '/api/v1/creations/' + ident
    assert c.get(path).json()['stage'] == 'idea'
    assert not store.rows('SELECT * FROM tasks')
    # Manual creative discussion works while scheduled generation is paused.
    turn = c.post(path+'/turn', json={'version':1,'text':'帮我找角度'})
    assert turn.status_code == 200
    assert c.post(path+'/turn', json={'version':1,'text':'重复请求'}).status_code == 409
    assert c.put(path, json={'version':1,'idea':'抢写'}).status_code == 409
    asyncio.run(jobs.run(jobs.claim()))
    record = c.get(path).json()
    assert len(record['messages']) == 2 and record['version'] == 2
    assert c.get('/api/v1/messages').json() == []  # no persona chat contamination
    c.post(path+'/turn', json={'version':2,'text':'选可靠性角度，写一版','mode':'draft'})
    asyncio.run(jobs.run(jobs.claim()))
    assert engine.requests[-1].history[-1]['text'] == '三个角度，请选择一个。'
    assert engine.requests[-1].context['writing_workspace']['original_idea'] == '今天做了一个提醒工具'
    record = c.get(path).json()
    assert record['candidate'] == '候选正文'
    assert not store.rows('SELECT * FROM drafts')
    assert c.put(path, json={'version':1,'idea':'过期修改'}).status_code == 409
    updated = c.put(path, json={'version':3,'idea':record['idea'],'candidate':'我确认的正文'}).json()
    final = c.post(path+'/finalize', json={'version':updated['version']}).json()['draft_id']
    assert c.post(path+'/finalize', json={'version':updated['version']}).json()['draft_id'] == final
    assert c.post(path+'/turn', json={'version':updated['version'],'text':'覆盖已定稿'}).status_code == 409
    assert store.rows('SELECT text FROM drafts WHERE id=?',(final,))[0]['text'] == '我确认的正文'
    reopened = c.get(path).json()
    assert reopened['stage'] == 'final' and len(reopened['messages']) == 4
    assert len(store.rows('SELECT * FROM drafts')) == 1


def test_failure_cancel_isolation_and_empty_validation(workspace):
    c, store, jobs, engine = workspace
    a = c.post('/api/v1/creations', json={'idea':'第一个想法'}).json()['id']
    b = c.post('/api/v1/creations', json={'idea':'第二个想法'}).json()['id']
    assert c.post('/api/v1/creations', json={'idea':'  '}).status_code == 422
    path = '/api/v1/creations/'+a
    assert c.post(path+'/finalize', json={'version':1}).status_code == 422
    c.post(path+'/turn', json={'version':1,'text':'第一次失败'})
    engine.failure = True
    asyncio.run(jobs.run(jobs.claim()))
    record = c.get(path).json()
    assert record['task']['status'] == 'failed' and record['task']['text'] == '第一次失败'
    assert record['version'] == 1 and record['messages'] == []
    engine.failure = False
    c.post(path+'/turn', json={'version':1,'text':'重试'})
    asyncio.run(jobs.run(jobs.claim()))
    c.post('/api/v1/creations/'+b+'/turn', json={'version':1,'text':'全新对话'})
    asyncio.run(jobs.run(jobs.claim()))
    assert engine.requests[-1].history == []
    task = c.post(path+'/turn', json={'version':2,'text':'取消这轮'}).json()['task_id']
    c.post('/api/v1/tasks/'+task+'/cancel', json={})
    assert c.get(path).json()['task']['status'] == 'cancelled'
    assert c.put(path, json={'version':2,'idea':'可以继续改','candidate':'手写'}).status_code == 200


def test_desktop_creation_routes_are_bounded():
    ident = 'a'*32
    for method, path in [('GET','/creations'),('POST','/creations'),('GET','/creations/'+ident),('PUT','/creations/'+ident),('POST','/creations/'+ident+'/turn'),('POST','/creations/'+ident+'/finalize')]:
        assert desktop_route('/api/v1'+path, method)
    assert not desktop_route('/api/v1/creations/'+ident+'/delete', 'POST')


def test_manual_writing_stage_and_latest_final_preview(workspace):
    c, store, jobs, engine = workspace
    ident = c.post('/api/v1/creations', json={'idea':'手写灵感'}).json()['id']
    path = '/api/v1/creations/' + ident
    record = c.put(path, json={'version':1,'idea':'手写灵感','candidate':'手写正文'}).json()
    assert record['stage'] == 'writing'
    assert not engine.requests
    draft_id = c.post(path+'/finalize', json={'version':record['version']}).json()['draft_id']
    draft = c.get('/api/v1/drafts').json()[0]
    assert draft['creation_id'] == ident
    c.put('/api/v1/drafts/'+draft_id, json={'text':'内容库最新正文','version':draft['version']})
    assert c.get(path).json()['final_text'] == '内容库最新正文'
