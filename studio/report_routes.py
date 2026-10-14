"""Weekly diagnosis report: list, ask for one, and read one back."""
import time

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from . import weekly


class Generate(BaseModel):
    # Empty means "the most recent week that has already ended".
    week_start: str = Field(default='', max_length=10)


def register(app, store, jobs, session):
    def account():
        name = str(store.get('x_username', '') or '')
        if not name:
            raise HTTPException(422, '先在本机设置里填上 X 用户名。')
        return name

    @app.get('/api/v1/reports/weekly', dependencies=[Depends(session)])
    def listing():
        name = account()
        now_ms = int(time.time() * 1000)
        try:
            current = weekly.resolve_week(store, now_ms).isoformat()
        except ValueError:
            current = ''
        return {'account': name, 'latest_week_start': current,
                'reports': weekly.listing(store, name)}

    @app.post('/api/v1/reports/weekly/generate', dependencies=[Depends(session)])
    def generate(body: Generate | None = None):
        name = account()
        now_ms = int(time.time() * 1000)
        try:
            week_start = weekly.resolve_week(store, now_ms,
                                             ((body.week_start if body else '') or '').strip() or None)
        except ValueError as exc:
            raise HTTPException(422, '周起始日必须是一个周一（YYYY-MM-DD）。') from exc
        # A week that is still running has moving numbers; §4.8 compares two ends.
        if week_start > weekly.last_complete_week(now_ms, weekly.timezone(store)):
            raise HTTPException(422, '这一周还没结束，等周结束再生成。')
        task = jobs.enqueue('weekly', {'week_start': week_start.isoformat()},
                            dedupe='weekly:' + week_start.isoformat())
        return {'task_id': task, 'week_start': week_start.isoformat()}

    @app.get('/api/v1/reports/weekly/{ident}', dependencies=[Depends(session)])
    def detail(ident: str):
        report = weekly.load(store, ident)
        if not report:
            raise HTTPException(404, '没有这份周报。')
        return report
