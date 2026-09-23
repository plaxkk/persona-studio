import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'deployment')]
import pytest
from fastapi.testclient import TestClient
from hermes_bridge import owner_allowed

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('FACTORY_ADMIN_TOKEN', 'test-only-token')
    monkeypatch.setenv('FACTORY_LIVE_ENABLED', '0')
    import admin_server
    app = importlib.reload(admin_server)
    return TestClient(app.make_app(), headers={'Authorization': 'Bearer test-only-token'})

@pytest.mark.parametrize('chat,sender,kind,owner,expected', [
    ('123','123','private','123',True), ('123','999','private','123',False),
    ('999','999','private','123',False), ('123','123','group','123',False),
    ('123','123','private','',False)])
def test_owner_boundary(chat, sender, kind, owner, expected):
    assert owner_allowed({'chat':{'id':chat,'type':kind},'from':{'id':sender}},owner) is expected

def test_auth_live_lock_and_pause(client):
    assert client.get('/api/config', headers={'Authorization':''}).status_code == 401
    assert client.post('/api/config/feature', json={'key':'shadow_mode','enabled':False}).status_code == 409
    client.post('/api/config/feature', json={'key':'pause_all','enabled':True})
    for action in ['post','reply','like','repost','quote','follow']:
        assert client.post('/api/rate/check', json={'action':action,'increment':True}).json()['reason']=='pause_all'

def test_rate_audit_memory(client):
    client.post('/api/config/limit', json={'key':'daily_posts','value':1})
    assert client.post('/api/rate/check',json={'action':'post','increment':True}).json()['ok']
    assert not client.post('/api/rate/check',json={'action':'post','increment':True}).json()['ok']
    client.post('/api/audit',json={'action':'post','shadow':True,'sent':False,'reason':'deployment_test'})
    row=client.get('/api/audit').json()[0]
    assert row['shadow'] and not row['sent']
    client.post('/api/memory',json={'category':'preference','content':'likes concise replies'})
    assert 'concise' in client.post('/api/memory/digest',json={'query':'concise'}).json()['digest']

def test_read_only(client):
    client.post('/api/config/feature',json={'key':'read_only','enabled':True})
    assert client.post('/api/rate/check',json={'action':'post'}).json()['reason']=='read_only'

@pytest.mark.parametrize('action,args',[('post',['--text','fixture']),('reply',['--tweet-id','123','--text','fixture']),
    ('like',['--tweet-id','123']),('repost',['--tweet-id','123']),('quote',['--tweet-id','123','--text','fixture','--screen-name','example']),('follow',['--user','example'])])
def test_adapter_dry_run(action,args):
    env={k:v for k,v in os.environ.items() if k not in ('X_AUTH_TOKEN','X_CT0')}
    result=subprocess.run([sys.executable,str(ROOT/'scripts/x_adapter.py'),action,*args,'--dry-run'],capture_output=True,text=True,env=env,check=True)
    assert json.loads(result.stdout)['dry_run']

def test_prompt_injection():
    result=subprocess.run([sys.executable,str(ROOT/'scripts/x_signal.py'),'--mode','interaction','--username','bot_example','--input',str(ROOT/'fixtures/prompt-injection-reply.json')],capture_output=True,text=True,check=True)
    assert '"prompt_injection": true' in result.stdout and '"skip_tool_actions": true' in result.stdout

def test_browse_shadow_pipeline(client, monkeypatch, capsys):
    import automation_runner as runner
    def local_api(base,path,payload=None,token=''):
        response=client.get(path) if payload is None else client.post(path,json=payload)
        response.raise_for_status()
        return response.json()
    monkeypatch.setattr(runner,'api_json',local_api)
    monkeypatch.setattr(sys,'argv',['runner','--kind','browse','--dry-run','--browse-input',
        str(ROOT/'fixtures/browse-high-signal-unfollowed.json'),'--max-browse-items','1','--max-browse-follows','1'])
    assert runner.main()==0
    output=json.loads(capsys.readouterr().out)
    assert output['results']
    rows=client.get('/api/audit').json()
    assert len(rows)==len(output['results'])
    assert all(row['shadow'] and not row['sent'] for row in rows)
    assert any(row['action']=='follow' for row in rows)

def test_shadow_tick_generates_pending_without_live(client,monkeypatch):
    import shadow_tick as tick
    def api(base,path,payload=None,token=''):
        response=client.get(path) if payload is None else client.post(path,json=payload)
        response.raise_for_status()
        return response.json()
    monkeypatch.setattr(tick,'api_json',api)
    import automation_runner
    monkeypatch.setattr(automation_runner,'api_json',api)
    for key in ['FACTORY_ADMIN_TOKEN','MODEL_API_KEY','MODEL_BASE_URL','MODEL_ID','HERMES_PYTHON']:
        monkeypatch.setenv(key,'local-test')
    monkeypatch.setenv('PERSONA_PATH',str(ROOT/'.local/hermes/skills/x-persona'))
    original=subprocess.run
    def run(cmd,**kwargs):
        if any(str(x).endswith('hermes_worker.py') for x in cmd):
            return subprocess.CompletedProcess(cmd,0,json.dumps({'reply':'有些界面少一个按钮，反而更容易用。'}),'')
        return original(cmd,**kwargs)
    monkeypatch.setattr(tick.subprocess,'run',run)
    tick.main()
    rows=client.get('/api/audit').json()
    assert rows[0]['shadow'] and not rows[0]['sent']
    assert rows[0]['metadata']['adapter']['dry_run']
    assert client.get('/api/pending').json()
