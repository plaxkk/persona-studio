"""Install the current OS user's password-free, extension-bound native connector."""

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from studio.browser_identity import EXTENSION_ORIGIN
from studio.native_connector import HOST_NAME


def install(state, port=18880, *, home=None, host_name=HOST_NAME):
    home = Path(home or Path.home())
    state = Path(state).resolve()
    if not (state / "studio.sqlite3").is_file():
        raise ValueError("请先初始化本机工作室。")
    if not 1 <= port <= 65535:
        raise ValueError("端口无效。")
    target = state.parent / (host_name + ".command")
    command = [
        str(Path(sys.executable).absolute()),
        "-m",
        "studio.native_connector",
        str(state),
        str(port),
    ]
    # Preserve the venv interpreter path (do not resolve its symlink).
    target.write_text(
        "#!/bin/sh\ncd "
        + shlex.quote(str(REPO))
        + " || exit 1\nexec "
        + shlex.join(command)
        + ' "$@"\n'
    )
    target.chmod(0o700)
    manifest = {
        "name": host_name,
        "description": "Persona Studio local authorization",
        "path": str(target),
        "type": "stdio",
        "allowed_origins": [EXTENSION_ORIGIN + "/"],
    }
    if sys.platform == "darwin":
        roots = [
            home / "Library/Application Support" / browser
            for browser in [
                "Google/Chrome",
                "Google/ChromeForTesting",
                "Chromium",
                "Microsoft Edge",
            ]
        ]
    elif sys.platform.startswith("linux"):
        roots = [
            home / ".config" / browser
            for browser in [
                "google-chrome",
                "google-chrome-for-testing",
                "chromium",
                "microsoft-edge",
            ]
        ]
    else:
        raise ValueError("当前安装器支持 macOS 和 Linux。")
    paths = []
    for root in roots:
        folder = root / "NativeMessagingHosts"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (host_name + ".json")
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        path.chmod(0o600)
        paths.append(path)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=REPO / ".local/state")
    parser.add_argument("--port", type=int, default=18880)
    args = parser.parse_args()
    os.umask(0o077)
    install(args.state, args.port)
    print("本机连接程序已安装。重新加载浏览器扩展后，点击授权即可。")
