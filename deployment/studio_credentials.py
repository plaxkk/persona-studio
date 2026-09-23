"""Interactive terminal companion; never pass secrets in arguments or print them."""

import getpass, json, os, sys, secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.store import Store
from studio.security import Secrets

os.umask(0o077)
store = Store(Path(os.environ.get("STUDIO_STATE_DIR", ROOT / ".local/state")))
vault = Secrets(store.root)
print("人格工作室安全凭据助手。留空不修改；也可直接使用 Web 设置页。")
for key, label in [
    ("X_AUTH_TOKEN", "X auth_token"),
    ("X_CT0", "X ct0"),
    ("ENGINE_HERMES_API_KEY", "Hermes 模型 API Key"),
    ("ENGINE_OPENCLAW_API_KEY", "OpenClaw 模型 API Key"),
    ("TELEGRAM_BOT_TOKEN", "Telegram Bot Token（可选）"),
]:
    value = getpass.getpass(label + "：").strip()
    if value:
        vault.update({key: value})
        if key.startswith("X_"):
            store.set("x_status", "not_verified")
            store.set("x_revision", secrets.token_hex(8))
        if key.startswith("ENGINE_"):
            engine = "hermes" if "HERMES" in key else "openclaw"
            with store.db() as c:
                row = c.execute(
                    "SELECT config FROM engines WHERE id=?", (engine,)
                ).fetchone()
                config = json.loads(row[0])
                config["revision"] = secrets.token_hex(8)
                c.execute(
                    "UPDATE engines SET status='not_verified',checked=0,config=? WHERE id=?",
                    (json.dumps(config), engine),
                )
        if key == "TELEGRAM_BOT_TOKEN":
            store.set("telegram_status", "not_verified")
store.set("paused", True)
print(
    "已安全保存，权限 0600。请在 Web 设置用户名、模型和 owner ID 并验证连接；同步保持暂停。"
)
