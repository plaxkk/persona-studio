import { readPersonaPage } from "./persona-page.js";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const SOURCES = new Set(["own", "mentions", "replies", "quotes", "notifications", "timeline"]);
const TAB_KEY = "persona-sync-tab";

async function read(tabId, account, action) {
  const result = await chrome.scripting.executeScript({
    target: { tabId }, world: "ISOLATED", func: readPersonaPage,
    args: [action, account],
  });
  return result[0]?.result || { state: "waiting_browser" };
}

async function ready(tabId, account, action, expectedUrl = "") {
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const tab = await chrome.tabs.get(tabId);
      // tabs.update can return while Chrome still reports the previous page as
      // complete. Never inspect its DOM as if it belonged to the new route.
      if (tab.status === "complete" && (!expectedUrl || tab.url === expectedUrl)) {
        const result = await read(tabId, account, action);
        if (result.state !== "waiting_browser") return result;
      }
    } catch {
      return { state: "waiting_browser" };
    }
    await delay(500);
  }
  return { state: "waiting_browser" };
}

async function clickRoute(tabId, pathname, postOnly = false) {
  try {
    const result = await chrome.scripting.executeScript({
      target: { tabId }, world: "ISOLATED",
      func: (path, onlyPost) => {
        const links = onlyPost
          ? [...document.querySelectorAll('main article[data-testid="tweet"] time')]
              .map((time) => time.closest("a"))
          : [...document.querySelectorAll('a[href]')];
        const link = links.find((node) => node?.getAttribute("href")?.toLowerCase() === path.toLowerCase());
        if (!link) return false;
        link.click();
        return true;
      },
      args: [pathname, postOnly],
    });
    return Boolean(result[0]?.result);
  } catch {
    return false;
  }
}

function sourceUrl(source, account) {
  const query = {
    mentions: `@${account} -from:${account}`,
    replies: `to:${account} -from:${account}`,
    quotes: `url:x.com/${account}/status -from:${account}`,
  }[source];
  if (query) return `https://x.com/search?q=${encodeURIComponent(query)}&src=typed_query&f=live`;
  if (source === "own") return `https://x.com/${account}`;
  if (source === "notifications") return "https://x.com/notifications/mentions";
  return "https://x.com/home";
}

export async function operateSync(action) {
  const account = String(action.account || "");
  const detail = action.kind === "context_read";
  if (!/^[A-Za-z0-9_]{1,15}$/.test(account) ||
      (detail ? !/^[A-Za-z0-9_]{1,15}$/.test(action.author || "") || !/^\d+$/.test(action.post_id || "") : !SOURCES.has(action.source)))
    return { state: "invalid_request" };
  const saved = (await chrome.storage.session.get(TAB_KEY))[TAB_KEY];
  let tab;
  if (saved?.tab) {
    try { tab = await chrome.tabs.get(saved.tab); } catch { /* Create a fresh read tab. */ }
  }
  if (tab && !tab.url?.startsWith("https://x.com/")) return { state: "waiting_browser" };
  if (!tab) {
    tab = await chrome.tabs.create({ url: "https://x.com/home", active: false });
    await chrome.storage.session.set({ [TAB_KEY]: { tab: tab.id } });
  }
  let result = await ready(tab.id, account, { kind: "identity" });
  if (result.state !== "ready") return result;
  if (detail) {
    const url = `https://x.com/${action.author}/status/${action.post_id}`;
    // X can leave a direct reload on its Loading placeholder even when the
    // same post opens normally through a link in its already-loaded app.
    let opened = await clickRoute(tab.id, new URL(url).pathname, true);
    if (!opened) {
      const current = await chrome.tabs.get(tab.id);
      if (current.url !== url && new URL(current.url).pathname !== "/home" && await clickRoute(tab.id, "/home")) {
        const home = await ready(tab.id, account, { kind: "identity" }, "https://x.com/home");
        if (home.state === "ready") opened = await clickRoute(tab.id, new URL(url).pathname, true);
      }
    }
    if (!opened && (await chrome.tabs.get(tab.id)).url !== url) await chrome.tabs.update(tab.id, { url });
    result = await ready(tab.id, account, { kind: "sync_detail", author: action.author, post_id: action.post_id }, url);
    if (result.state !== "ready" || result.post_id !== action.post_id || String(result.author || "").toLowerCase() !== action.author.toLowerCase())
      return { state: result.state === "ready" ? "source_error" : result.state };
    return result;
  }
  const url = sourceUrl(action.source, account);
  if (tab.url !== url) {
    await chrome.tabs.update(tab.id, { url });
  }
  if (action.source === "timeline") {
    // The home page defaults to For you. The Following tab is the requested source.
    const identity = await ready(tab.id, account, { kind: "identity" }, url);
    if (identity.state !== "ready") return identity;
    let selected = false;
    for (let attempt = 0; attempt < 12 && !selected; attempt++) {
      const tabs = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "ISOLATED",
        func: () => {
          const candidate = [...document.querySelectorAll('main [role="tab"]')]
            .find((node) => /^(Following|正在关注|关注)$/.test(node.textContent?.trim() || ""));
          if (!candidate) return false;
          if (candidate.getAttribute("aria-selected") !== "true") candidate.click();
          return true;
        },
      });
      selected = Boolean(tabs[0]?.result);
      if (!selected) await delay(400);
    }
    if (!selected) return { state: "source_error" };
  }
  result = await ready(tab.id, account, { kind: "sync_snapshot", source: action.source }, url);
  if (result.state !== "ready") return result;
  // A stale page, redirect, or search mismatch cannot be called a successful read.
  const page = String(result.page || "");
  if (action.source === "own" && page.split("?")[0].toLowerCase() !== `/${account.toLowerCase()}`)
    return { state: "source_error" };
  if (["mentions", "replies", "quotes"].includes(action.source)) {
    const actual = new URL(page, "https://x.com");
    const expected = new URL(url);
    if (actual.pathname !== "/search" || actual.searchParams.get("q") !== expected.searchParams.get("q") || actual.searchParams.get("f") !== "live")
      return { state: "source_error" };
  }
  if (action.source === "notifications" && !page.startsWith("/notifications/mentions"))
    return { state: "source_error" };
  if (action.source === "timeline" && (page.split("?")[0] !== "/home" || !/^(Following|正在关注|关注)$/.test(result.selected_tab)))
    return { state: "source_error" };
  return result;
}
