import json
import sqlite3
import subprocess
import time
import asyncio

from studio.inspiration import collect_work, _discussion_tail
from studio.engines import EngineRequest, prompt_for


def test_disabled_reads_nothing(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not read sources")
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert collect_work(tmp_path) == {}
    (tmp_path / "work-inspiration.json").write_text('{"enabled":false}')
    assert collect_work(tmp_path) == {}


def test_sources_are_scoped_and_grounded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.com", "commit", "--allow-empty", "-m",
                    "Fix reconnect loop"], check=True, capture_output=True)
    db = tmp_path / "codex.sqlite"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE threads(id,title,first_user_message,preview,updated_at,cwd,source DEFAULT 'vscode',rollout_path DEFAULT '')")
        c.executemany("INSERT INTO threads(id,title,first_user_message,preview,updated_at,cwd) VALUES(?,?,?,?,?,?)", [
            ("a", "Question", "api_key=secret123", "Discussed retry cost", int(time.time()), str(repo)),
            ("b", "PRIVATE OTHER PROJECT", "", "", int(time.time()), "/elsewhere"),
            ("c", "STALE", "", "", 1, str(repo)),
        ])
        c.execute("INSERT INTO threads VALUES('guard','INTERNAL GUARDIAN','','',?,?,'subagent','')", (int(time.time()),str(repo)))
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([
        dict(author="owner", kind="post", text="a" * 40),
        dict(author="other", kind="post", text="OTHER AUTHOR" * 10),
    ]))
    (tmp_path / "work-inspiration.json").write_text(json.dumps(dict(
        enabled=True, repositories=[str(repo)], codex_db=str(db),
        account="owner", style_corpus=str(corpus),
    )))
    work = collect_work(tmp_path)
    content = json.dumps(work)
    assert "Fix reconnect loop" in content
    assert "Discussed retry cost" in content
    assert "secret123" not in content
    assert "PRIVATE OTHER PROJECT" not in content and "STALE" not in content
    assert "INTERNAL GUARDIAN" not in content
    assert "OTHER AUTHOR" not in content
    assert len(work["style_examples"]) == 1
    assert any(x["type"] == "discussion_not_verified_outcome" for x in work["items"])
    assert "不能证明上线" in prompt_for(EngineRequest("post", "", {}, {"work_inspiration": work}))
    assert "原创写作规则" not in prompt_for(EngineRequest("reply", "", {}, {"work_inspiration": work}))


def test_missing_sources_do_not_break_generation(tmp_path):
    (tmp_path / "work-inspiration.json").write_text(json.dumps(dict(
        enabled=True, repositories=[str(tmp_path / "missing")],
        codex_db=str(tmp_path / "missing.db"), memory_files=["/missing"], days="invalid",
    )))
    work = collect_work(tmp_path)
    assert work["items"] == []
    assert work["source_status"]["codex"] == "unavailable"
    assert not (tmp_path / "missing.db").exists()


def test_session_excerpts_exclude_tool_output_and_outside_paths(tmp_path):
    sessions = tmp_path / 'sessions'
    sessions.mkdir()
    path = sessions / 'sample.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in [
        dict(type='response_item', payload=dict(type='message', role='user', content=[dict(text='Why is this so slow?')])),
        dict(type='event_msg', payload=dict(type='user_message', message='Why is this so slow?')),
        dict(type='response_item', payload=dict(type='function_call_output', output='PRIVATE TOOL CONTENT')),
    ]))
    assert _discussion_tail(path, tmp_path / 'state.sqlite') == ['Why is this so slow?']
    outside = tmp_path / 'outside.jsonl'
    outside.write_text(path.read_text())
    assert _discussion_tail(outside, tmp_path / 'state.sqlite') == []


def test_post_pipeline_passes_work_to_engine(tmp_path):
    from studio.store import Store
    from studio.jobs import Jobs
    from studio.engines import EngineResult

    store = Store(tmp_path)
    store.set('paused', False)
    with store.db(True) as c:
        c.execute("UPDATE engines SET status='ready'")
    (tmp_path / 'work-inspiration.json').write_text('{"enabled":true,"allowed_engines":["hermes"]}')
    jobs = Jobs(store)
    requests = []

    class Engine:
        async def execute(self, request, config, task_id):
            requests.append(request)
            return EngineResult('具体问题值得先看证据。', 'hermes')

    jobs.engines.adapters['hermes'] = Engine()
    ident = jobs.enqueue('post', {'text': '写一条工作观察'})
    asyncio.run(jobs.run(jobs.claim()))
    assert store.rows('SELECT status FROM tasks WHERE id=?', (ident,))[0]['status'] == 'succeeded'
    assert 'work_inspiration' in requests[0].context
    assert len(store.rows('SELECT * FROM drafts')) == 1
