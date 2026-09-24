// Regression: ISSUE-003 — redundant profile navigation and premature load failure.
// Found by /qa on 2026-09-24
// Report: .gstack/qa-reports/qa-report-persona-studio-2026-09-24.md
import { test } from "node:test";
import assert from "node:assert/strict";
let saved = {},
  url = "https://x.com/example",
  updates = [],
  replies = [],
  calls = [];
globalThis.chrome = {
  storage: {
    session: {
      get: async (key) => (key === "device" ? {} : { [key]: saved }),
      set: async (data) => {
        saved = Object.values(data)[0];
      },
    },
    onChanged: { addListener() {} },
  },
  alarms: { create() {}, onAlarm: { addListener() {} } },
  tabs: {
    get: async () => ({ id: 7, url, status: "complete" }),
    update: async (_id, opts) => {
      url = opts.url;
      updates.push(url);
    },
  },
  scripting: {
    executeScript: async ({ args }) => {
      calls.push(args[0].kind);
      return [
        {
          result: replies.shift() || {
            state: "ready",
            account: "example",
            records: [],
          },
        },
      ];
    },
  },
};
const { operate } = await import("../browser-extension/persona-browser.js");
const realTimeout = globalThis.setTimeout;
function reset() {
  saved = { tab: 7, generation: 0 };
  url = "https://x.com/example";
  updates = [];
  replies = [];
  calls = [];
  globalThis.setTimeout = (fn) => realTimeout(fn, 0);
}
const action = (kind) => ({
  id: 1,
  import_id: "fixture",
  generation: 0,
  kind,
  account: "example",
});
test("profile on the current page does not reload it", async () => {
  reset();
  try {
    assert.equal((await operate(action("profile"))).state, "ready");
    assert.deepEqual(updates, []);
    assert.deepEqual(calls, ["identity", "snapshot"]);
  } finally {
    globalThis.setTimeout = realTimeout;
  }
});
test("new replies page waits through transient DOM loading", async () => {
  reset();
  replies = [
    { state: "ready" },
    { state: "waiting_browser" },
    { state: "waiting_browser" },
    {
      state: "ready",
      account: "example",
      records: [{ id: "90071992547409930" }],
    },
  ];
  try {
    const result = await operate(action("replies"));
    assert.equal(result.records.length, 1);
    assert.deepEqual(updates, ["https://x.com/example/with_replies"]);
    assert.deepEqual(calls, ["identity", "snapshot", "snapshot", "snapshot"]);
  } finally {
    globalThis.setTimeout = realTimeout;
  }
});
test("challenge stops without repeated navigation", async () => {
  reset();
  replies = [{ state: "challenge" }];
  try {
    assert.equal((await operate(action("profile"))).state, "challenge");
    assert.deepEqual(updates, []);
    assert.deepEqual(calls, ["identity"]);
  } finally {
    globalThis.setTimeout = realTimeout;
  }
});
test("readiness polling is bounded", async () => {
  reset();
  replies = Array(30).fill({ state: "waiting_browser" });
  try {
    assert.equal((await operate(action("profile"))).state, "waiting_browser");
    assert.equal(calls.length, 30);
    assert.deepEqual(updates, []);
  } finally {
    globalThis.setTimeout = realTimeout;
  }
});
