from __future__ import annotations
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse
from scripts.credential_helper import load_env, write_env, extract_cookie_editor_values

_KEYS = {
    "x": ["X_AUTH_TOKEN", "X_CT0"],
    "telegram": ["TELEGRAM_BOT_TOKEN"],
    "hermes": ["ENGINE_HERMES_API_KEY"],
    "openclaw": ["ENGINE_OPENCLAW_API_KEY"],
}
_lock = threading.RLock()


class Secrets:
    def __init__(self, root):
        self.path = Path(root) / ".env"

    def all(self):
        return load_env(self.path)[1]

    def get(self, key):
        return self.all().get(key, "")

    def present(self, scope):
        return {k: bool(self.get(k)) for k in _KEYS[scope]}

    def update(self, values):
        if any(k not in sum(_KEYS.values(), []) for k in values):
            raise ValueError("unsupported_secret")
        if any("\n" in v or "\r" in v or "\x00" in v for v in values.values()):
            raise ValueError("invalid_secret")
        with _lock, (self.path.parent / "credentials.lock").open("a+") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self.all()
            current.update(values)
            current = {k: v for k, v in current.items() if v}
            fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".secret-")
            os.close(fd)
            path = Path(name)
            try:
                write_env(path, current)
                path.chmod(0o600)
                os.replace(path, self.path)
            finally:
                path.unlink(missing_ok=True)

    def delete(self, scope):
        self.update({k: "" for k in _KEYS[scope]})

    def cookies(self, payload):
        if not isinstance(payload, list) or len(payload) > 200:
            raise ValueError("invalid_cookie_export")
        selected = []
        for cookie in payload:
            if not isinstance(cookie, dict):
                continue
            domain = str(cookie.get("domain", "")).lstrip(".").lower()
            if cookie.get("name") in ["auth_token", "ct0"] and domain in [
                "x.com",
                "twitter.com",
            ]:
                selected.append(cookie)
        if {c["name"] for c in selected} != {"auth_token", "ct0"}:
            raise ValueError("missing_x_cookies")
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".cookie-import-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(selected, f)
            self.update(extract_cookie_editor_values(Path(name)))
        finally:
            Path(name).unlink(missing_ok=True)


def password_hash(password):
    salt = secrets.token_hex(16)
    return (
        salt
        + ":"
        + hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
    )


def password_check(password, stored):
    try:
        salt, expected = stored.split(":")
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def canonical_x_url(value, post=True):
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc.lower() not in [
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
    ]:
        raise ValueError("invalid_x_url")
    match = re.fullmatch(
        r"/([A-Za-z0-9_]{1,15}|i/web)/status/([0-9]{1,30})/?", parsed.path
    )
    if not match:
        raise ValueError("invalid_x_post_url")
    return f"https://x.com/{match[1]}/status/{match[2]}"


def sanitize(text, values=()):
    for value in values:
        if len(value) > 6:
            text = text.replace(value, "[已隐藏凭据]")
    text = re.sub(
        r"(?i)(auth_token|ct0|api[_-]?key|password|bot_token)\s*[=:]\s*[^\s,;]+",
        r"\1=[已隐藏凭据]",
        text,
    )
    text = re.sub(
        r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b|\bsk-[A-Za-z0-9_-]{16,}\b", "[已隐藏凭据]", text
    )
    return text
