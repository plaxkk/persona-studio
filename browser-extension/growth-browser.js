import { readPersonaPage } from "./persona-page.js";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const handleOK = (value) => /^[A-Za-z0-9_]{1,15}$/.test(value || "");

function readRewardPage() {
  if (location.origin !== "https://x.com" ||
      location.pathname !== "/i/jf/creators/original_content_rewards")
    return { state: "source_error" };
  const main = document.querySelector("main [data-testid='primaryColumn']") || document.querySelector("main");
  const text = main?.innerText?.trim() || "";
  if (!text) return { state: "waiting_browser" };
  if (!/reward|奖励|check back|稍后查看/i.test(text)) return { state: "source_error" };
  // X owns both wording and thresholds. Keep the visible text as evidence;
  // these labels are observations, not rules baked into the product.
  const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean).slice(0, 80);
  return { state: "ready", page_status: /check back later|稍后查看/i.test(text) ? "check_back_later" : "visible",
    period: null, lines, raw_text: text.slice(0, 12000) };
}

function readFollowerCards() {
  if (!/^\/[A-Za-z0-9_]{1,15}\/followers$/.test(location.pathname))
    return { state: "source_error" };
  const primary = document.querySelector("main [data-testid='primaryColumn']") || document.querySelector("main");
  if (!primary) return { state: "waiting_browser", hint: "no_main" };
  const cards = [...primary.querySelectorAll('[data-testid="UserCell"]')];
  if (!cards.length) {
    const empty = primary.querySelector('[data-testid="emptyState"]');
    const emptyText = empty?.innerText || "";
    if (/no followers|没有粉丝|暂无粉丝/i.test(emptyText))
      return { state: "ready", handles: [], empty: true };
    if (/something went wrong|try reloading|出了点问题|重新加载/i.test(primary.innerText))
      return { state: "source_error", hint: "x_page_error" };
    return { state: "waiting_browser", hint: "no_cards" };
  }
  const handles = cards.map((card) => {
    const link = [...card.querySelectorAll("a[href]")].find((node) =>
      /^\/[A-Za-z0-9_]{1,15}$/.test(node.getAttribute("href") || ""));
    const handle = link?.getAttribute("href")?.slice(1) || "";
    return { handle, verified: !!card.querySelector('[data-testid="icon-verified"]') };
  // executeScript serializes this function without its module-level closure.
  }).filter((item) => /^[A-Za-z0-9_]{1,15}$/.test(item.handle));
  if (!handles.length) return { state: "waiting_browser", hint: "card_links" };
  return { state: "ready", handles };
}

async function openAccountTab(account, url, path, foreground = false) {
  const tab = await chrome.tabs.create({ url, active: foreground });
  for (let n = 0; n < 40; n++) {
    const current = await chrome.tabs.get(tab.id);
    if (current.status === "complete") {
      if (current.url?.includes("/i/flow/login")) return { tab, error: "waiting_login" };
      if (!current.url?.startsWith(`https://x.com${path}`)) return { tab, error: "source_error" };
      const read = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "ISOLATED",
        func: readPersonaPage, args: [{ kind: "identity" }, account] });
      const state = read[0]?.result?.state;
      if (state === "ready") return { tab };
      if (state === "account_mismatch" || state === "waiting_login") return { tab, error: state };
    }
    if (n === 7 && !foreground) await chrome.tabs.update(tab.id, { active: true }).catch(() => {});
    await delay(500);
  }
  return { tab, error: "waiting_browser", hint: "identity" };
}

export async function operateGrowthRewards(action) {
  const account = String(action.account || "");
  if (!handleOK(account)) return { state: "invalid_request" };
  let tab;
  try {
    const opened = await openAccountTab(account,
      "https://x.com/i/jf/creators/original_content_rewards",
      "/i/jf/creators/original_content_rewards");
    tab = opened.tab;
    if (opened.error) return { state: opened.error };
    for (let n = 0; n < 30; n++) {
      const read = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "ISOLATED",
        func: readRewardPage });
      const result = read[0]?.result || { state: "waiting_browser" };
      if (result.state !== "waiting_browser") return { ...result, account };
      await delay(500);
    }
    return { state: "waiting_browser" };
  } catch { return { state: "waiting_browser" }; }
  finally { if (tab?.id) await chrome.tabs.remove(tab.id).catch(() => {}); }
}

export async function operateGrowthFollowers(action) {
  const account = String(action.account || "");
  if (!handleOK(account) || !Array.isArray(action.exclude) || action.exclude.length > 2000 ||
      action.exclude.some((item) => !handleOK(item))) return { state: "invalid_request" };
  let tab;
  let previous;
  try {
    previous = (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    const path = `/${account}/followers`;
    const found = new Map();
    // X may populate an already-open followers page while a new tab still has
    // an empty timeline. Read that page without navigating or scrolling it.
    const existingTabs = (await chrome.tabs.query({})).filter((candidate) => {
      try { return new URL(candidate.url).origin === "https://x.com" &&
        new URL(candidate.url).pathname === path; }
      catch { return false; }
    });
    for (const existing of existingTabs) {
      if (!existing.id) continue;
      const identity = await chrome.scripting.executeScript({ target: { tabId: existing.id }, world: "ISOLATED",
        func: readPersonaPage, args: [{ kind: "identity" }, account] }).catch(() => []);
      if (identity[0]?.result?.state === "ready") {
        const visible = await chrome.scripting.executeScript({ target: { tabId: existing.id }, world: "ISOLATED",
          func: readFollowerCards }).catch(() => []);
        if (visible[0]?.result?.state === "ready") {
          for (const item of visible[0].result.handles || []) found.set(item.handle.toLowerCase(), item);
          if (found.size) break;
        }
      }
    }
    const opened = await openAccountTab(account, `https://x.com${path}`, path, true);
    tab = opened.tab;
    if (opened.error) return { state: opened.error, hint: opened.hint || "open" };
    let stalled = 0;
    let empty = false;
    let lastHint = "no_cards";
    const usedExisting = found.size > 0;
    for (let n = 0; n < 45 && found.size < 180 && !usedExisting; n++) {
      const read = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "ISOLATED",
        func: readFollowerCards });
      const result = read[0]?.result || { state: "waiting_browser" };
      if (result.hint) lastHint = result.hint;
      if (result.state === "ready") {
        if (result.empty) { empty = true; break; }
        const before = found.size;
        for (const item of result.handles) found.set(item.handle.toLowerCase(), item);
        stalled = before === found.size ? stalled + 1 : 0;
      } else if (result.state !== "waiting_browser") return result;
      if (n === 4 && !found.size)
        await chrome.tabs.update(tab.id, { active: true }).catch(() => {});
      if (stalled >= 3) break;
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "ISOLATED",
        func: () => window.scrollBy(0, Math.max(window.innerHeight * 2, 900)) });
      await delay(700);
    }
    if (!found.size && !empty) return { state: "waiting_browser", hint: lastHint };
    const excluded = new Set(action.exclude.map((item) => item.toLowerCase()));
    const candidates = [...found.values()].filter((item) => !excluded.has(item.handle.toLowerCase())).slice(0, 12);
    const records = [];
    for (const item of candidates) {
      const path = `/${item.handle}`;
      await chrome.tabs.update(tab.id, { url: `https://x.com${path}` });
      let profile = null;
      for (let n = 0; n < 30; n++) {
        const current = await chrome.tabs.get(tab.id);
        if (current.status === "complete" && current.url?.startsWith(`https://x.com${path}`)) {
          const read = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "ISOLATED",
            func: readPersonaPage, args: [{ kind: "account_snapshot", handle: item.handle }, account] });
          const data = read[0]?.result;
          if (data?.state === "ready" && data.handle.toLowerCase() === item.handle.toLowerCase()) {
            profile = data.record; break;
          }
          if (data?.state && !["waiting_browser"].includes(data.state)) break;
        }
        await delay(400);
      }
      records.push({ handle: item.handle, verified: item.verified,
        followers: profile?.followers ?? null, followers_display: profile?.followers_display || "" });
    }
    return { state: "ready", account, discovered: found.size, records,
      stopped_reason: empty ? "explicit_empty" : stalled >= 3 ? "scroll_stalled" : "bounded_sample" };
  } catch { return { state: "waiting_browser" }; }
  finally {
    if (tab?.id) {
      if (previous?.id && previous.id !== tab.id) {
        const current = await chrome.tabs.query({ active: true, currentWindow: true }).catch(() => []);
        if (current[0]?.id === tab.id)
          await chrome.tabs.update(previous.id, { active: true }).catch(() => {});
      }
      await chrome.tabs.remove(tab.id).catch(() => {});
    }
  }
}
