"""Persistent, bounded read requests for the authenticated browser connector."""
import asyncio
import json
import re
import time
from datetime import datetime
from .security import sanitize
from .xreader import ReadError, tweet_row
from .sync_status import SOURCES


class BrowserReads:
    def __init__(self, store):
        self.store = store

    def connected(self):
        rows = self.store.rows('SELECT version,seen FROM persona_browser_presence WHERE id=1')
        version = rows[0]['version'] if rows else ''
        return bool(rows and rows[0]['seen'] > time.time()-45
                    and re.fullmatch(r'1\.3\.\d+', version)
                    and int(version.split('.')[-1]) >= 5)

    def next_action(self):
        with self.store.db(True) as c:
            row = c.execute("SELECT b.* FROM browser_reads b JOIN tasks t ON t.id=b.task_id WHERE b.status='queued' AND b.updated<=? AND t.status='running' AND t.cancel=0 ORDER BY b.id LIMIT 1", (int(time.time()),)).fetchone()
            if not row: return None
            c.execute("UPDATE browser_reads SET status='sent',updated=? WHERE id=?", (int(time.time()), row['id']))
            return {'id': row['id'], 'source': row['source'], 'account': row['account'],
                    'kind': 'sync_read', 'transport': 'sync'}

    def receive(self, ident, data):
        # Only the authenticated extension bridge can call this method.
        if not isinstance(data, dict): return
        with self.store.db(True) as c:
            row = c.execute("SELECT * FROM browser_reads WHERE id=? AND status='sent'", (ident,)).fetchone()
            if not row: return
            state = data.get('state')
            if state == 'ready' and str(data.get('account','')).lower() != row['account'].lower():
                data = {'state':'account_mismatch'}
            elif state == 'ready' and not isinstance(data.get('records'), list):
                data = {'state':'source_error'}
            elif state == 'ready' and len(json.dumps(data, ensure_ascii=False)) > 500000:
                data = {'state':'source_error'}
            previous = json.loads(row['result'])
            retries = int(previous.get('_retry', 0))
            transient = data.get('state') in ('waiting_browser', 'source_error')
            if transient and retries < 2:
                data['_retry'] = retries + 1
                status, available = 'queued', int(time.time()) + 10 * 2 ** retries
            else:
                status, available = 'done', int(time.time())
            c.execute("UPDATE browser_reads SET status=?,result=?,updated=? WHERE id=?",
                      (status, json.dumps(data, ensure_ascii=False), available, ident))

    async def sync(self, job, alive):
        if not self.connected(): raise ReadError('browser_offline')
        account = self.store.get('x_username')
        now = int(time.time())
        with self.store.db(True) as c:
            for source in SOURCES:
                c.execute("INSERT OR IGNORE INTO browser_reads(task_id,source,account,status,updated) VALUES(?,?,?,'queued',?)", (job['id'],source,account,now))
            # Completed sources survive worker restarts; only unfinished reads repeat.
            c.execute("UPDATE browser_reads SET status='queued' WHERE task_id=? AND status='sent'", (job['id'],))
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if not alive(job): raise ReadError('cancelled')
            rows = self.store.rows('SELECT * FROM browser_reads WHERE task_id=?', (job['id'],))
            if all(r['status']=='done' for r in rows): break
            if not self.connected(): break
            await asyncio.sleep(1)
        sources, posts = {}, {}
        still_connected = self.connected()
        codes = {'waiting_login':'needs_login','account_mismatch':'identity_mismatch','challenge':'access_restricted','waiting_browser':'timeout','source_error':'source_error','invalid_request':'source_error'}
        for r in rows:
            data = json.loads(r['result'])
            code = ('browser_offline' if r['status'] != 'done' and not still_connected else
                    'ok' if r['status']=='done' and data.get('state')=='ready' else
                    codes.get(data.get('state'),'timeout'))
            records = data.get('records',[])[:30] if code=='ok' else []
            count=0
            for item in records:
                if not isinstance(item,dict): continue
                p = tweet_row({'id':item.get('id'),'user':{'screen_name':item.get('author')},'text':item.get('text')}, r['source'], account)
                if not p: continue
                try: published=int(datetime.fromisoformat(item.get('created_at','').replace('Z','+00:00')).timestamp())
                except (TypeError,ValueError,OverflowError): published=None
                p['context'].update(published_at=published, truncated=bool(item.get('truncated')), media_pending=bool(item.get('media_pending')), browser_context=sanitize(str(item.get('context',''))[:6000]), reader='browser')
                posts.setdefault(p['id'],p); count+=1
            sources[r['source']]={'status':code,'count':count}
        return {'posts':list(posts.values()),'sources':sources,'complete':all(s['status']=='ok' for s in sources.values())}
