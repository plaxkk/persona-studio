"""Redacted, repeatable local health and fixture verification."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from credential_helper import load_env
from automation_runner import api_json
_, env=load_env(ROOT/'.local/state/.env')
token=env['FACTORY_ADMIN_TOKEN']
base='http://127.0.0.1:18880'
report={'health':api_json(base,'/api/health'), 'config':api_json(base,'/api/config',token=token),
        'credentials_present':{k:bool(env.get(k)) for k in ['TELEGRAM_BOT_TOKEN','OWNER_TELEGRAM_ID','MODEL_API_KEY','MODEL_ID','MODEL_BASE_URL','X_AUTH_TOKEN','X_CT0']}}
assert report['config']['features']['shadow_mode']
assert report['config']['features']['pause_all']
for action in ['post','reply','like','repost','quote','follow']:
    assert api_json(base,'/api/rate/check',{'action':action,'increment':True},token)['reason']=='pause_all'
api_json(base,'/api/personas',{'slug':'x-persona','name':'Synthetic x-persona','path':env['PERSONA_PATH'],'notes':'Temporary synthetic persona; awaiting owner corpus'},token)
api_json(base,'/api/audit',{'action':'deployment_check','reason':'loopback, auth, pause and shadow checked','risk':'low','shadow':True},token)
with urllib.request.urlopen(base) as response:
    assert response.status==200 and b'<div id="root">' in response.read()
report['web_http']=200
for service in ['admin','hermes']:
    proc=subprocess.run(['launchctl','print',f'gui/{os.getuid()}/local.x-persona.{service}'],capture_output=True,text=True)
    report[service+'_launchd_running']=proc.returncode==0 and 'state = running' in proc.stdout
report['telegram_live']='pending_credentials_and_owner_echo'
report['x_live']='disabled_pending_owner_confirmation'
path=ROOT/'.local/verification.json'
path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
