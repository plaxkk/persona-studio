"""The connector's version gate must survive a version bump.

The gate used to match the literal "1.3." and then compare patch numbers. The
day the extension reported 1.4.0 the patch part read as 0, so every browser read
would have gone offline at once -- no error, just `browser_offline` everywhere.
"""
import time

import pytest
from fastapi import HTTPException

from studio.browser_identity import BASELINE, extension_at_least, extension_version
from studio.browser_reads import BrowserReads
from studio.persona_imports import PersonaImports
from studio.store import Store


def test_a_later_version_satisfies_an_earlier_floor():
    assert extension_at_least("1.4.0", (1, 3, 7))
    assert extension_at_least("1.9.2", (1, 3, 7))
    assert extension_at_least("2.0.0", (1, 3, 7))
    # A shorter report is padded, not rejected.
    assert extension_at_least("1.4", (1, 3, 7))


def test_an_older_or_unreadable_version_does_not():
    assert not extension_at_least("1.3.6", (1, 3, 7))
    assert not extension_at_least("1.2.99", (1, 3, 7))
    assert not extension_at_least("", (1, 3, 0))
    assert not extension_at_least(None, (1, 3, 0))
    # A version we never shipped is refused rather than guessed at.
    assert not extension_at_least("1.4.0-beta", (1, 3, 0))


def test_the_parser_keeps_every_component():
    assert extension_version("1.4.10") == (1, 4, 10)
    assert extension_version("not a version") is None


def _presence(store, version, seen=None):
    with store.db(True) as c:
        c.execute("INSERT OR REPLACE INTO persona_browser_presence VALUES(1,?,?)",
                  (version, seen if seen is not None else int(time.time())))


def test_both_readers_accept_a_1_4_extension(tmp_path):
    store = Store(tmp_path)
    _presence(store, "1.4.0")
    assert BrowserReads(store).connected()
    assert PersonaImports(store).connected()
    # The same gate is behind the pairing path, which rejects older builds.
    PersonaImports(store).presence("1.4.0")


def test_the_baseline_still_excludes_the_old_connector(tmp_path):
    store = Store(tmp_path)
    _presence(store, "1.2.9")
    assert not BrowserReads(store).connected()
    assert not PersonaImports(store).connected()
    with pytest.raises(HTTPException):
        PersonaImports(store).presence("1.2.9")
    assert BASELINE == (1, 3, 0)


def test_a_stale_heartbeat_is_still_offline_whatever_the_version(tmp_path):
    store = Store(tmp_path)
    _presence(store, "1.4.0", seen=int(time.time()) - 60)
    assert not BrowserReads(store).connected()
    assert not PersonaImports(store).connected()
