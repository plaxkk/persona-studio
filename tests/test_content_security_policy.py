"""The panel's CSP is declared twice and browsers enforce both.

The local API serves the built panel to `127.0.0.1:18880`; Vercel serves the
same panel to the hosted origin. Each sends its own `Content-Security-Policy`,
so a directive that is relaxed in one place and not the other is not "mostly
working" — the feature breaks on whichever origin was missed, which is how
attachment previews shipped broken under `img-src 'self' data:`.

That class of bug is invisible to the API tests (the bytes are stored and served
correctly) and to a quick click-through in the panel (the image element appears,
it just never decodes). Hence a test that reads both declarations.
"""
import json
from pathlib import Path

from studio.api import CONTENT_SECURITY_POLICY

ROOT = Path(__file__).resolve().parents[1]


def directives(policy: str) -> dict[str, list[str]]:
    out = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = tokens[1:]
    return out


def vercel_policy() -> str:
    config = json.loads((ROOT / "vercel.json").read_text())
    headers = [
        header
        for rule in config["headers"]
        for header in rule["headers"]
        if header["key"] == "Content-Security-Policy"
    ]
    assert len(headers) == 1, "vercel.json 里应当只有一条 CSP"
    return headers[0]["value"]


def test_both_copies_of_the_policy_are_identical():
    assert vercel_policy() == CONTENT_SECURITY_POLICY


def test_attachments_can_be_shown_but_never_executed():
    rules = directives(vercel_policy())
    # Attachments are read back in chunks and rendered from object URLs.
    assert "blob:" in rules["img-src"], "图片预览会被 CSP 拒掉"
    assert "blob:" in rules["media-src"], "视频预览会被 CSP 拒掉"
    # An uploaded file must never become a script.
    assert "blob:" not in rules["script-src"]
    assert "blob:" not in rules["default-src"]
    # The remaining lockdown stays in place.
    assert rules["frame-ancestors"] == ["'none'"]
    assert rules["form-action"] == ["'self'"]
    assert rules["base-uri"] == ["'self'"]
    assert rules["connect-src"] == ["'self'"]
