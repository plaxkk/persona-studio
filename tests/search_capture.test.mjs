import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { operateSearch } from '../browser-extension/search-browser.js';

const SOURCE = fs.readFileSync(new URL('../browser-extension/search-capture.js', import.meta.url), 'utf8');
const HANDLE = 'lifesinger';
const SEARCH = 'https://x.com/search?q=from%3Alifesinger+min_faves%3A500&src=typed_query&f=live';
const QUERY = 'from:lifesinger min_faves:500 -filter:replies -filter:retweets';

class FakeXHR {
  listeners = [];
  status = 200;
  responseText = '';
  open(method, url) { this.method = method; this.url = url; }
  addEventListener(name, callback) { if (name === 'loadend') this.listeners.push(callback); }
  send() { for (const callback of this.listeners) callback(); }
}

function loadCollector({ pathname = '/search', now = () => 1_700_000_000_000 } = {}) {
  const window = {};
  const location = { origin: 'https://x.com', pathname, href: 'https://x.com' + pathname };
  vm.runInNewContext(SOURCE, { window, location, XMLHttpRequest: FakeXHR, URL,
    URLSearchParams, TextEncoder, JSON, Date: { now } });
  return window;
}

// What X sends: the question and the cursor both travel in the request
// variables, and they come back byte-identical inside the response.
function sendSearch(window, { method = 'GET', variables, body, status = 200, responseText }) {
  const request = new FakeXHR();
  request.status = status;
  request.responseText = responseText ?? '{}';
  const encoded = encodeURIComponent(JSON.stringify(variables));
  const url = method === 'POST'
    ? 'https://x.com/i/api/graphql/hash/SearchTimeline'
    : `https://x.com/i/api/graphql/hash/SearchTimeline?variables=${encoded}`;
  request.open(method, url);
  request.send(body);
  return request;
}

const payload = { data: { search_by_raw_query: { raw_query: QUERY } } };
const pageText = (extra = {}) => JSON.stringify({
  ...payload,
  request_headers: { authorization: 'must-not-leak', ...extra },
});

test('search capture keeps the query and cursor but never response headers', () => {
  const window = loadCollector();
  sendSearch(window, {
    variables: { rawQuery: QUERY, cursor: 'CURSOR-1', product: 'Latest' },
    responseText: pageText(),
  });
  const captured = window.__personaStudioTakeSearch();
  assert.equal(captured.raw_query, QUERY);
  assert.equal(captured.cursor_sent, 'CURSOR-1');
  assert.equal(captured.payload.data.search_by_raw_query.raw_query, QUERY);
  assert.equal(JSON.stringify(captured).includes('authorization'), false);
  // One page per take: the driver drains the queue rather than reading a page twice.
  assert.equal(window.__personaStudioTakeSearch(), null);
  assert.deepEqual({ ...window.__personaStudioSearchCounts(), queued: 0 },
    { requests: 1, captured: 1, dropped: 0, queued: 0 });
});

test('search capture ignores every other GraphQL operation on the page', () => {
  const window = loadCollector();
  const other = new FakeXHR();
  other.open('GET', 'https://x.com/i/api/graphql/hash/SearchBoxQuery?variables=%7B%7D');
  other.responseText = '{"data":{"search_box":{"hidden":true}}}';
  other.send();
  assert.equal(window.__personaStudioTakeSearch(), null);
  // Only a SearchTimeline request is even counted as a request.
  assert.equal(window.__personaStudioSearchCounts().requests, 0);
});

test('a page X refused is a page the read did not get', () => {
  const window = loadCollector();
  sendSearch(window, { variables: { rawQuery: QUERY }, status: 429,
    responseText: pageText() });
  assert.equal(window.__personaStudioTakeSearch(), null);
  const counts = window.__personaStudioSearchCounts();
  assert.equal(counts.requests, 1);
  assert.equal(counts.dropped, 1);
});

test('a POST body carries the variables when the URL does not', () => {
  const window = loadCollector();
  sendSearch(window, {
    method: 'POST',
    body: JSON.stringify({ variables: { rawQuery: QUERY, cursor: 'CURSOR-2' } }),
    responseText: pageText(),
  });
  const captured = window.__personaStudioTakeSearch();
  assert.equal(captured.raw_query, QUERY);
  assert.equal(captured.cursor_sent, 'CURSOR-2');
});

test('every response the collector refused is counted, not silently missing', () => {
  const window = loadCollector();
  sendSearch(window, { variables: { rawQuery: QUERY }, responseText: 'not json' });
  sendSearch(window, { variables: { rawQuery: QUERY }, responseText: '{"errors":[{"code":88}]}' });
  sendSearch(window, { variables: { rawQuery: QUERY },
    responseText: 'x'.repeat(4_000_001) });
  const counts = window.__personaStudioSearchCounts();
  assert.equal(counts.captured, 0);
  // A hole in the middle of a read is what the driver reports to the studio.
  assert.equal(counts.dropped, 3);
});

test('a stale response is dropped rather than paired with a fresh request', () => {
  let now = 1_700_000_000_000;
  const window = loadCollector({ now: () => now });
  sendSearch(window, { variables: { rawQuery: QUERY }, responseText: pageText() });
  now += 120_001;
  assert.equal(window.__personaStudioTakeSearch(), null);
  assert.equal(window.__personaStudioSearchCounts().queued, 0);
});

test('the collector only installs itself on the search page', () => {
  const window = loadCollector({ pathname: '/home' });
  assert.equal(window.__personaStudioTakeSearch, undefined);
});

// ---- the driver -----------------------------------------------------------

function timeline(count) {
  return { data: { search_by_raw_query: { search_timeline: { timeline: {
    instructions: [{ entries: Array.from({ length: count }, (_, index) => ({
      content: { entryType: 'TimelineTimelineItem', index },
    })) }],
  } } } } };
}

const captured = (count, { cursor = 'C1', bytes = 20_000, query = QUERY } = {}) => ({
  raw_query: query, cursor_sent: cursor, observed_at: 0, captured_at_ms: 0,
  bytes, payload: timeline(count),
});

function install({ pages = [], identity = { state: 'ready', account: 'kkplayit' },
  counts = null, tabUrl = SEARCH, loadingRounds = 0 } = {}) {
  const opened = [], removed = [], updates = [];
  let index = 0, identityIndex = 0, loadIndex = 0;
  const identities = Array.isArray(identity) ? identity : [identity];
  globalThis.chrome = {
    tabs: {
      create: async ({ url }) => { opened.push(url); return { id: 7 }; },
      get: async () => ({ status: loadIndex++ < loadingRounds ? 'loading' : 'complete', url: tabUrl }),
      update: async (id, props) => { updates.push(props); },
      remove: async (id) => { removed.push(id); },
    },
    scripting: { executeScript: async (options) => {
      if (options.world === 'ISOLATED')
        return [{ result: options.args ? identities[Math.min(identityIndex++, identities.length - 1)] : true }];
      if (String(options.func).includes('Counts')) return [{ result: counts }];
      return [{ result: index < pages.length ? pages[index++] : null }];
    } },
  };
  return { opened, removed, updates };
}

function action(extra = {}) {
  return { kind: 'search_read', account: 'kkplayit', handle: HANDLE,
    url: SEARCH, ...extra };
}

test('search read pages until X returns the empty page and closes its tab', async () => {
  // The ending page echoes the cursor it was sent, so it repeats the cursor of
  // the page before it -- measured live on @lifesinger and @chunxiangai. An
  // ending read as a repeat would leave every read permanently incomplete.
  const { opened, removed } = install({ pages: [
    captured(20, { cursor: 'C1' }), captured(5, { cursor: 'C2' }),
    captured(0, { cursor: 'C2' }),
  ] });
  const read = await operateSearch(action());
  assert.equal(read.state, 'ready');
  assert.equal(read.account, 'kkplayit');
  assert.equal(read.handle, HANDLE);
  assert.equal(read.stopped_reason, 'exhausted');
  assert.equal(read.promoted, false);
  assert.equal(read.pages.length, 3);
  assert.equal(opened[0], SEARCH);
  assert.deepEqual(removed, [7]);
});

test('search read stops at the limit it was given', async () => {
  install({ pages: [captured(30), captured(30, { cursor: 'C2' })] });
  const read = await operateSearch(action({ limit: 25 }));
  assert.equal(read.state, 'ready');
  assert.equal(read.stopped_reason, 'limit');
  assert.equal(read.pages.length, 1);
});

test('a cursor that comes back twice as often as it should ends the read', async () => {
  install({ pages: [captured(20, { cursor: 'SAME' }), captured(20, { cursor: 'SAME' }),
    captured(20, { cursor: 'SAME' }), captured(20, { cursor: 'SAME' })] });
  const read = await operateSearch(action());
  assert.equal(read.stopped_reason, 'no_progress');
  assert.equal(read.pages.length, 1);
});

test('a dropped page is reported even when the loop reached the end', async () => {
  install({ pages: [captured(3), captured(0)],
    counts: { requests: 3, captured: 2, dropped: 1, queued: 0 } });
  const read = await operateSearch(action());
  assert.equal(read.stopped_reason, 'capture_overflow');
});

test('a read that had to raise the tab says so', async () => {
  const { updates } = install({ pages: [null, null, null, null, captured(0)] });
  const read = await operateSearch(action());
  assert.equal(read.promoted, true);
  assert.deepEqual(updates, [{ active: true }]);
  assert.equal(read.stopped_reason, 'exhausted');
});

test('a background search that stalls is raised and a late identity is retried', async () => {
  const { updates } = install({ loadingRounds: 9,
    identity: [{ state: 'waiting_browser' }, { state: 'ready', account: 'kkplayit' }],
    pages: [captured(1), captured(0)] });
  const read = await operateSearch(action());
  assert.equal(read.state, 'ready');
  assert.equal(read.promoted, true);
  assert.deepEqual(updates, [{ active: true }]);
});

test('a login wall or a wrong page is reported instead of read', async () => {
  install({ tabUrl: 'https://x.com/i/flow/login' });
  assert.equal((await operateSearch(action())).state, 'waiting_login');
  install({ tabUrl: 'https://x.com/home' });
  assert.equal((await operateSearch(action())).state, 'source_error');
  install({ identity: { state: 'waiting_browser' } });
  assert.equal((await operateSearch(action())).state, 'waiting_browser');
});

test('a request that is not a benchmark search never opens a tab', async () => {
  for (const bad of [
    action({ url: 'https://x.com/home' }),
    action({ url: 'https://x.com/search' }),
    action({ url: 'https://example.com/search?q=a' }),
    action({ account: 'not a handle' }),
    action({ handle: '@@@' }),
    action({ limit: 0 }),
    action({ limit: 1001 }),
    action({ limit: 2.5 }),
  ]) {
    const { opened } = install({ pages: [captured(0)] });
    assert.equal((await operateSearch(bad)).state, 'invalid_request', JSON.stringify(bad));
    assert.deepEqual(opened, []);
  }
});
