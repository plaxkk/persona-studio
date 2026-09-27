"""Durable inspiration -> conversation -> reviewed final draft workflow."""
import time
import uuid
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from .security import sanitize


class Idea(BaseModel):
    idea: str = Field(min_length=1, max_length=12000)


class Revision(BaseModel):
    version: int = Field(ge=1)
    idea: str = Field(min_length=1, max_length=12000)
    candidate: str = Field(default="", max_length=12000)


class Turn(BaseModel):
    version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=12000)
    mode: Literal['brainstorm', 'draft'] = 'brainstorm'


class Finalize(BaseModel):
    version: int = Field(ge=1)


def register(app, store, jobs, session, vault):
    def editable(c, ident, version):
        row = c.execute('SELECT * FROM creations WHERE id=?', (ident,)).fetchone()
        if not row:
            raise HTTPException(404, '创作记录不存在')
        if row['version'] != version or row['draft_id']:
            raise HTTPException(409, '内容已变化，请重新打开最新记录。')
        if row['task_id'] and c.execute("SELECT 1 FROM tasks WHERE id=? AND status IN ('queued','running')", (row['task_id'],)).fetchone():
            raise HTTPException(409, 'AI 正在回应，请等待或停止后再修改。')
        return row

    @app.get('/api/v1/creations', dependencies=[Depends(session)])
    def listing():
        return store.rows('SELECT * FROM creations ORDER BY updated DESC, rowid DESC')

    @app.post('/api/v1/creations', dependencies=[Depends(session)])
    def create(body: Idea):
        idea = sanitize(body.idea, vault.all().values()).strip()
        if not idea:
            raise HTTPException(422, '先记下一点想法。')
        ident, now = uuid.uuid4().hex, int(time.time())
        with store.db(True) as c:
            c.execute('INSERT INTO creations(id,idea,created,updated) VALUES(?,?,?,?)', (ident, idea, now, now))
        return {'id': ident}

    @app.get('/api/v1/creations/{ident}', dependencies=[Depends(session)])
    def detail(ident: str):
        rows = store.rows('SELECT * FROM creations WHERE id=?', (ident,))
        if not rows:
            raise HTTPException(404, '创作记录不存在')
        item = rows[0]
        item['messages'] = store.rows('SELECT id,role,text,created FROM messages WHERE channel=? ORDER BY id', ('creation:' + ident,))
        if item['draft_id']:
            drafts = store.rows('SELECT text FROM drafts WHERE id=?', (item['draft_id'],))
            item['final_text'] = drafts[0]['text'] if drafts else item['candidate']
        item['task'] = None
        if item['task_id']:
            rows = store.rows('SELECT id,status,error,payload FROM tasks WHERE id=?', (item['task_id'],))
            if rows:
                import json
                task = rows[0]
                task['text'] = json.loads(task.pop('payload')).get('text', '')
                item['task'] = task
        return item

    @app.put('/api/v1/creations/{ident}', dependencies=[Depends(session)])
    def edit(ident: str, body: Revision):
        idea = sanitize(body.idea, vault.all().values()).strip()
        candidate = sanitize(body.candidate, vault.all().values()).strip()
        if not idea:
            raise HTTPException(422, '灵感内容不能为空。')
        with store.db(True) as c:
            editable(c, ident, body.version)
            c.execute("UPDATE creations SET idea=?,candidate=?,stage=CASE WHEN ?<>'' THEN 'writing' ELSE stage END,version=version+1,updated=? WHERE id=?", (idea, candidate, candidate, int(time.time()), ident))
        return detail(ident)

    @app.post('/api/v1/creations/{ident}/turn', dependencies=[Depends(session)])
    def turn(ident: str, body: Turn):
        text = sanitize(body.text, vault.all().values()).strip()
        if not text:
            raise HTTPException(422, '请输入想讨论或修改的内容。')
        # enqueue checks the revision and reserves this conversation in one transaction.
        task = jobs.enqueue('chat', {'creation_id': ident, 'expected_version': body.version,
            'mode': body.mode, 'text': text, 'channel': 'creation:' + ident})
        return {'task_id': task}

    @app.post('/api/v1/creations/{ident}/finalize', dependencies=[Depends(session)])
    def finalize(ident: str, body: Finalize):
        with store.db(True) as c:
            existing = c.execute('SELECT draft_id FROM creations WHERE id=?', (ident,)).fetchone()
            if existing and existing['draft_id']:
                return {'draft_id': existing['draft_id']}
            row = editable(c, ident, body.version)
            if not row['candidate'].strip():
                raise HTTPException(422, '先生成或写好候选稿，再确认定稿。')
            draft_id = store.draft(row['candidate'], 'post', c=c)
            c.execute("UPDATE creations SET stage='final',draft_id=?,version=version+1,updated=? WHERE id=?", (draft_id, int(time.time()), ident))
            store.event('draft', '已确认定稿，等待手动发布', {'draft_id': draft_id, 'creation_id': ident}, c)
        return {'draft_id': draft_id}
