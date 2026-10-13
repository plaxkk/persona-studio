import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { operateAnalyticsContent, queryMatchesDays } from '../browser-extension/analytics-browser.js';

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
  assert.equal(captured.max_results, 1000);
  assert.equal(captured.payload.data.viewer_v2.user_results.result.tweets_results.length, 0);
  assert.equal(JSON.stringify(captured).includes('authorization'), false);
  assert.equal(window.__personaStudioTakeAnalytics(), null);
});

test('query window must match the requested range', () => {
  const record = { from_time: '2026-09-15T00:00:00Z',
    to_time: '2026-10-13T00:00:00Z', max_results: 1000 };
  assert.equal(queryMatchesDays(record, 28), true);
  assert.equal(queryMatchesDays(record, 90), false);
  assert.equal(queryMatchesDays({ ...record, max_results: -1 }, 28), false);
});

test('browser read verifies account and window before returning data', async () => {
  let removed = false;
  const record = { from_time: '2026-09-15T00:00:00Z',
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
