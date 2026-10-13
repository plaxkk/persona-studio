"""Persistent, bounded read requests for the authenticated browser connector."""
import asyncio
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
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
            if row:
                c.execute("UPDATE interaction_context_reads SET status='sent',updated=? WHERE id=?", (int(time.time()), row['id']))
                return {'id': 'context:' + str(row['id']), 'kind': 'context_read', 'transport': 'sync',
                        'post_id': row['post_id'], 'author': row['author'], 'account': row['account']}
            row = c.execute("SELECT b.* FROM metric_reads b JOIN tasks t ON t.id=b.task_id WHERE b.status='queued' AND b.updated<=? AND t.status='running' AND t.cancel=0 ORDER BY b.id LIMIT 1", (int(time.time()),)).fetchone()
            if row:
                c.execute("UPDATE metric_reads SET status='sent',updated=? WHERE id=?", (int(time.time()), row['id']))
                return {'id': 'metric:' + str(row['id']), 'kind': 'metric_read', 'transport': 'sync',
                        'post_id': row['tweet_id'], 'author': row['author'], 'account': row['account']}
            row = c.execute("SELECT b.* FROM profile_reads b JOIN tasks t ON t.id=b.task_id WHERE b.status='queued' AND b.updated<=? AND t.status='running' AND t.cancel=0 ORDER BY b.id LIMIT 1", (int(time.time()),)).fetchone()
            if row:
                c.execute("UPDATE profile_reads SET status='sent',updated=? WHERE id=?", (int(time.time()), row['id']))
                return {'id': 'profile:' + str(row['id']), 'kind': 'profile_read', 'transport': 'sync',
                        'account': row['account']}
            return None

    def receive(self, ident, data):
        # Only the authenticated extension bridge can call this method.
        if not isinstance(data, dict): return
        if isinstance(ident, str) and ident.startswith('context:'):
            self.receive_context(ident, data)
            return
        if isinstance(ident, str) and ident.startswith(('metric:', 'profile:')):
            self.receive_measurement(ident, data)
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

    def receive_measurement(self, ident, data):
        kind, _, number = ident.partition(':')
        if kind not in ('metric','profile') or not number.isdigit(): return
        table = 'metric_reads' if kind == 'metric' else 'profile_reads'
        with self.store.db(True) as c:
            row = c.execute(f"SELECT * FROM {table} WHERE id=? AND status='sent'", (int(number),)).fetchone()
            if not row: return
            if (data.get('state') == 'ready' and
                (str(data.get('account','')).lower() != row['account'].lower() or
                 not isinstance(data.get('record'),dict) or
                 (kind == 'metric' and (str(data.get('post_id')) != row['tweet_id'] or
                  str(data.get('author','')).lower() != row['author'].lower())))):
                data = {'state':'source_error'}
            if len(json.dumps(data, ensure_ascii=False)) > 10000: data = {'state':'source_error'}
            retries = int(json.loads(row['result']).get('_retry',0))
            if data.get('state') in ('waiting_browser','source_error') and retries < 2:
                data = {'state':data['state'],'_retry':retries+1}
                status, available = 'queued', int(time.time()) + 10 * 2 ** retries
            else:
                status, available = 'done', int(time.time())
                data['_observed_at'] = available
            c.execute(f"UPDATE {table} SET status=?,result=?,updated=? WHERE id=?",
                      (status,json.dumps(data,ensure_ascii=False),available,row['id']))

    async def metric(self, job, alive):
        if not self.connected(8): raise ReadError('browser_offline')
        post_id = str(job['payload'].get('tweet_id',''))
        rows = self.store.rows("SELECT author FROM draft_publications WHERE tweet_id=? AND author IS NOT NULL LIMIT 1",(post_id,))
        if not rows or not re.fullmatch(r'\d{1,30}',post_id): raise ReadError('post_missing')
        account = self.store.get('x_username')
        with self.store.db(True) as c:
            c.execute("INSERT OR IGNORE INTO metric_reads(task_id,tweet_id,author,account,status,updated) VALUES(?,?,?,?,?,?)",
                      (job['id'],post_id,rows[0]['author'],account,'queued',int(time.time())))
            c.execute("UPDATE metric_reads SET status='queued' WHERE task_id=? AND status='sent'",(job['id'],))
        data = await self._wait_measurement('metric_reads',job,alive)
        if data.get('state') != 'ready': raise ReadError('source_error')
        observed = data['_observed_at']
        record = data.get('record') or {}
        try: published = int(datetime.fromisoformat(str(record['created_at']).replace('Z','+00:00')).timestamp())
        except (KeyError,TypeError,ValueError,OverflowError): published = None
        if published is not None and not (1136073600 <= published <= observed+300): published = None
        counts = {}
        for key in ('views','likes','replies','reposts','quotes','bookmarks'):
            value = record.get(key)
            counts[key] = value if type(value) is int and 0 <= value <= 10**12 else None
        target = str(job['payload'].get('target_window','baseline'))
        if target not in ('baseline','24h','72h'): target = 'baseline'
        age = observed-published if published else None
        expected = {'24h':86400,'72h':259200}.get(target)
        window_status = ('baseline' if expected is None else 'unknown' if age is None else
                         'on_time' if abs(age-expected) <= 7200 else 'late' if age>expected else 'early')
        kind = record.get('kind') if record.get('kind') in ('post','quote','reply') else None
        with self.store.db(True) as c:
            c.execute("UPDATE draft_publications SET platform_observed_at=?,published_at=COALESCE(?,published_at),observation_source='browser',publication_kind=COALESCE(?,publication_kind) WHERE tweet_id=?",
                      (observed,published,kind,post_id))
            c.execute("INSERT OR IGNORE INTO post_metric_snapshots(tweet_id,observed_at,published_at,age_seconds,target_window,window_status,source,views,likes,replies,reposts,quotes,bookmarks) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (post_id,observed,published,age,target,window_status,'browser',*(counts[k] for k in ('views','likes','replies','reposts','quotes','bookmarks'))))
        return {'tweet_id':post_id,'observed_at':observed,'target_window':target,'window_status':window_status}

    async def profile(self, job, alive):
        if not self.connected(8): raise ReadError('browser_offline')
        account = self.store.get('x_username')
        with self.store.db(True) as c:
            c.execute("INSERT OR IGNORE INTO profile_reads(task_id,account,status,updated) VALUES(?,?,?,?)",
                      (job['id'],account,'queued',int(time.time())))
            c.execute("UPDATE profile_reads SET status='queued' WHERE task_id=? AND status='sent'",(job['id'],))
        data = await self._wait_measurement('profile_reads',job,alive)
        if data.get('state') != 'ready': raise ReadError('source_error')
        record = data.get('record') or {}
        count = record.get('followers')
        followers = count if type(count) is int and 0 <= count <= 10**12 else None
        pin = str(record.get('pinned_tweet_id') or '')
        pin = pin if re.fullmatch(r'\d{1,30}',pin) else None
        observed = data['_observed_at']
        day = datetime.fromtimestamp(observed,ZoneInfo(self.store.get('timezone','Asia/Shanghai'))).date().isoformat()
        with self.store.db(True) as c:
            c.execute("INSERT INTO profile_snapshots(account,observed_at,day,followers,bio,pinned_tweet_id,source) VALUES(?,?,?,?,?,?,?) ON CONFLICT(account,day,source) DO UPDATE SET observed_at=excluded.observed_at,followers=COALESCE(excluded.followers,profile_snapshots.followers),bio=excluded.bio,pinned_tweet_id=excluded.pinned_tweet_id",
                      (account,observed,day,followers,sanitize(str(record.get('bio',''))[:3000]),pin,'browser'))
        return {'account':account,'observed_at':observed,'followers':followers}

    async def _wait_measurement(self, table, job, alive):
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if not alive(job): raise ReadError('cancelled')
            row = self.store.rows(f"SELECT status,result FROM {table} WHERE task_id=?",(job['id'],))[0]
            if row['status']=='done': return json.loads(row['result'])
            if not self.connected(8): break
            await asyncio.sleep(1)
        raise ReadError('timeout')

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
            if not self.connected(7): break
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
