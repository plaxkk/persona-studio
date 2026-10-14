import { readPersonaPage } from "./persona-page.js";

// Reads one benchmark account's viral posts off X's search page (06B-1).
//
// The page is X's own: nothing is typed into it, no query is synthesised here,
// and no response body leaves the browser unparsed. What the driver does is
// scroll a search result page that the studio already asked for and collect the
// SearchTimeline bodies X publishes while doing it. `studio/benchmarks.py` is
// what decides whether those bodies answer the question the studio asked.
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const SEARCH_PREFIX = "https://x.com/search";
const LOAD_ATTEMPTS = 70;
const POLL_MS = 700;
const IDLE_ROUNDS = 4;
const STOP_ROUNDS = 40;
const DEADLINE_MS = 420_000;
const RETENTION_BYTES = 6_000_000;
const MIN_LIMIT = 1;
const MAX_LIMIT = 1000;

// Structural probe only: enough to know the page is the empty one that ends a
// query. The server re-derives the truth from the same body.
function countItems(payload) {
  const instructions =
    payload?.data?.search_by_raw_query?.search_timeline?.timeline?.instructions;
  if (!Array.isArray(instructions)) return 0;
  let count = 0;
  for (const instruction of instructions)
    for (const entry of instruction?.entries || [])
      if (entry?.content?.entryType === "TimelineTimelineItem") count += 1;
  return count;
}

async function inMain(tabId, func) {
  const done = await chrome.scripting.executeScript({
    target: { tabId }, world: "MAIN", func,
  });
  return done[0]?.result ?? null;
}

async function waitForLoad(tabId) {
  for (let attempt = 0; attempt < LOAD_ATTEMPTS; attempt++) {
    const current = await chrome.tabs.get(tabId);
    if (current.status === "complete") {
      if (current.url?.startsWith("https://x.com/i/flow/login"))
        return { state: "waiting_login" };
      if (!current.url?.startsWith(SEARCH_PREFIX)) return { state: "source_error" };
      return { state: "ready" };
    }
    await delay(500);
  }
  return { state: "waiting_browser" };
}

async function harvest(tabId, limit) {
  const pages = [];
  const asked = new Set();
  let retained = 0;
  let items = 0;
  let idle = 0;
  let promoted = false;
  let duplicates = 0;
  let reason = "timeout";
  const deadline = Date.now() + DEADLINE_MS;

  while (Date.now() < deadline) {
    const captured = await inMain(tabId, () =>
      window.__personaStudioTakeSearch?.() || null);
    if (!captured || typeof captured !== "object") {
      idle += 1;
      // Scrolled every idle round, not just the first: X only fills the page
      // while something keeps asking it to, and a single scroll at the top of a
      // long wait leaves the rest of the wait with nothing to load from.
      await chrome.scripting.executeScript({
        target: { tabId }, world: "ISOLATED",
        func: () => window.scrollTo(0, document.documentElement.scrollHeight),
      });
      // X stops loading more into a tab it never shows. Rather than stall, the
      // tab is raised once -- and the read records that it borrowed the
      // foreground, because the studio was working in the user's browser.
      if (idle === IDLE_ROUNDS && !promoted) {
        promoted = true;
        await chrome.tabs.update(tabId, { active: true }).catch(() => {});
      }
      if (idle >= STOP_ROUNDS) break;
      await delay(POLL_MS);
      continue;
    }
    idle = 0;
    const size = Number(captured.bytes) || 0;
    const count = countItems(captured.payload);
    // Emptiness is checked before the duplicate guard, and it has to be: the
    // page that ends a query echoes back the cursor it was sent, which is the
    // cursor of the page before it, so the guard would read the ending as a
    // repeat. The ending page is kept -- `merge_pages` reads completeness off
    // the last page, so the last page has to be the empty one that was seen.
    if (count === 0) {
      if (retained + size > RETENTION_BYTES) reason = "buffer_full";
      else {
        pages.push(captured.payload);
        retained += size;
        reason = "exhausted";
      }
      break;
    }
    // The cursor travels in the request, so the same cursor twice is the same
    // page twice: it adds nothing and means X has stopped advancing.
    const cursor = captured.cursor_sent === null ? "" : String(captured.cursor_sent);
    const key = `${captured.raw_query}|${cursor}`;
    if (asked.has(key)) {
      duplicates += 1;
      if (duplicates >= 3) {
        reason = "no_progress";
        break;
      }
      continue;
    }
    asked.add(key);
    if (retained + size > RETENTION_BYTES) {
      reason = "buffer_full";
      break;
    }
    pages.push(captured.payload);
    retained += size;
    items += count;
    if (items >= limit) {
      reason = "limit";
      break;
    }
    const current = await chrome.tabs.get(tabId).catch(() => null);
    if (!current || !current.url?.startsWith(SEARCH_PREFIX)) {
      reason = "timeout";
      break;
    }
    await chrome.scripting.executeScript({
      target: { tabId }, world: "ISOLATED",
      func: () => window.scrollTo(0, document.documentElement.scrollHeight),
    });
    await delay(POLL_MS);
  }

  const counts = await inMain(tabId, () =>
    window.__personaStudioSearchCounts?.() || null);
  // A page the collector had to drop is a hole in the middle of the read: the
  // read cannot claim to have reached the end of anything.
  if (counts && Number(counts.dropped) > 0) reason = "capture_overflow";
  return { pages, reason, promoted };
}

async function readSearch(url, account, limit) {
  let tab;
  try {
    // A tab of its own per read, never a reused one: the collector keeps state
    // in the page, and a re-run has to start from an unscrolled timeline.
    tab = await chrome.tabs.create({ url, active: false });
    const loaded = await waitForLoad(tab.id);
    if (loaded.state !== "ready") return loaded;
    const identity = await chrome.scripting.executeScript({
      target: { tabId: tab.id }, world: "ISOLATED",
      func: readPersonaPage, args: [{ kind: "identity" }, account],
    });
    const status = identity[0]?.result || { state: "waiting_browser" };
    if (status.state !== "ready") return status;
    const read = await harvest(tab.id, limit);
    if (!read.pages.length) return { state: "source_error" };
    return {
      state: "ready", account, stopped_reason: read.reason,
      promoted: read.promoted, pages: read.pages,
    };
  } catch {
    return { state: "waiting_browser" };
  } finally {
    if (tab?.id) await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

export async function operateSearch(action) {
  const account = String(action.account || "");
  const handle = String(action.handle || "").replace(/^@/, "");
  const limit = Number(action.limit ?? MAX_LIMIT);
  const url = String(action.url || "");
  if (!/^[A-Za-z0-9_]{1,15}$/.test(account) ||
      !/^[A-Za-z0-9_]{1,15}$/.test(handle) ||
      !Number.isInteger(limit) || limit < MIN_LIMIT || limit > MAX_LIMIT ||
      !url.startsWith(`${SEARCH_PREFIX}?`))
    return { state: "invalid_request" };
  const read = await readSearch(url, account, limit);
  return read.state === "ready" ? { ...read, handle } : read;
}
