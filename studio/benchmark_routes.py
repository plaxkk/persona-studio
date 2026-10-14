"""Benchmark accounts and their viral posts (06B-1), and 爆款拆解 (06B-2).

Every route takes its input from the path, never a query string: the connector
refuses query strings outside a short allowlist (`browser-extension/desktop.js`),
so a `?handle=` route would 403 through the cloud panel.
"""
import re

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from . import benchmarks, deconstruct

HANDLE = re.compile(r'[A-Za-z0-9_]{1,15}')
POST_ID = re.compile(r'[0-9]{1,25}')


class Account(BaseModel):
    handle: str = Field(min_length=1, max_length=16)
    label: str = Field(default='', max_length=80)
    note: str = Field(default='', max_length=200)


class Deconstruct(BaseModel):
    # Empty means "use the engine configured in settings", the same default the
    # other generation kinds fall back to.
    engine: str = Field(default='', max_length=32)


def register(app, store, jobs, session):
    def known(handle: str) -> str:
        clean = handle.strip().lstrip('@').lower()
        if not HANDLE.fullmatch(clean):
            raise HTTPException(422, '用户名格式不正确。')
        return clean

    @app.get('/api/v1/benchmarks/accounts', dependencies=[Depends(session)])
    def listing():
        rows = []
        for account in benchmarks.accounts(store, active_only=False):
            posts = benchmarks.posts(store, account['handle'])
            viral = [post for post in posts if post['is_viral']]
            # "没有数据" and "读了但一条都没达标" are different states, and the
            # panel must be able to say which one it is showing.
            captured = [post['captured_at'] for post in posts if post['captured_at']]
            rows.append({**account,
                         'posts': len(posts),
                         'viral': len(viral),
                         'last_captured': max(captured) if captured else None,
                         'coverage_gap': next(
                             (post['coverage_gap'] for post in posts
                              if post['coverage_gap']), '')})
        return {'accounts': rows, 'min_faves': benchmarks.MIN_FAVES}

    @app.post('/api/v1/benchmarks/accounts', dependencies=[Depends(session)])
    def add(body: Account):
        handle = known(body.handle)
        benchmarks.add_account(store, handle, label=body.label.strip(),
                               note=body.note.strip())
        return {'handle': handle}

    @app.get('/api/v1/benchmarks/posts/{handle}', dependencies=[Depends(session)])
    def posts(handle: str):
        clean = known(handle)
        if not any(row['handle'] == clean
                   for row in benchmarks.accounts(store, active_only=False)):
            raise HTTPException(404, '这个账号还没有对读过。')
        rows = benchmarks.posts(store, clean)
        return {'handle': clean, 'min_faves': benchmarks.MIN_FAVES,
                'posts': rows}

    @app.post('/api/v1/benchmarks/read/{handle}', dependencies=[Depends(session)])
    def read(handle: str):
        clean = known(handle)
        if not any(row['handle'] == clean
                   for row in benchmarks.accounts(store, active_only=False)):
            raise HTTPException(404, '先把账号加进对标列表，再读它的推文。')
        # One read per account at a time; a second request joins the first.
        task = jobs.enqueue('benchmark', {'handle': clean},
                            dedupe='benchmark:' + clean)
        return {'task_id': task, 'handle': clean,
                'query': benchmarks.current_query(store, clean)}

    @app.get('/api/v1/benchmarks/deconstructions/{handle}',
             dependencies=[Depends(session)])
    def deconstructions(handle: str):
        clean = known(handle)
        return {'handle': clean, 'deconstructions': deconstruct.list_for(store, clean)}

    @app.get('/api/v1/benchmarks/deconstruction/{post_id}',
             dependencies=[Depends(session)])
    def deconstruction(post_id: str):
        # The document itself, so the panel does not have to ship every stored
        # document just to show one of them.
        if not POST_ID.fullmatch(post_id):
            raise HTTPException(422, '推文 ID 格式不正确。')
        rows = store.rows(
            'SELECT design_doc FROM viral_deconstructions WHERE post_id=?',
            (post_id,))
        if not rows:
            raise HTTPException(404, '这条推文还没有拆解过。')
        return {'post_id': post_id, 'doc': rows[0]['design_doc']}

    @app.post('/api/v1/benchmarks/deconstruct/{post_id}',
              dependencies=[Depends(session)])
    def decompose(post_id: str, body: Deconstruct | None = None):
        if not POST_ID.fullmatch(post_id):
            raise HTTPException(422, '推文 ID 格式不正确。')
        found = store.rows(
            'SELECT handle FROM benchmark_posts WHERE post_id=?', (post_id,))
        if not found:
            raise HTTPException(404, '先把这条推文读进来，再拆解。')
        payload = {'post_id': post_id}
        engine = (body.engine if body else '').strip()
        if engine:
            payload['engine'] = engine
        # One deconstruction per post at a time; a second click joins the first.
        task = jobs.enqueue('deconstruct', payload, dedupe='deconstruct:' + post_id)
        return {'task_id': task, 'post_id': post_id, 'handle': found[0]['handle']}
