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

    def connected(self, min_patch=5):
        rows = self.store.rows('SELECT version,seen FROM persona_browser_presence WHERE id=1')
        version = rows[0]['version'] if rows else ''
        return bool(rows and rows[0]['seen'] > time.time()-45
                    and re.fullmatch(r'1\.3\.\d+', version)
                    and int(version.split('.')[-1]) >= min_patch)

    def next_action(self):
        with self.store.db(True) as c:
            row = c.execute("SELECT b.* FROM browser_reads b JOIN tasks t ON t.id=b.task_id WHERE b.status='queued' AND b.updated<=? AND t.status='running' AND t.cancel=0 ORDER BY b.id LIMIT 1", (int(time.time()),)).fetchone()
            if row:
                c.execute("UPDATE browser_reads SET status='sent',updated=? WHERE id=?", (int(time.time()), row['id']))
                return {'id': row['id'], 'source': row['source'], 'account': row['account'],
                        'kind': 'sync_read', 'transport': 'sync'}
            row = c.execute("SELECT b.* FROM interaction_context_reads b JOIN tasks t ON t.id=b.task_id WHERE b.status='queued' AND b.updated<=? AND t.status='running' AND t.cancel=0 ORDER BY b.id LIMIT 1", (int(time.time()),)).fetchone()
            if not row: return None
            c.execute("UPDATE interaction_context_reads SET status='sent',updated=? WHERE id=?", (int(time.time()), row['id']))
            return {'id': 'context:' + str(row['id']), 'kind': 'context_read', 'transport': 'sync',
                    'post_id': row['post_id'], 'author': row['author'], 'account': row['account']}

    def receive(self, ident, data):
        # Only the authenticated extension bridge can call this method.
        if not isinstance(data, dict): return
        if isinstance(ident, str) and ident.startswith('context:'):
            self.receive_context(ident, data)
            return
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

    def receive_context(self, ident, data):
        if not re.fullmatch(r'context:[0-9]+', ident): return
        with self.store.db(True) as c:
            row = c.execute("SELECT * FROM interaction_context_reads WHERE id=? AND status='sent'", (int(ident[8:]),)).fetchone()
            if not row: return
            if data.get('state') == 'ready' and (str(data.get('post_id')) != row['post_id'] or
                str(data.get('author','')).lower() != row['author'].lower() or
                str(data.get('account','')).lower() != row['account'].lower() or
                not isinstance(data.get('record'), dict) or
                not str(data.get('record', {}).get('text','')).strip() or
                str(data.get('record', {}).get('id','')) != row['post_id'] or
                str(data.get('record', {}).get('author','')).lower() != row['author'].lower()):
                data = {'state':'source_error'}
            if len(json.dumps(data, ensure_ascii=False)) > 50000:
                data = {'state':'source_error'}
            retries = int(json.loads(row['result']).get('_retry', 0))
            if data.get('state') in ('waiting_browser','source_error') and retries < 2:
                data['_retry'] = retries + 1
                status, available = 'queued', int(time.time()) + 10 * 2 ** retries
            else:
                status, available = 'done', int(time.time())
            c.execute("UPDATE interaction_context_reads SET status=?,result=?,updated=? WHERE id=?",
                      (status, json.dumps(data, ensure_ascii=False), available, row['id']))

    async def enrich(self, job, alive):
        if not self.connected(7): raise ReadError('browser_offline')
        now = int(time.time())
        account = self.store.get('x_username')
        ids = [str(p) for p in job['payload'].get('post_ids', []) if re.fullmatch(r'\d+', str(p))][:30]
        with self.store.db(True) as c:
            for post_id in ids:
                post = c.execute("SELECT author FROM posts WHERE id=? AND kind!='own'", (post_id,)).fetchone()
                if post:
                    c.execute("INSERT OR IGNORE INTO interaction_context_reads(task_id,post_id,author,account,status,updated) VALUES(?,?,?,?,?,?)",
                              (job['id'],post_id,post['author'],account,'queued',now))
            c.execute("UPDATE interaction_context_reads SET status='queued' WHERE task_id=? AND status='sent'", (job['id'],))
        deadline = time.monotonic() + 480
        while time.monotonic() < deadline:
            if not alive(job): raise ReadError('cancelled')
            rows = self.store.rows("SELECT * FROM interaction_context_reads WHERE task_id=?", (job['id'],))
            if all(r['status']=='done' for r in rows): break
            # Detail navigation can occupy the extension long enough for its
            # heartbeat to expire. Keep the bounded task open so the in-flight
            # read can reconnect and return, instead of abandoning the queue.
            await asyncio.sleep(1)
        updated, failed = 0, 0
        with self.store.db(True) as c:
            for row in rows:
                data = json.loads(row['result'])
                if row['status'] != 'done' or data.get('state') != 'ready':
                    failed += 1
                    continue
                record = data['record']
                post = c.execute("SELECT context FROM posts WHERE id=?", (row['post_id'],)).fetchone()
                if not post: continue
                context = json.loads(post['context'])
                for key in ('parent_needed','quote_needed','truncated','media_pending'):
                    context[key] = bool(record.get(key))
                for key in ('parent','quote'):
                    item = record.get(key)
                    if isinstance(item, dict) and re.fullmatch(r'[A-Za-z0-9_]{1,15}', str(item.get('author',''))) and re.fullmatch(r'\d+', str(item.get('id',''))):
                        context[key] = {'id': str(item['id']), 'author': item['author'],
                                        'text': sanitize(str(item.get('text',''))[:6000]),
                                        'url': f"https://x.com/{item['author']}/status/{item['id']}"}
                context['browser_context'] = sanitize(str(record.get('browser_context',''))[:2000])
                context['media'] = [{'kind': str(m.get('kind',''))[:20], 'source': str(m.get('source',''))[:20], 'description': sanitize(str(m.get('description',''))[:500])}
                                    for m in record.get('media', [])[:8] if isinstance(m, dict)]
                context['detail_checked'] = True
                try: published=int(datetime.fromisoformat(str(record.get('created_at','')).replace('Z','+00:00')).timestamp())
                except (TypeError, ValueError, OverflowError): published=None
                if published: context['published_at'] = published
                c.execute("UPDATE posts SET text=?,context=?,seen=? WHERE id=?",
                          (sanitize(str(record.get('text',''))[:10000]), json.dumps(context, ensure_ascii=False), now, row['post_id']))
                updated += 1
        if not updated:
            raise ReadError('context_unavailable')
        return {'checked': updated, 'failed': failed, 'requested': len(rows),
                'complete': failed == 0}

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
