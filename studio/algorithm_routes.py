"""算法更新跟踪（06D）。

上游指 X 官方开源的那份 For You 算法（默认 `xai-org/x-algorithm`）。这里只做三件事：
看它动没动、把「改了什么」原样摆出来、把引擎和人的判断分开存。

每条路由都从路径里取参数，不用查询串：连接助手会拒掉 `desktop.js` 白名单之外的
查询串，`?sha=` 这样的路由在云面板里会 403。
"""
import re

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from . import algorithm_watch

SHA = re.compile(r'[0-9a-f]{7,40}')
EVENT_ID = re.compile(r'[0-9a-f]{32}')


class Check(BaseModel):
    # 检查本身不需要引擎；这一项是人想在读到改动之后顺带让引擎读一遍。
    analyze: bool = False
    engine: str = Field(default='', max_length=32)


class Judge(BaseModel):
    status: str = Field(min_length=1, max_length=16)
    impact: str = Field(default='', max_length=2000)


def register(app, store, jobs, session):
    @app.get('/api/v1/algorithm/events', dependencies=[Depends(session)])
    def listing():
        state = algorithm_watch.upstream(store)
        rows = algorithm_watch.events(store)
        # 「本机还没看过上游」不能由面板自己编一个「上游没有变化」：这里把状态原样
        # 送过去，包括 `last_checked` 为空这一事实。
        return {
            'repo': state['repo'],
            'watching': algorithm_watch.watching(store),
            'state': state,
            'events': rows,
            'statuses': list(algorithm_watch.STATUSES),
            'status_label': algorithm_watch.STATUS_LABEL,
            'check_labels': algorithm_watch.check_labels(),
            'thresholds': algorithm_watch.thresholds(),
        }

    @app.get('/api/v1/algorithm/proposals/{event_id}', dependencies=[Depends(session)])
    def one(event_id: str):
        # 单取一份，面板不用为了看一条而拉回全部事件。
        if not EVENT_ID.fullmatch(event_id):
            raise HTTPException(422, '事件 ID 格式不正确。')
        row = algorithm_watch.proposal(store, event_id)
        if not row:
            raise HTTPException(404, '这条事件不在了。')
        return {'event': row, 'repo': row['repo'],
                'status_label': algorithm_watch.STATUS_LABEL}

    @app.post('/api/v1/algorithm/check', dependencies=[Depends(session)])
    def check(body: Check | None = None):
        payload = {}
        engine = (body.engine if body else '').strip()
        if body and body.analyze:
            payload['analyze'] = True
            if engine:
                payload['engine'] = engine
        # One check at a time; a second click joins the one already queued. The
        # key is the same one the daily automatic check uses, so a manual click
        # does not queue a second read behind the scheduled one.
        task = jobs.enqueue('algorithm_check', payload, dedupe='algorithm-check')
        return {'task_id': task, 'repo': algorithm_watch.repository(store),
                'analyze': bool(payload.get('analyze'))}

    @app.post('/api/v1/algorithm/impact/{sha}', dependencies=[Depends(session)])
    def impact(sha: str, body: Judge):
        if not SHA.fullmatch(sha):
            raise HTTPException(422, '提交哈希格式不正确。')
        status = body.status.strip().lower()
        if status not in algorithm_watch.STATUSES:
            raise HTTPException(422, '这个状态不认。')
        event_id = algorithm_watch.judge(store, sha, status=status,
                                        impact=body.impact.strip())
        if not event_id:
            raise HTTPException(404, '这条提交还没有被记下来。')
        return {'event_id': event_id, 'sha': sha, 'status': status}

    @app.post('/api/v1/algorithm/watching/{state}', dependencies=[Depends(session)])
    def watching(state: str):
        if state not in ('on', 'off'):
            raise HTTPException(422, '只认 on 和 off。')
        store.set('algorithm_watch', state == 'on')
        return {'watching': state == 'on'}
