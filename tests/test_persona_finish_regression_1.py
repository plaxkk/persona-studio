"""Regression: ISSUE-001 — zero reads must not finish collection.
Found by /qa on 2026-09-24.
Report: .gstack/qa-reports/qa-report-persona-studio-2026-09-24.md
"""
import pytest
from studio.store import Store
from studio.persona_imports import PersonaImports
from studio.persona_mcp import dispatch

@pytest.fixture
def task(tmp_path):
    imports = PersonaImports(Store(tmp_path))
    imports.presence('1.3.0')
    ident = imports.create('example', 20, 'fixture', 'medium')
    with imports.store.db() as c:
        c.execute("UPDATE persona_imports SET status='collecting' WHERE id=?", (ident,))
    return imports, ident

def read(task, kind, records=()):
    imports, ident = task
    action = imports.enqueue_action(ident, 0, kind)
    imports.next_action()
    imports.receive(action, ident, 0, {'state':'ready','account':'example','records':list(records)})

def test_finish_without_reads_keeps_collecting(task):
    imports, ident = task
    assert dispatch(imports, ident, 0, 'finish', {})['finished'] is False
    assert imports.get(ident)['phase'] == 'collect'
    assert imports.get(ident)['status'] == 'collecting'

def test_pending_read_is_not_evidence(task):
    imports, ident = task
    imports.enqueue_action(ident, 0, 'snapshot')
    assert dispatch(imports, ident, 0, 'finish', {})['finished'] is False

def test_empty_snapshot_requires_both_sources(task):
    imports, ident = task
    read(task, 'snapshot')
    assert not dispatch(imports, ident, 0, 'finish', {})['finished']
    read(task, 'posts')
    assert not dispatch(imports, ident, 0, 'finish', {})['finished']
    read(task, 'replies')
    assert dispatch(imports, ident, 0, 'finish', {})['finished']
    assert imports.get(ident)['phase'] == 'analyze'

def test_verified_target_allows_finish(task):
    imports, ident = task
    read(task, 'snapshot', [{'id':str(90071992547409930+i),'author':'example','kind':'reply' if i%2 else 'post','text':f'中文 {i}'} for i in range(20)])
    assert dispatch(imports, ident, 0, 'finish', {})['finished']

def test_cancelled_task_rejects_finish(task):
    imports, ident = task
    read(task, 'posts'); read(task, 'replies')
    imports.transition(ident, 'cancel')
    with pytest.raises(ValueError): dispatch(imports, ident, 0, 'finish', {})
