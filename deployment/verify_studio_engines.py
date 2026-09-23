"""Actual installed engine processes against a loopback model fixture, never real credentials."""

import asyncio, json, os, sys, tempfile, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.store import Store
from studio.security import Secrets
from studio.engines import Engines, EngineRequest

requests = []


class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        p = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append(p)
        text = "这是本地模拟模型回复。"
        content_type = "application/json"
        body = json.dumps(
            {
                "id": "fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "local-fixture",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()
        if p.get("stream"):
            chunks = [
                {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "local-fixture",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": text},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "local-fixture",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            ]
            body = (
                "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                + "data: [DONE]\n\n"
            ).encode()
            content_type = "text/event-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def run():
    with tempfile.TemporaryDirectory(prefix="persona-engine-test-") as folder:
        store = Store(Path(folder))
        store.set("paused", False)
        secrets = Secrets(store.root)
        old = Secrets(ROOT / ".local/state").all()
        for k in ["HERMES_PYTHON", "HERMES_SOURCE"]:
            if old.get(k):
                os.environ["STUDIO_" + k] = old[k]
        secrets.update(
            {
                "ENGINE_HERMES_API_KEY": "fixture-hermes-key",
                "ENGINE_OPENCLAW_API_KEY": "fixture-openclaw-key",
                "X_AUTH_TOKEN": "must-never-reach-engine",
                "TELEGRAM_BOT_TOKEN": "must-never-reach-model",
            }
        )
        engine = Engines(store)
        config = {
            "model": "local-fixture",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        }
        for name, adapter in engine.adapters.items():
            count = len(requests)
            result = await adapter.execute(
                EngineRequest("chat", "你好", store.get("persona")), config, "fixture"
            )
            assert result.text == "这是本地模拟模型回复。", (name, result)
            batch = requests[count:]
            assert batch and not any(r.get("tools") for r in batch), (
                name + " sent tools"
            )
            assert "must-never-reach" not in json.dumps(batch)
            assert "_gstack-command" not in json.dumps(batch)
            assert "coding-agent" not in json.dumps(batch)
            print(
                json.dumps(
                    {
                        "engine": name,
                        "actual_process": True,
                        "model": "local_fixture",
                        "requests": len(batch),
                        "tools_sent": False,
                        "passed": True,
                    }
                ),
                flush=True,
            )


server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    asyncio.run(run())
finally:
    server.shutdown()
