"""Claude Code CLI helpers: local binary, login probe, isolated argv.

The writing engine uses whatever Claude Code this machine already has logged in,
so no API key is stored here. The flags below are the whole isolation contract and
each one is load-bearing, verified against claude 2.1.296:

  --safe-mode              This machine's own settings define hooks. Without this
                           they run for every draft the engine writes.
  --tools ""               No tool of any kind is offered to the model.
  --strict-mcp-config      No MCP server, including ones installed locally.
  --no-session-persistence Nothing is appended to the session history.
  --permission-prompts     A request that somehow appears fails instead of hanging.
  --disable-slash-commands No skill can take over the run.

Two flags are deliberately absent. `--bare` "skips credentials", so the local
login is never read and every run fails. `--restricted` drops the machine's own
credential and base-url settings; the CLI then reports "Not logged in" or
"Credit balance is too low" while still exiting with a well-formed JSON body,
which reads like a product bug rather than a flag mistake.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

BINARY = "claude"
REQUIRED_FLAGS = (
    "--safe-mode",
    "--tools",
    "--strict-mcp-config",
    "--no-session-persistence",
    "--permission-prompts",
    "--json-schema",
    "--output-format",
)
SETTINGS = Path.home() / ".claude/settings.json"


def executable():
    found = os.environ.get("STUDIO_CLAUDE_BIN") or shutil.which(BINARY)
    if found:
        return found
    # The service runs under launchd, whose PATH is not the login shell's.
    for root in [
        Path.home() / ".nvm/versions/node",
        Path.home() / ".local/bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]:
        for path in sorted(root.glob("*/bin/" + BINARY), reverse=True) + [root / BINARY]:
            if path.is_file():
                return str(path)
    return ""


def environment():
    # HOME is required: the CLI reads its own login and settings from there.
    return {
        k: v
        for k, v in os.environ.items()
        if k
        in (
            "HOME",
            "PATH",
            "TMPDIR",
            "LANG",
            "USER",
            "LOGNAME",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
        )
    }


def local_model():
    """The model this machine's Claude Code is pointed at, for display only.

    The engine never passes --model: the run uses the machine's own setting, so a
    machine pointed somewhere other than a Claude model writes with that model and
    the panel must say so instead of implying otherwise.
    """
    try:
        settings = json.loads(SETTINGS.read_text())
        value = settings.get("env", {}).get("ANTHROPIC_MODEL")
    except (AttributeError, OSError, ValueError):
        return ""
    return str(value or "").strip()


def flags_supported(binary):
    """Confirm the isolation flags exist before trusting any run.

    A release that renamed or dropped one of these would otherwise be trusted
    silently, so the check is on the flags themselves and not on a version number.
    """
    try:
        text = subprocess.run(
            [binary, "--help"],
            capture_output=True,
            text=True,
            timeout=20,
            env=environment(),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return all(flag in text for flag in REQUIRED_FLAGS)


def login_status(text):
    """The parsed `auth status` body, from either printed shape.

    The CLI pretty-prints this object across several lines, so scanning for a line
    that merely starts with "{" finds the opening brace alone and parses it as
    invalid JSON. Whole-output parsing comes first; the line scan stays for a
    release that prints it compactly, and skips any line it cannot parse instead
    of letting one bad line hide a real login behind "无法核对".
    """
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def preflight():
    binary = executable()
    result = {
        "installed": bool(binary),
        "ready": False,
        "model": "",
        "version": "",
        "auth": "",
        "message": "请在本机安装 Claude Code CLI。",
    }
    if not binary:
        return result
    try:
        if not flags_supported(binary):
            result["message"] = (
                "当前 Claude Code 缺少已验证的隔离参数（--safe-mode 等），已阻止启动。"
            )
            return result
        result["version"] = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            env=environment(),
        ).stdout.strip()
        auth = subprocess.run(
            [binary, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=20,
            env=environment(),
        )
        status = login_status(auth.stdout)
        if auth.returncode or not status.get("loggedIn"):
            result["message"] = "请先在本机完成 claude 登录。"
            return result
        result["model"] = local_model()
        result["auth"] = str(status.get("authMethod", ""))
        result["ready"] = True
        result["message"] = "本机登录已检测；启动时验证模型可用性。"
    except (OSError, ValueError, subprocess.SubprocessError):
        result["message"] = "无法核对本机 Claude Code 配置或登录状态。"
    return result


def command(schema):
    """argv for one writing run. The prompt arrives on stdin, never via argv."""
    return [
        executable() or BINARY,
        "--print",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema),
        "--tools",
        "",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-mode",
        "manual",
        "--permission-prompts",
        "none",
        "--disable-slash-commands",
    ]


def structured(data):
    """The draft text from one result object, or "" when the shape is wrong.

    `structured_output` is what --json-schema produces. A release that ignored the
    schema would still answer with the same JSON inside `result`, and that same
    shape is the only fallback accepted: raw prose is never a valid draft, because
    nothing would then prove the schema was honoured.
    """
    value = data.get("structured_output")
    if not isinstance(value, dict):
        try:
            value = json.loads(data.get("result") or "")
        except (TypeError, ValueError):
            value = None
    text = value.get("text") if isinstance(value, dict) else None
    return text if isinstance(text, str) and text.strip() else ""


def failed(data):
    """Whether the run reported a failure of its own, rather than a bad shape.

    A flag mistake and a broken shape need different answers: the first one is a
    call that has to be retried after a fix, the second is output this product must
    never store. An unrecognised `terminal_reason` counts as a failure only when the
    run did not also report success, so a release that adds a new value while still
    completing does not silently stop working.
    """
    if data.get("subtype") != "success" or data.get("is_error"):
        return True
    terminal = data.get("terminal_reason")
    return terminal not in (None, "", "completed")
