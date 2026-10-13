import asyncio
import time
from types import SimpleNamespace

import pytest

from studio.jobs import Jobs
from studio.browser_reads import BrowserReads
from studio.store import Store
from studio.sync_status import SOURCES, summary
from studio.xreader import ReadError, published_time


def test_failed_round_replaces_success_status_without_erasing_data(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.set('paused', False)
    store.set('x_status', 'ready')
    before = int(time.time()) - 7200
    with store.db() as c:
        c.execute("INSERT INTO sources VALUES('own','ok',?,?,18)", (before, before))
    class BrokenReader:
        def __init__(self, secrets): pass
        async def sync(self, username): raise ReadError('client_incompatible')
        async def close(self): pass
    monkeypatch.setattr('studio.jobs.XReader', BrokenReader)
    jobs = Jobs(store)
    ident = jobs.enqueue('sync')
    asyncio.run(jobs.run(jobs.claim()))
    state = summary(store)
    assert state['stale']
    assert state['latest']['status'] == 'failed'
    assert len(state['sources']) == 6
    own = next(s for s in state['sources'] if s['name'] == 'own')
    assert own['last_success'] == before
    assert own['last_attempt'] > before
    assert own['status'] == 'client_incompatible'
    assert own['count'] == 0
    assert store.rows('SELECT id FROM tasks')[0]['id'] == ident


def test_network_retry_is_bounded_and_recovers_after_restart(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.set('paused', False)
    store.set('x_status', 'ready')
    class Offline:
        def __init__(self, secrets): pass
        async def sync(self, username): raise ReadError('network_error')
        async def close(self): pass
    monkeypatch.setattr('studio.jobs.XReader', Offline)
    jobs = Jobs(store)
    ident = jobs.enqueue('sync')
    for attempt in range(3):
        with store.db() as c:
            c.execute('UPDATE tasks SET available=0 WHERE id=?', (ident,))
        asyncio.run(jobs.run(jobs.claim()))
        row = store.rows('SELECT * FROM tasks')[0]
        assert row['attempts'] == attempt + 1
        assert row['status'] == ('failed' if attempt == 2 else 'queued')
    with store.db() as c:
        c.execute("UPDATE tasks SET status='running' WHERE id=?", (ident,))
    jobs.recover()
    assert store.rows('SELECT status FROM tasks')[0]['status'] == 'queued'


def test_previously_connected_browser_reports_offline_without_legacy_fallback(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.set('paused', False)
    store.set('x_status', 'ready')
    store.set('x_username', 'kkplayit')
    store.set('browser_reads_preferred', True)

    class LegacyReader:
        def __init__(self, secrets): pass
        async def sync(self, username):
            raise AssertionError('legacy reader must not run after browser migration')
        async def close(self): pass

    monkeypatch.setattr('studio.jobs.XReader', LegacyReader)
    jobs = Jobs(store)
    ident = jobs.enqueue('sync')
    asyncio.run(jobs.run(jobs.claim()))
    task = store.rows('SELECT status,error FROM tasks WHERE id=?', (ident,))[0]
    assert task == {'status': 'failed', 'error': 'browser_offline'}
    state = summary(store)
    assert all(source['status'] == 'browser_offline' for source in state['sources'])


def test_browser_read_keeps_partial_results_and_deduplicates_posts(tmp_path):
    store = Store(tmp_path)
    store.set('paused', False)
    store.set('x_status', 'ready')
    store.set('x_username', 'kkplayit')
    with store.db() as c:
        c.execute("INSERT INTO persona_browser_presence VALUES(1,'1.3.5',?)", (int(time.time()),))
    jobs = Jobs(store)
    ident = jobs.enqueue('sync')
    job = jobs.claim()
    assert job['id'] == ident
    reads = BrowserReads(store)

    async def scenario():
        pending = asyncio.create_task(reads.sync(job, lambda _: True))
        for _ in range(100):
            if len(store.rows('SELECT id FROM browser_reads')) == len(SOURCES):
                break
            await asyncio.sleep(0.01)
        assert len(store.rows('SELECT id FROM browser_reads')) == len(SOURCES)
        quote_id = None
        for source in SOURCES:
            action = reads.next_action()
            assert action['source'] == source
            assert set(action) == {'id', 'source', 'account', 'kind', 'transport'}
            if source == 'quotes':
                quote_id = action['id']
                reads.receive(quote_id, {'state': 'source_error'})
                continue
            records = []
            if source == 'own':
                records = [{'id': '123', 'author': 'kkplayit', 'text': 'My post',
                            'created_at': '2026-10-13T01:00:00.000Z'}]
            if source in ('mentions', 'notifications'):
                records = [{'id': '456', 'author': 'alice', 'text': 'Hi @kkplayit',
                            'created_at': '2026-10-13T02:00:00.000Z'}]
            reads.receive(action['id'], {'state': 'ready', 'account': 'kkplayit', 'records': records})
        for _ in range(2):
            with store.db() as c:
                c.execute('UPDATE browser_reads SET updated=0 WHERE id=?', (quote_id,))
            action = reads.next_action()
            assert action['source'] == 'quotes'
            reads.receive(quote_id, {'state': 'source_error'})
        return await pending

    result = asyncio.run(scenario())
    assert not result['complete']
    assert result['sources']['quotes']['status'] == 'source_error'
    assert result['sources']['own']['status'] == 'ok'
    assert {post['id'] for post in result['posts']} == {'123', '456'}
    assert next(post for post in result['posts'] if post['id'] == '456')['context']['published_at'] == 1791856800


def test_published_time_is_not_collection_time():
    assert published_time(SimpleNamespace()) is None
    assert published_time({'created_at': 'bad'}) is None
    assert published_time({'created_at': 'Tue Oct 13 01:00:00 +0000 2026'}) == 1791853200


def test_stale_uses_configured_interval(tmp_path):
    store = Store(tmp_path)
    store.set('sync_interval', 60)
    with store.db() as c:
        for name in SOURCES:
            c.execute("INSERT INTO sources VALUES(?,'ok',1000,1000,0)", (name,))
    assert not summary(store, 1120)['stale']
    assert summary(store, 1121)['stale']


def test_missing_source_is_explicitly_stale(tmp_path):
    store = Store(tmp_path)
    state = summary(store)
    assert state['stale']
    assert len(state['sources']) == len(SOURCES)
    assert all(source['status'] == 'not_attempted' for source in state['sources'])
