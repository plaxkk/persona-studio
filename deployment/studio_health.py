"""Read-only health diagnostic; outputs status, never credentials or private paths."""

import json, os, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.store import Store
from studio.engines import Engines

store = Store(Path(os.environ.get("STUDIO_STATE_DIR", ROOT / ".local/state")))
port = int(os.environ.get("STUDIO_PORT", 18880))
try:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/v1/health", timeout=5
    ) as r:
        service = json.load(r)
except Exception:
    service = {"service": "unavailable"}
result = {
    "service": service,
    "worker_alive": time.time() - store.get("worker_heartbeat") < 30,
    "paused": store.get("paused"),
    "engines": [
        {k: e[k] for k in ["id", "installed", "supported", "status"]}
        for e in Engines(store).list()
    ],
    "x": store.get("x_status"),
    "telegram": store.get("telegram_status"),
    "recent_sync": store.get("last_sync"),
    "sources": store.rows("SELECT name,status,last_success FROM sources"),
    "live_x_writes": False,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
sys.exit(0 if service.get("service") == "alive" and result["worker_alive"] else 1)
