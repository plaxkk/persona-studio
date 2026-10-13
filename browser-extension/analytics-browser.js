import { readPersonaPage } from "./persona-page.js";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const DAYS = new Set([7, 14, 28, 90]);

function seconds(value) {
  const text = String(value || "");
  if (/^\d{10}$/.test(text)) return Number(text);
  if (/^\d{13}$/.test(text)) return Number(text) / 1000;
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed / 1000 : null;
}

export function queryMatchesDays(record, days) {
  const from = seconds(record?.from_time), to = seconds(record?.to_time);
  const span = from == null || to == null ? null : (to - from) / 86400;
  return Number.isFinite(span) && span >= days - 2 && span <= days + 2 &&
    Number.isInteger(record?.max_results) && record.max_results >= 1 &&
    record.max_results <= 10_000;
}

export async function operateAnalyticsContent(action) {
  const account = String(action.account || "");
  const days = action.days;
  if (!/^[A-Za-z0-9_]{1,15}$/.test(account) || !DAYS.has(days))
    return { state: "invalid_request" };
  const url = `https://x.com/i/account_analytics/content?type=posts&sort=impressions&dir=desc&days=${days}`;
  let tab;
  try {
    // A new tab gives every job its own page and captured request. We never
    // reuse a possibly cached response from a previous analytics session.
    tab = await chrome.tabs.create({ url, active: false });
    for (let attempt = 0; attempt < 70; attempt++) {
      const current = await chrome.tabs.get(tab.id);
      if (current.status === "complete") {
        if (current.url?.startsWith("https://x.com/i/flow/login"))
          return { state: "waiting_login" };
        if (!current.url?.startsWith("https://x.com/i/account_analytics/content"))
          return { state: "source_error" };
        const identity = await chrome.scripting.executeScript({
          target: { tabId: tab.id }, world: "ISOLATED",
          func: readPersonaPage, args: [{ kind: "identity" }, account],
        });
        const status = identity[0]?.result || { state: "waiting_browser" };
        if (status.state !== "waiting_browser" && status.state !== "ready")
          return status;
        if (status.state === "ready") {
          const captured = await chrome.scripting.executeScript({
            target: { tabId: tab.id }, world: "MAIN",
            func: () => window.__personaStudioTakeAnalytics?.() || null,
          });
          const record = captured[0]?.result;
          if (record) {
            if (!queryMatchesDays(record, days) ||
                !record.payload?.data?.viewer_v2?.user_results?.result?.tweets_results)
              return { state: "source_error" };
            return { state: "ready", account, record };
          }
        }
      }
      await delay(500);
    }
    return { state: "waiting_browser" };
  } catch {
    return { state: "waiting_browser" };
  } finally {
    if (tab?.id) await chrome.tabs.remove(tab.id).catch(() => {});
  }
}
