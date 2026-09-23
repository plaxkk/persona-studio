import sys, json, threading, tempfile, subprocess, os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.codex_persona import command, environment
from studio.store import Store
from studio.persona_imports import PersonaImports

payload = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models":[]}')

    def do_POST(self):
        payload.update(
            json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        )
        self.send_response(400)
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"tool audit only"}}')


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp)
    schema = p / "schema.json"
    schema.write_text('{"type":"object","properties":{},"additionalProperties":false}')
    store = Store(p / "state")
    imp = PersonaImports(store)
    imp.presence("1.3.0")
    ident = imp.create("example", 20, "gpt-6-astra", "medium")
    args = command(
        p,
        {"model": "gpt-6-astra", "reasoning": "medium", "id": ident, "generation": 0},
        schema,
        p / "output",
        store.root,
    )
    args[-1:-1] = [
        "-c",
        'model_provider="audit"',
        "-c",
        f'model_providers.audit={{name="audit",base_url="http://127.0.0.1:{server.server_port}/v1",wire_api="responses",env_key="PERSONA_TEST_KEY",request_max_retries=0}}',
    ]
    env = environment()
    env["PERSONA_TEST_KEY"] = "synthetic-test-key"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env.pop(key, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    r = subprocess.run(
        args,
        input="Return empty JSON. Test only.",
        text=True,
        capture_output=True,
        env=env,
        timeout=45,
    )
    allowed = {
        "list_mcp_resources",
        "list_mcp_resource_templates",
        "read_mcp_resource",
        "request_user_input",
        "mcp__persona",
    }
    actual = {t.get("name") for t in payload.get("tools", [])}
    assert actual and actual <= allowed, actual
    namespace = next(t for t in payload["tools"] if t.get("name") == "mcp__persona")
    assert {t["name"] for t in namespace["tools"]} == {"browse", "finish", "progress"}
    for needle in ["gstack", "kk/.agents", "Durable Preferences"]:
        for message in payload.get("input", []):
            body = str(message)
            if needle in body:
                at = body.index(needle)
                print("unexpected context", needle, body[max(0, at - 120) : at + 180])
    assert not any(
        x in str(payload) for x in ["gstack", "kk/.agents", "Durable Preferences"]
    )
    print(
        "PASS actual Codex tool inventory: only task-bound browser MCP and inert protocol utilities; no shell, image/file tool, other MCP or agent tools"
    )


server.shutdown()
