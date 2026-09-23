"""Scheduled Hermes draft generation. Every X adapter call is unconditionally dry-run."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from automation_runner import api_json, Candidate, audit_candidate, run_adapter


def main():
    base='http://127.0.0.1:18880'
    token=os.environ['FACTORY_ADMIN_TOKEN']
    cfg=api_json(base,'/api/config',token=token)
    if cfg['features']['pause_all'] or cfg['features']['read_only'] or not cfg['features']['auto_post']:
        print('shadow_tick: paused')
        return
    if not all(os.environ.get(k) for k in ['MODEL_API_KEY','MODEL_BASE_URL','MODEL_ID']):
        print('shadow_tick: waiting_for_model')
        return
    if not api_json(base,'/api/rate/check',{'action':'post'},token)['ok']:
        return
    recent=api_json(base,'/api/audit?limit=50',token=token)
    texts=[row['text'] for row in recent if row['action']=='post' and row['text']]
    prompt='写一条符合人设的原创 X 草稿，仅输出正文，不要引号，不超过 180 字。不要假装真人，不要编造时事或私人经历。避免重复以下近期草稿：'+json.dumps(texts,ensure_ascii=False)
    child_env={k:v for k,v in os.environ.items() if k not in ['TELEGRAM_BOT_TOKEN','X_AUTH_TOKEN','X_CT0','FACTORY_ADMIN_TOKEN']}
    p=subprocess.run([os.environ['HERMES_PYTHON'],str(ROOT/'deployment/hermes_worker.py')],input=json.dumps({'text':prompt}),
        capture_output=True,text=True,timeout=180,env=child_env,cwd=ROOT/'.local/hermes')
    if p.returncode:
        raise RuntimeError('hermes_worker_failed')
    text=json.loads(p.stdout)['reply'].strip()
    candidate=Candidate('post','scheduled Hermes shadow draft',text=text)
    check=subprocess.run([sys.executable,str(Path(os.environ['PERSONA_PATH'])/'scripts/check_reply.py'),text],capture_output=True,text=True,timeout=20)
    if check.returncode or not json.loads(check.stdout).get('ok') or text in texts:
        audit_candidate(base,token,candidate,False,True,{'skipped':'style_safety_or_duplicate'})
        return
    # Pause is checked again after model generation. No live switch exists here.
    rate=api_json(base,'/api/rate/check',{'action':'post','increment':True},token)
    if not rate['ok']:
        audit_candidate(base,token,candidate,False,True,{'skipped':rate})
        return
    result=run_adapter(ROOT/'scripts',candidate,dry_run=True)
    audit_candidate(base,token,candidate,False,True,{'adapter':result,'runtime':'hermes'})
    api_json(base,'/api/pending',{'action':'post','text':text,'reason':'Hermes shadow review','risk':'low','persona_slug':'x-persona'},token)
    print('shadow_tick: draft audited, no X write')


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('shadow_tick_error='+type(exc).__name__)
        sys.exit(1)
