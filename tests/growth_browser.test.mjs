import test from 'node:test';
import assert from 'node:assert/strict';
import { operateGrowthFollowers } from '../browser-extension/growth-browser.js';

test('an already-loaded X followers page supplies a sample without changing that tab', async () => {
  const changed = [];
  const urls = new Map([[1, 'https://x.com/kkplayit/followers']]);
  globalThis.chrome = {
    tabs: {
      query: async (query) => query.active ? [{ id: 1, url: urls.get(1) }] :
        [{ id: 1, url: urls.get(1) }],
      create: async ({ url }) => { urls.set(2, url); return { id: 2 }; },
      get: async (id) => ({ id, url: urls.get(id), status: 'complete' }),
      update: async (id, change) => { changed.push(id); if (change.url) urls.set(id, change.url); },
      remove: async (id) => { changed.push(id); },
    },
    scripting: {
      executeScript: async ({ target, func, args }) => {
        if (func.name === 'readFollowerCards') {
          assert.equal(target.tabId, 1);
          return [{ result: { state: 'ready', handles: [{ handle: 'reader', verified: true }] } }];
        }
        if (args?.[0]?.kind === 'identity') return [{ result: { state: 'ready' } }];
        return [{ result: { state: 'ready', handle: 'reader',
          record: { followers: 1200, followers_display: '1,200 Followers' } } }];
      },
    },
  };
  try {
    const result = await operateGrowthFollowers({ account: 'kkplayit', exclude: [] });
    assert.equal(result.state, 'ready');
    assert.equal(result.discovered, 1);
    assert.deepEqual(result.records, [{ handle: 'reader', verified: true,
      followers: 1200, followers_display: '1,200 Followers' }]);
    assert.ok(changed.length > 0 && changed.every((id) => id === 2));
  } finally {
    delete globalThis.chrome;
  }
});
