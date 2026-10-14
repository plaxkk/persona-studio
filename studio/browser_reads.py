"""Persistent, bounded read requests for the authenticated browser connector."""
import asyncio
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from .browser_identity import extension_at_least
from .security import sanitize
from .xreader import ReadError, tweet_row
from .sync_status import SOURCES
from . import account_quality, benchmarks

# A benchmark read is one paginated search, so it is the largest result the
# bridge carries. The extension chunks anything over 100KB (`result-chunks.js`)
# because a single WebSocket frame may not exceed 950000 bytes.
SEARCH_LIMIT = 1000
SEARCH_RESULT_BYTES = 8_000_000
# `search_read` first ships in 1.4.0, so a 1.3 connector must be told to update
# rather than asked for a kind it does not implement.
SEARCH_MINIMUM = (1, 4, 0)
SEARCH_DEADLINE = 600
# One account observation is four reads and they share a single browser tab, so
# they run one after another. Each can take as long as a search read, and the
# last of the four has to finish inside this budget.
ACCOUNT_DEADLINE = 2700
# The connector's states are not all error codes the panel can render, so each
# one becomes the sentence a person can act on.
READ_FAILURE = {
    'waiting_login': '浏览器没有登录 X',
    'waiting_browser': '浏览器没有响应',
    'account_mismatch': '读到的页面不是这个账号',
    'query_mismatch': '页面回答的不是这次查询',
    'invalid_request': '这次读取的请求不合法',
    'source_error': '页面结构读不出来',
}


class BrowserReads:
    def __init__(self, store):
        self.store = store

    def connected(self, minimum=5):
        """Is the connector attached and new enough for this read?

        `minimum` is a patch number (the 1.3 series, as every caller passes) or a
        whole version tuple for a read that needs a later release.
        """
        floor = (1, 3, int(minimum)) if isinstance(minimum, int) else tuple(minimum)
        rows = self.store.rows('SELECT version,seen FROM persona_browser_presence WHERE id=1')
        return bool(rows and rows[0]['seen'] > time.time()-45
                    and extension_at_least(rows[0]['version'], floor))

    def next_action(self):
        with self.store.db(True) as c:
            # All browser reads share one Chrome tab. Two in-flight actions can
            # navigate it away from each other and strand both detail reads.
            busy = c.execute("""SELECT 1 FROM browser_reads b JOIN tasks t ON t.id=b.task_id
                WHERE b.status='sent' AND t.status='running' AND t.cancel=0
                UNION ALL
                SELECT 1 FROM interaction_context_reads b JOIN tasks t ON t.id=b.task_id
                WHERE b.status='sent' AND t.status='running' AND t.cancel=0
                UNION ALL
                SELECT 1 FROM metric_reads b JOIN tasks t ON t.id=b.task_id
                WHERE b.status='sent' AND t.status='running' AND t.cancel=0
                UNION ALL
                SELECT 1 FROM profile_reads b JOIN tasks t ON t.id=b.task_id
                WHERE b.status='sent' AND t.status='running' AND t.cancel=0
                UNION ALL
                SELECT 1 FROM search_reads b JOIN tasks t ON t.id=b.task_id
                WHERE b.status='sent' AND t.status='running' AND t.cancel=0
                UNION ALL
                SELECT 1 FROM account_reads b JOIN tasks t ON t.id=b.task_id
                WHERE b.status='sent' AND t.status='running' AND t.cancel=0
                LIMIT 1""").fetchone()
            if busy:
                return None
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
            row = c.execute("SELECT b.* FROM search_reads b JOIN tasks t ON t.id=b.task_id WHERE b.status='queued' AND b.updated<=? AND t.status='running' AND t.cancel=0 ORDER BY b.id LIMIT 1", (int(time.time()),)).fetchone()
            if row:
                c.execute("UPDATE search_reads SET status='sent',updated=? WHERE id=?", (int(time.time()), row['id']))
                # The server, not the driver, owns the query: §4.9.1's syntax and
                # the one-year window are requirements, and building them in one
                # place keeps a stale driver from silently searching something else.
                return {'id': 'search:' + str(row['id']), 'kind': 'search_read', 'transport': 'sync',
                        'account': row['account'], 'handle': row['handle'],
                        'query': row['query'],
                        'url': benchmarks.search_url(row['query']),
                        'limit': SEARCH_LIMIT}
            row = c.execute("SELECT b.* FROM account_reads b JOIN tasks t ON t.id=b.task_id WHERE b.status='queued' AND b.updated<=? AND t.status='running' AND t.cancel=0 ORDER BY b.id LIMIT 1", (int(time.time()),)).fetchone()
            if row:
                c.execute("UPDATE account_reads SET status='sent',updated=? WHERE id=?", (int(time.time()), row['id']))
                if row['kind'] == account_quality.PROFILE:
                    # The one read here that is not a search: §4.11's signals for
                    # an engager live on *their* profile, so this asks the
                    # connector for somebody else's page.
                    return {'id': 'account:' + str(row['id']), 'kind': 'account_read',
                            'transport': 'sync', 'account': row['account'],
                            'handle': row['handle']}
                return {'id': 'account:' + str(row['id']), 'kind': 'search_read',
                        'transport': 'sync', 'account': row['account'],
                        'handle': row['handle'], 'query': row['query'],
                        'url': benchmarks.search_url(row['query']),
                        'limit': account_quality.SAMPLE_LIMIT}
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
        if isinstance(ident, str) and ident.startswith('search:'):
            self.receive_search(ident, data)
            return
        if isinstance(ident, str) and ident.startswith('account:'):
            self.receive_account(ident, data)
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

    def receive_search(self, ident, data):
        if not re.fullmatch(r'search:[0-9]+', ident): return
        rows = self.store.rows("SELECT * FROM search_reads WHERE id=? AND status='sent'", (int(ident[7:]),))
        if not rows: return
        row = rows[0]
        summary = None
        if data.get('state') == 'ready':
            pages = data.get('pages')
            if (str(data.get('account','')).lower() != row['account'].lower()
                    or str(data.get('handle','')).lstrip('@').lower() != row['handle'].lower()):
                # The query was built here, so a different author means the driver
                # read some other page. Retrying cannot fix that; the user has to.
                data = {'state':'account_mismatch'}
            elif (not isinstance(pages, list) or not pages
                    or not all(isinstance(page, dict) for page in pages)
                    or len(json.dumps(data, ensure_ascii=False)) > SEARCH_RESULT_BYTES):
                data = {'state':'source_error'}
            else:
                summary = self.absorb_search(row, data)
                if summary.get('state') != 'ready':
                    # A refusal is not a summary: what gets written below has to be
                    # the state (which carries its retry count), not the raw page.
                    data, summary = {'state':summary['state']}, None
        retries = int(json.loads(row['result']).get('_retry',0))
        if (data.get('state') in ('waiting_browser','waiting_login','source_error','query_mismatch')
                and retries < 2):
            data = {'state':data['state'],'_retry':retries+1}
            status, available = 'queued', int(time.time()) + 10 * 2 ** retries
        else:
            status, available = 'done', int(time.time())
            if retries: data = {**data, '_retry':retries}  # how often it had to try
        with self.store.db(True) as c:
            c.execute("UPDATE search_reads SET status=?,result=?,updated=? WHERE id=?",
                      (status, json.dumps(summary if summary is not None else data, ensure_ascii=False),
                       available, row['id']))

    def absorb_search(self, row, data):
        """Parse the driver's pages into posts and report what the read reached.

        SearchTimeline bodies are the only multi-megabyte payload the bridge
        carries, and they are redundant once parsed, so they are consumed here
        and never stored: what survives is the posts and what the read missed.
        """
        pages = data['pages']
        # The driver names why it stopped; an unrecognised name is not a licence
        # to call the read finished, so anything but "exhausted" leaves it open.
        stopped = data.get('stopped_reason')
        stopped_reason = stopped if isinstance(stopped, str) and len(stopped) <= 40 else ''
        observed = int(time.time())
        # The pages have to answer the question this read asked. X echoes the
        # query inside the response, and a page built from a different one would
        # otherwise be filed under this account's name -- `parse_search_page`
        # stamps the *requested* handle onto every post it reads.
        if not all(benchmarks.query_matches(benchmarks.echoed_query(page), row['query'])
                   for page in pages):
            return {'state':'query_mismatch'}
        try:
            parsed = [benchmarks.parse_search_page(page, handle=row['handle'], observed_at=observed,
                                                   raw_query=row['query'])
                      for page in pages]
        except benchmarks.BenchmarkShapeError:
            # A timeline whose shape we no longer recognise is a read that has to
            # be retried, not an exception thrown at the message handler.
            return {'state':'source_error'}
        merged = benchmarks.merge_pages(parsed, raw_query=row['query'])
        if stopped_reason != 'exhausted':
            # `merge_pages` reads completeness off the last page, but only the
            # driver knows whether it stopped there or ran out of road. A read the
            # driver cut short is not complete even when the page it stopped on
            # happened to be the empty one.
            merged['complete'] = False
        gap_text = benchmarks.coverage_gap_text(merged['gaps'],
                                                stopped_at_limit=stopped_reason == 'limit',
                                                limit=SEARCH_LIMIT)
        if not merged['complete'] and not gap_text:
            # Nothing failed to parse and the limit was not reached, yet the read
            # did not reach the end of the query: the panel gets the fact, not a
            # blank that reads like a finished read.
            gap_text = '读取未到结果末尾，剩余结果未读取'
        if merged['posts']:
            benchmarks.store_posts(self.store, merged['posts'], handle=row['handle'],
                                   source='search', gap_text=gap_text)
        return {'state':'ready', 'handle':row['handle'], 'query':row['query'],
                'parsed':len(merged['posts']), 'gaps':len(merged['gaps']),
                'complete':merged['complete'], 'coverage_gap':gap_text,
                'stopped_reason':stopped_reason, 'observed_at':observed,
                'foregrounded':bool(data.get('promoted'))}

    def receive_account(self, ident, data):
        """Accept one of an account observation's four reads (06C).

        Two shapes arrive here: a profile page, and three searches. They share
        one queue because a task may hold only a single `search_reads` row, and
        because all four are consumed together -- one account's features are
        computed from whichever of the four came back.
        """
        if not re.fullmatch(r'account:[0-9]+', ident): return
        rows = self.store.rows("SELECT * FROM account_reads WHERE id=? AND status='sent'",
                               (int(ident[8:]),))
        if not rows: return
        row = rows[0]
        search = row['kind'] != account_quality.PROFILE
        summary = None
        if data.get('state') == 'ready':
            if str(data.get('account','')).lower() != row['account'].lower():
                data = {'state':'account_mismatch'}
            elif str(data.get('handle','')).lstrip('@').lower() != row['handle'].lower():
                # The query was built here from this handle, so a page about a
                # different author is another search's result. Retrying cannot
                # fix that; the user has to.
                data = {'state':'account_mismatch'}
            elif not search and not isinstance(data.get('record'), dict):
                data = {'state':'source_error'}
            elif search:
                summary = self.absorb_quality(row, data)
                if summary.get('state') != 'ready':
                    data, summary = {'state': summary['state']}, None
            else:
                # The profile is the one read here that is not a search, so it
                # needs its own write: without this the A 层 features are
                # computed from nothing and every one of them says "主页没有读到".
                account_quality.store_profile(self.store, row['handle'],
                                              data['record'],
                                              observed_at=int(time.time()))
        retries = int(json.loads(row['result']).get('_retry', 0))
        if (data.get('state') in ('waiting_browser','waiting_login','source_error','query_mismatch')
                and retries < 2):
            data = {'state': data['state'], '_retry': retries + 1}
            status, available = 'queued', int(time.time()) + 10 * 2 ** retries
        else:
            status, available = 'done', int(time.time())
            if retries: data = {**data, '_retry': retries}
        with self.store.db(True) as c:
            c.execute("UPDATE account_reads SET status=?,result=?,updated=? WHERE id=?",
                      (status, json.dumps(summary if summary is not None else data,
                                          ensure_ascii=False), available, row['id']))

    def absorb_quality(self, row, data):
        """Parse a quality search into account samples, keeping what was missed.

        06B reads pages of the same shape, but the records go to a different
        table on purpose: a quality read has no viral threshold, so filing its
        ordinary posts under `benchmark_posts` would afterwards read as a
        benchmark account whose 达标 count was zero.
        """
        pages = data['pages']
        stopped = data.get('stopped_reason')
        stopped_reason = stopped if isinstance(stopped, str) and len(stopped) <= 40 else ''
        observed = int(time.time())
        if not all(benchmarks.query_matches(benchmarks.echoed_query(page), row['query'])
                   for page in pages):
            return {'state':'query_mismatch'}
        try:
            parsed = [benchmarks.parse_search_page(page, handle=row['handle'],
                                                   observed_at=observed,
                                                   raw_query=row['query'])
                      for page in pages]
        except benchmarks.BenchmarkShapeError:
            return {'state':'source_error'}
        merged = benchmarks.merge_pages(parsed, raw_query=row['query'])
        if stopped_reason != 'exhausted':
            # Only the driver knows whether it stopped or ran out of road.
            merged['complete'] = False
        gap_text = benchmarks.coverage_gap_text(merged['gaps'],
                                                stopped_at_limit=stopped_reason == 'limit',
                                                limit=account_quality.SAMPLE_LIMIT)
        if stopped_reason == 'buffer_full':
            gap_text = '浏览器一次带回的页面总量到顶，剩余结果未读取'
        elif stopped_reason == 'capture_overflow':
            gap_text = '有页面在浏览器侧被丢掉，这次读取不完整'
        elif not merged['complete'] and not gap_text:
            gap_text = '读取未到结果末尾，剩余结果未读取'
        if merged['posts']:
            account_quality.store_samples(self.store, merged['posts'], handle=row['handle'],
                                          kind=row['kind'], gap_text=gap_text)
        return {'state':'ready', 'handle':row['handle'], 'kind':row['kind'],
                'query':row['query'], 'parsed':len(merged['posts']),
                'gaps':len(merged['gaps']), 'complete':merged['complete'],
                'coverage_gap':gap_text, 'stopped_reason':stopped_reason,
                'observed_at':observed, 'foregrounded':bool(data.get('promoted'))}

    async def quality(self, job, alive):
        """Read one account's four pages and turn them into features (06C-1).

        Four reads run against one shared browser tab, so they are serialised by
        `next_action`. That means a partial result is a real outcome: three reads
        can succeed and the fourth fail. What comes back is still recorded --
        with the failing one named -- because "we could not read the replies" is
        an answer to 测得到吗, while a failed task is not.
        """
        if not self.connected(SEARCH_MINIMUM): raise ReadError('browser_offline')
        handle = str(job['payload'].get('handle','')).strip().lstrip('@').lower()
        if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', handle): raise ReadError('account_missing')
        plan = account_quality.plan(self.store, handle)
        account = str(self.store.get('x_username') or '')
        now = int(time.time())
        with self.store.db(True) as c:
            for kind in account_quality.READ_SET:
                query = plan['reads'].get(kind, '')
                row = c.execute("SELECT status,result FROM account_reads WHERE task_id=? AND kind=?",
                                (job['id'], kind)).fetchone()
                if row is None:
                    c.execute("INSERT INTO account_reads(task_id,kind,handle,account,query,status,updated)"
                              " VALUES(?,?,?,?,?,?,?)",
                              (job['id'], kind, plan['handle'], account, query, 'queued', now))
                    continue
                try: state = json.loads(row['result']).get('state')
                except (TypeError, ValueError): state = None
                # A read that already succeeded is not repeated; one that failed
                # or never returned is queued again, with the current window.
                if row['status'] != 'done' or state != 'ready':
                    c.execute("UPDATE account_reads SET status='queued',query=?,handle=?,updated=?"
                              " WHERE task_id=? AND kind=?",
                              (query, plan['handle'], now, job['id'], kind))
        observed, reads, failures = await self._wait_quality(job, alive)
        if not any(state['state'] == 'ready' for state in reads.values()):
            # Nothing came back at all: the connector's states become the codes
            # the task record and the panel already know how to render.
            first = next((state['state'] for state in reads.values() if state['state']), '')
            raise ReadError({'waiting_login':'needs_login','account_mismatch':'identity_mismatch',
                             'waiting_browser':'browser_offline'}.get(first, 'source_error'))
        for kind, state in reads.items():
            if state['state'] != 'ready':
                failures[kind] = READ_FAILURE.get(state['state'], '浏览器没有返回结果')
        summary = account_quality.record(self.store, plan['handle'], observed_at=observed,
                                         task_id=job['id'], reads=reads, failures=failures)
        # `Store.event` is the studio's own log; the panel reads the feature row.
        self.store.event('account_quality', f'@{plan["handle"]} 账号观察',
                         {'counts': summary['counts'], 'reads': {
                             kind: state['state'] for kind, state in reads.items()},
                          'confidence': summary['confidence']})
        return {**summary, 'partial': bool(failures),
                'reads': {kind: state['state'] for kind, state in reads.items()}}

    async def _wait_quality(self, job, alive):
        """Wait for all four reads, returning when none of them is still moving.

        A read that exhausted its retries is finished, not pending, so the wait
        ends on "nothing is queued or in flight" rather than on "all succeeded".
        """
        deadline = time.monotonic() + ACCOUNT_DEADLINE
        while time.monotonic() < deadline:
            if not alive(job): raise ReadError('cancelled')
            rows = self.store.rows("SELECT kind,status FROM account_reads WHERE task_id=?",
                                   (job['id'],))
            if rows and all(row['status'] == 'done' for row in rows):
                return int(time.time()), account_quality.read_states(self.store, job['id']), {}
            await asyncio.sleep(1)
        raise ReadError('timeout')

    async def benchmark(self, job, alive):
        """Read one benchmark account's §4.9.1 viral posts through the connector."""
        if not self.connected(SEARCH_MINIMUM): raise ReadError('browser_offline')
        handle = str(job['payload'].get('handle','')).strip().lstrip('@').lower()
        if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', handle): raise ReadError('account_missing')
        query = benchmarks.current_query(self.store, handle)
        account = self.store.get('x_username')
        with self.store.db(True) as c:
            c.execute("INSERT OR IGNORE INTO search_reads(task_id,handle,account,query,status,updated) VALUES(?,?,?,?,?,?)",
                      (job['id'],handle,account,query,'queued',int(time.time())))
            c.execute("UPDATE search_reads SET status='queued',query=? WHERE task_id=? AND status='sent'", (query,job['id']))
        data = await self._wait_search(job, alive)
        if data.get('state') != 'ready':
            # The connector's states are not all error codes the panel can render,
            # so each one becomes the sentence the user actually needs.
            raise ReadError({'waiting_login':'needs_login','account_mismatch':'identity_mismatch',
                             'waiting_browser':'browser_offline'}.get(data.get('state'),'source_error'))
        return {'handle':data['handle'], 'query':data['query'], 'parsed':data['parsed'],
                'gaps':data['gaps'], 'complete':data['complete'],
                'coverage_gap':data['coverage_gap']}

    async def _wait_search(self, job, alive):
        deadline = time.monotonic()+SEARCH_DEADLINE
        while time.monotonic()<deadline:
            if not alive(job): raise ReadError('cancelled')
            row = self.store.rows("SELECT status,result FROM search_reads WHERE task_id=?",(job['id'],))
            if row and row[0]['status']=='done': return json.loads(row[0]['result'])
            # A paginated read can outlast the connector's heartbeat. Keep the
            # bounded task open so the in-flight read can return (see enrich).
            await asyncio.sleep(1)
        raise ReadError('timeout')

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
