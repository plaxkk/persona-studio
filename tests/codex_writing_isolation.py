"""Audit the real CLI's outgoing tool inventory against a local mock model only."""
import json, os, subprocess, sys, tempfile, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.codex_persona import command, environment, preflight

payload = {}
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_POST(self):
        payload.update(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(400)
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"tool audit only"}}')
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models":[]}')

server = HTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        schema = folder/'schema.json'
        schema.write_text('{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false}')
        info = preflight()
        args = command(folder, {'model': info['model'], 'reasoning': info['reasoning']}, schema, folder/'output', base_instructions='Text-only writing assistant. Return the requested text without tools.')
        args[-1:-1] = ['-c', 'model_provider="audit"', '-c', f'model_providers.audit={{name="audit",base_url="http://127.0.0.1:{server.server_port}/v1",wire_api="responses",env_key="WRITING_TEST_KEY",request_max_retries=0}}']
        env = environment()
        env['WRITING_TEST_KEY'] = 'synthetic-test-key'
        for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'):
            env.pop(key, None)
        env['NO_PROXY'] = '127.0.0.1,localhost'
        subprocess.run(args, input='Return test text.', text=True, capture_output=True, env=env, timeout=45)
        assert payload, 'No model request captured'
        names = {t.get('name', t.get('type')) for t in payload.get('tools', [])}
        assert names <= {'list_mcp_resources','list_mcp_resource_templates','read_mcp_resource','request_user_input'}, names
        assert 'gstack' not in str(payload) and 'Durable Preferences' not in str(payload)
        assert not any(k in env for k in ('OPENAI_API_KEY','X_AUTH_TOKEN','ENGINE_HERMES_API_KEY'))
        print('PASS actual Codex writing tool inventory:', sorted(names))
finally:
    server.shutdown()
