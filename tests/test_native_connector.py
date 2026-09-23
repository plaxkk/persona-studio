import io
import json
import struct
import pytest
from studio.native_connector import authorize, run
from studio.browser_identity import EXTENSION_ORIGIN
from studio.store import Store
from studio.security import digest
from deployment.install_browser_connector import install


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "state")
    store.set("password_hash", "fixture-initialized")
    return store


def test_protocol_issues_revocable_session_without_password(state):
    payload = json.dumps(
        {"type": "authorize", "local": "http://127.0.0.1:18880"}
    ).encode()
    output = io.BytesIO()
    run(
        state.root,
        18880,
        EXTENSION_ORIGIN + "/",
        io.BytesIO(struct.pack("=I", len(payload)) + payload),
        output,
    )
    raw = output.getvalue()
    assert struct.unpack("=I", raw[:4])[0] == len(raw[4:])
    result = json.loads(raw[4:])
    assert result["ok"]
    assert state.rows("SELECT token_hash FROM desktop_tokens")[0][
        "token_hash"
    ] == digest(result["token"])


@pytest.mark.parametrize(
    "origin,message",
    [
        (
            "https://evil.test/",
            {"type": "authorize", "local": "http://127.0.0.1:18880"},
        ),
        (
            EXTENSION_ORIGIN + "/",
            {"type": "authorize", "local": "http://evil.test:18880"},
        ),
        (
            EXTENSION_ORIGIN + "/",
            {"type": "authorize", "local": "http://127.0.0.1:9999"},
        ),
        (EXTENSION_ORIGIN + "/", {"type": "shell", "local": "http://127.0.0.1:18880"}),
        (
            EXTENSION_ORIGIN + "/",
            {
                "type": "authorize",
                "local": "http://127.0.0.1:18880",
                "state": "/tmp/other",
            },
        ),
    ],
)
def test_rejects_other_sources_operations_and_ports(state, origin, message):
    with pytest.raises(ValueError):
        authorize(message, origin, state.root, 18880)
    assert not state.rows("SELECT * FROM sessions")


def test_malformed_request_has_no_internal_details(state):
    for data in [b"", struct.pack("=I", 99999), struct.pack("=I", 3) + b"no"]:
        output = io.BytesIO()
        run(state.root, 18880, EXTENSION_ORIGIN + "/", io.BytesIO(data), output)
        result = json.loads(output.getvalue()[4:])
        assert not result["ok"] and "token" not in result
        assert str(state.root) not in result["message"]


def test_installer_is_extension_bound_and_private(state, tmp_path):
    paths = install(state.root, home=tmp_path / "home")
    for path in paths:
        manifest = json.loads(path.read_text())
        assert manifest["allowed_origins"] == [EXTENSION_ORIGIN + "/"]
        assert path.stat().st_mode & 0o777 == 0o600
