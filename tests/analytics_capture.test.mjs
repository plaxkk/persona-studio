import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { operateAnalyticsContent, operateAnalyticsOverview,
  queryMatchesDays, queryMatchesDates, queryMatchesOverview } from '../browser-extension/analytics-browser.js';

class FakeXHR {
  listeners = [];
  status = 200;
  responseText = '';
  open(method, url) { this.method = method; this.url = url; }
  addEventListener(name, callback) { if (name === 'loadend') this.listeners.push(callback); }
  send() { for (const callback of this.listeners) callback(); }
}

test('MAIN-world capture retains only a bounded Content business response', () => {
  const window = {};
  const location = { origin: 'https://x.com', pathname: '/i/account_analytics/content',
    href: 'https://x.com/i/account_analytics/content?days=28' };
  const source = fs.readFileSync(new URL('../browser-extension/analytics-capture.js', import.meta.url), 'utf8');
  vm.runInNewContext(source, { window, location, XMLHttpRequest: FakeXHR, URL, TextEncoder, Date, JSON });
  const other = new FakeXHR();
  other.open('GET', 'https://x.com/i/api/graphql/id/accountOverviewDailyQuery?variables={}');
  other.responseText = '{"data":{"secret":"other page"}}';
  other.send();
  assert.equal(window.__personaStudioTakeAnalytics(), null);
  const response = new FakeXHR();
  const variables = encodeURIComponent(JSON.stringify({ from_time: '2026-09-15T00:00:00Z',
    to_time: '2026-10-13T00:00:00Z', max_results: 1000 }));
  response.open('GET', `https://x.com/i/api/graphql/hash/contentPageQuery?variables=${variables}`);
  response.responseText = JSON.stringify({ data: { viewer_v2: { user_results: {
    result: { tweets_results: [] } } } }, request_headers: { authorization: 'must-not-leak' } });
  response.send();
  const captured = window.__personaStudioTakeAnalytics();
  assert.equal(captured.query_kind, 'content');
  assert.equal(captured.max_results, 1000);
  assert.equal(captured.payload.data.viewer_v2.user_results.result.tweets_results.length, 0);
  assert.equal(JSON.stringify(captured).includes('authorization'), false);
  assert.equal(window.__personaStudioTakeAnalytics(), null);
});

test('Overview XHR capture keeps current and previous windows without headers', () => {
  class OverviewXHR {
    listeners = [];
    status = 200;
    responseText = '';
    open(method, url) { this.method = method; this.url = url; }
    addEventListener(name, callback) { if (name === 'loadend') this.listeners.push(callback); }
    send() { for (const callback of this.listeners) callback(); }
  }
  const window = {};
  const location = { origin: 'https://x.com',
    pathname: '/i/account_analytics/overview',
    href: 'https://x.com/i/account_analytics/overview' };
  const source = fs.readFileSync(new URL('../browser-extension/analytics-capture.js', import.meta.url), 'utf8');
  vm.runInNewContext(source, { window, location, XMLHttpRequest: OverviewXHR,
    URL, TextEncoder, Date, JSON });
  const query = new OverviewXHR();
  const variables = encodeURIComponent(JSON.stringify({
    current_from_iso: '2026-09-16T00:00:00.000Z',
    current_to_iso: '2026-10-14T00:00:00.000Z',
    prev_from_iso: '2026-08-19T00:00:00.000Z',
    prev_to_iso: '2026-09-16T00:00:00.000Z',
  }));
  query.open('GET', `https://x.com/i/api/graphql/hash/accountOverviewDailyQuery?variables=${variables}`);
  query.responseText = JSON.stringify({ data: { viewer_v2: { user_results: { result: {
    current_time_series: [], previous_totals: [],
  } } } }, request_headers: { authorization: 'must-not-leak' } });
  query.send();
  const captured = window.__personaStudioTakeAnalytics();
  assert.equal(captured.query_kind, 'overview');
  assert.equal(captured.prev_to, '2026-09-16T00:00:00.000Z');
  assert.equal(JSON.stringify(captured).includes('authorization'), false);
  assert.equal(queryMatchesOverview(captured), true);
});

test('query window must match the requested range', () => {
  const record = { query_kind: 'content', from_time: '2026-09-15T00:00:00Z',
    to_time: '2026-10-13T00:00:00Z', max_results: 1000 };
  assert.equal(queryMatchesDays(record, 28), true);
  assert.equal(queryMatchesDays(record, 90), false);
  assert.equal(queryMatchesDays({ ...record, max_results: -1 }, 28), false);
});

test('custom historical window requires the exact observed X date boundaries', () => {
  const record = { query_kind: 'content', from_time: '2026-06-01T00:00:00.000Z',
    to_time: '2026-06-14T23:59:59.999Z', max_results: 1000 };
  assert.equal(queryMatchesDates(record, '2026-06-01', '2026-06-14'), true);
  assert.equal(queryMatchesDates(record, '2026-06-02', '2026-06-14'), false);
  assert.equal(queryMatchesDates(record, '2026-06-01', '2026-07-03'), false);
  assert.equal(queryMatchesDates(record, '2026-02-30', '2026-03-01'), false);
});

test('browser read verifies account and window before returning data', async () => {
  let removed = false;
  const record = { query_kind: 'content', from_time: '2026-09-15T00:00:00Z',
    to_time: '2026-10-13T00:00:00Z', max_results: 1000,
    payload: { data: { viewer_v2: { user_results: { result: { tweets_results: [] } } } } } };
  globalThis.chrome = { tabs: {
    create: async () => ({ id: 7 }),
    get: async () => ({ status: 'complete', url: 'https://x.com/i/account_analytics/content?days=28' }),
    remove: async () => { removed = true; },
  }, scripting: { executeScript: async ({ world }) => [{ result: world === 'ISOLATED'
    ? { state: 'ready', account: 'kkplayit' } : record }] } };
  const result = await operateAnalyticsContent({ kind: 'analytics_content_read',
    account: 'kkplayit', days: 28 });
  assert.equal(result.state, 'ready');
  assert.equal(result.record, record);
  assert.equal(removed, true);
});

test('historical read opens X custom range and validates the returned query', async () => {
  let opened = '';
  const record = { query_kind: 'content', from_time: '2026-06-01T00:00:00.000Z',
    to_time: '2026-06-14T23:59:59.999Z', max_results: 1000,
    payload: { data: { viewer_v2: { user_results: { result: { tweets_results: [] } } } } } };
  globalThis.chrome = { tabs: {
    create: async ({ url }) => { opened = url; return { id: 8 }; },
    get: async () => ({ status: 'complete', url: opened }),
    remove: async () => {},
  }, scripting: { executeScript: async ({ world }) => [{ result: world === 'ISOLATED'
    ? { state: 'ready', account: 'kkplayit' } : record }] } };
  const result = await operateAnalyticsContent({ account: 'kkplayit', days: 0,
    from_date: '2026-06-01', to_date: '2026-06-14' });
  assert.equal(result.state, 'ready');
  assert.match(opened, /from=2026-06-01&to=2026-06-14$/);
});

test('Overview browser action accepts only matching 28-day business response', async () => {
  const record = { query_kind: 'overview',
    from_time: '2026-09-16T00:00:00.000Z', to_time: '2026-10-14T00:00:00.000Z',
    prev_from: '2026-08-19T00:00:00.000Z', prev_to: '2026-09-16T00:00:00.000Z',
    payload: { data: { viewer_v2: { user_results: { result: {
      current_time_series: [], previous_totals: [],
    } } } } } };
  let opened = '';
  globalThis.chrome = { tabs: {
    create: async ({ url }) => { opened = url; return { id: 9 }; },
    get: async () => ({ status: 'complete', url: opened }),
    remove: async () => {},
  }, scripting: { executeScript: async ({ world }) => [{ result: world === 'ISOLATED'
    ? { state: 'ready', account: 'kkplayit' } : record }] } };
  const result = await operateAnalyticsOverview({ account: 'kkplayit', days: 28 });
  assert.equal(result.state, 'ready');
  assert.equal(opened, 'https://x.com/i/account_analytics/overview');
});
