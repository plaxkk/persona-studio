import { test } from 'node:test';
import assert from 'node:assert/strict';
import { operateSync } from '../browser-extension/sync-browser.js';

test('detail read waits for the target route and slow X page before inspecting it', async () => {
  const originalSetTimeout = globalThis.setTimeout;
  const originalChrome = globalThis.chrome;
  globalThis.setTimeout = (callback) => { callback(); return 0; };
  const oldUrl = 'https://x.com/home';
  const targetUrl = 'https://x.com/someone/status/8001';
  let navigationPolls = 0;
  let navigating = false;
  const readKinds = [];
  const session = {};
  globalThis.chrome = {
    storage: { session: {
      get: async (key) => ({ [key]: session[key] }),
      set: async (values) => Object.assign(session, values),
    } },
    tabs: {
      create: async () => ({ id: 7, url: oldUrl, status: 'complete' }),
      update: async () => { navigating = true; return { id: 7, url: targetUrl, status: 'loading' }; },
      get: async () => {
        if (!navigating) return { id: 7, url: oldUrl, status: 'complete' };
        navigationPolls += 1;
        if (navigationPolls <= 2) return { id: 7, url: oldUrl, status: 'complete' };
        if (navigationPolls <= 21) return { id: 7, url: targetUrl, status: 'loading' };
        return { id: 7, url: targetUrl, status: 'complete' };
      },
    },
    scripting: { executeScript: async ({ args: [action] }) => {
      readKinds.push(action.kind);
      if (action.kind === 'identity') return [{ result: { state: 'ready', account: 'owner' } }];
      assert.ok(navigationPolls > 21, 'must not read the old or loading page');
      return [{ result: { state: 'ready', post_id: '8001', author: 'someone', account: 'owner',
        record: { id: '8001', author: 'someone', text: '完整正文' } } }];
    } },
  };
  try {
    const result = await operateSync({ kind: 'context_read', account: 'owner', author: 'someone', post_id: '8001' });
    assert.equal(result.state, 'ready');
    assert.deepEqual(readKinds, ['identity', 'sync_detail']);
    assert.ok(navigationPolls > 21);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.chrome = originalChrome;
  }
});
