import { readPersonaPage } from "./persona-page.js";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const DAYS = new Set([7, 14, 28, 90]);

function validDateRange(from, to) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from || "") ||
      !/^\d{4}-\d{2}-\d{2}$/.test(to || "")) return false;
  const start = Date.parse(`${from}T00:00:00.000Z`);
  const end = Date.parse(`${to}T00:00:00.000Z`);
  return Number.isFinite(start) && Number.isFinite(end) &&
    new Date(start).toISOString().slice(0, 10) === from &&
    new Date(end).toISOString().slice(0, 10) === to &&
    end >= start && end - start <= 30 * 86400000 &&
    end <= Date.now();
}

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

export function queryMatchesDates(record, from, to) {
  if (!validDateRange(from, to)) return false;
  const start = seconds(record?.from_time), end = seconds(record?.to_time);
  const expectedStart = Date.parse(`${from}T00:00:00.000Z`) / 1000;
  const expectedEnd = Date.parse(`${to}T00:00:00.000Z`) / 1000 + 86399.999;
  const currentDay = to === new Date().toISOString().slice(0, 10);
  const endMatches = currentDay
    ? end != null && end >= Date.parse(`${to}T00:00:00.000Z`) / 1000 && end <= expectedEnd + 60
    : end != null && Math.abs(end - expectedEnd) < 60;
  return start != null && end != null &&
    Math.abs(start - expectedStart) < 60 &&
    endMatches &&
    Number.isInteger(record?.max_results) && record.max_results >= 1 &&
    record.max_results <= 10_000;
}

export function queryMatchesOverview(record) {
  const from = seconds(record?.from_time), to = seconds(record?.to_time);
  const prevFrom = seconds(record?.prev_from), prevTo = seconds(record?.prev_to);
  return [from, to, prevFrom, prevTo].every(Number.isFinite) &&
    26 <= (to - from) / 86400 && (to - from) / 86400 <= 30 &&
    26 <= (prevTo - prevFrom) / 86400 &&
    (prevTo - prevFrom) / 86400 <= 30 && Math.abs(prevTo - from) < 60;
}

async function captureAnalytics(account, url, path, valid) {
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
        if (!current.url?.startsWith(`https://x.com${path}`))
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
            if (!valid(record))
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

export async function operateAnalyticsContent(action) {
  const account = String(action.account || "");
  const days = action.days;
  const custom = validDateRange(action.from_date, action.to_date);
  if (!/^[A-Za-z0-9_]{1,15}$/.test(account) ||
      !(DAYS.has(days) || (days === 0 && custom)))
    return { state: "invalid_request" };
  const range = custom ? `from=${action.from_date}&to=${action.to_date}` : `days=${days}`;
  const path = "/i/account_analytics/content";
  const url = `https://x.com${path}?type=posts&sort=impressions&dir=desc&${range}`;
  return captureAnalytics(account, url, path, (record) =>
    record.query_kind === "content" &&
    (custom ? queryMatchesDates(record, action.from_date, action.to_date)
            : queryMatchesDays(record, days)) &&
    Array.isArray(record.payload?.data?.viewer_v2?.user_results?.result?.tweets_results));
}

export async function operateAnalyticsOverview(action) {
  const account = String(action.account || "");
  if (!/^[A-Za-z0-9_]{1,15}$/.test(account) || action.days !== 28)
    return { state: "invalid_request" };
  const path = "/i/account_analytics/overview";
  return captureAnalytics(account, `https://x.com${path}`, path, (record) => {
    const root = record.payload?.data?.viewer_v2?.user_results?.result;
    return record.query_kind === "overview" && queryMatchesOverview(record) &&
      Array.isArray(root?.current_time_series) && Array.isArray(root?.previous_totals);
  });
}
