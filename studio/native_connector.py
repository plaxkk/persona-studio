"""Single-operation Chrome native host. Never reachable through HTTP."""

import json
import os
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .browser_identity import EXTENSION_ORIGIN
from .desktop import Desktop
from .store import Store

HOST_NAME = "ai.personastudio.connector"


def authorize(message, origin, state, port):
    if origin != EXTENSION_ORIGIN + "/":
        raise ValueError("不支持的连接助手。")
    if (
        not isinstance(message, dict)
        or set(message) != {"type", "local"}
        or message["type"] != "authorize"
    ):
        raise ValueError("不支持的操作。")
    url = urlsplit(message["local"])
    if (
        url.scheme != "http"
        or url.hostname not in {"localhost", "127.0.0.1"}
        or url.port != port
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise ValueError("地址与本机安装不匹配，请使用安装时配置的端口。")
    state = Path(state)
    database = state / "studio.sqlite3"
    for path in (state, database):
        if path.is_symlink() or not path.exists() or path.stat().st_uid != os.getuid():
            raise ValueError("请先在当前电脑初始化工作室。")
    if state.stat().st_mode & 0o077:
        raise ValueError("本机状态目录权限不正确，请重新安装连接程序。")
    store = Store(state)
    if not store.get("password_hash"):
        raise ValueError("请先完成本机工作室初始化。")
    return {"ok": True, **Desktop(store).issue()}


def run(state, port, origin, source=None, destination=None):
    source = source or sys.stdin.buffer
    destination = destination or sys.stdout.buffer
    try:
        header = source.read(4)
        if len(header) != 4:
            raise ValueError("连接请求不完整。")
        length = struct.unpack("=I", header)[0]
        if not 0 < length <= 8192:
            raise ValueError("连接请求过大。")
        payload = source.read(length)
        if len(payload) != length:
            raise ValueError("连接请求不完整。")
        result = authorize(json.loads(payload), origin, state, port)
    except Exception:
        # Never send exceptions, paths, credentials, or tracebacks into browser logs.
        result = {
            "ok": False,
            "message": "本机授权失败，请检查安装地址并完成工作室初始化。",
        }
    encoded = json.dumps(result, ensure_ascii=False).encode()
    destination.write(struct.pack("=I", len(encoded)) + encoded)
    destination.flush()


if __name__ == "__main__":
    run(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "")
