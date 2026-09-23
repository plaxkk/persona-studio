"""Run real Hermes against a local fake model, with no external credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from credential_helper import load_env
_, configured=load_env(ROOT/'.local/state/.env')
requests=[]
class Model(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(payload)
        body=json.dumps({'id':'local-test','object':'chat.completion','created':1,'model':'local-fixture',
            'choices':[{'index':0,'message':{'role':'assistant','content':'这是本地模拟模型回复。'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}).encode()
        content_type='application/json'
        if payload.get('stream'):
            chunk={'id':'local-test','object':'chat.completion.chunk','created':1,'model':'local-fixture',
                   'choices':[{'index':0,'delta':{'role':'assistant','content':'这是本地模拟模型回复。'},'finish_reason':None}]}
            end={'id':'local-test','object':'chat.completion.chunk','created':1,'model':'local-fixture',
                 'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}
            body=('data: '+json.dumps(chunk)+'\n\ndata: '+json.dumps(end)+'\n\ndata: [DONE]\n\n').encode()
            content_type='text/event-stream'
        self.send_response(200);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
server=ThreadingHTTPServer(('127.0.0.1',0),Model)
threading.Thread(target=server.serve_forever,daemon=True).start()
env={'PATH':os.environ['PATH'],'HERMES_HOME':str(ROOT/'.local/hermes-test'),
     'PYTHONPATH':configured['HERMES_SOURCE'],'MODEL_API_KEY':'local-test-not-a-real-key',
     'MODEL_BASE_URL':f'http://127.0.0.1:{server.server_port}/v1', 'MODEL_ID':'local-fixture',
     'PERSONA_PATH':configured['PERSONA_PATH'],'PYTHONIOENCODING':'utf-8'}
Path(env['HERMES_HOME']).mkdir(exist_ok=True)
try:
    p=subprocess.run([configured['HERMES_PYTHON'],str(ROOT/'deployment/hermes_worker.py')],
        input=json.dumps({'text':'你好'}),text=True,capture_output=True,env=env,timeout=90,cwd=env['HERMES_HOME'])
    result=json.loads(p.stdout) if p.stdout.strip() else {'error':'empty_stdout'}
    print(json.dumps({'returncode':p.returncode,'result':result,'model_requests':len(requests),'tools_sent':any(r.get('tools') for r in requests)},ensure_ascii=False))
    assert p.returncode==0 and result.get('reply')=='这是本地模拟模型回复。'
    assert requests and not any(r.get('tools') for r in requests)
finally:
    server.shutdown()
