// Executes only in Chrome's isolated world, never receives the device token.
// No selectors, Javascript or URLs supplied by the model are evaluated here.
export async function readPersonaPage(action, expected) {
  if (location.origin !== "https://x.com") return { state: "waiting_browser" };
  const primary = document.querySelector("main");
  const ownLink = document.querySelector(
    'a[data-testid="AppTabBar_Profile_Link"]',
  );
  const account = ownLink
    ?.getAttribute("href")
    ?.match(/^\/([A-Za-z0-9_]{1,15})$/)?.[1];
  if (!account)
    return {
      state: /\/account\/access|\/i\/flow\/(challenge|verify)/.test(
        location.pathname,
      )
        ? "challenge"
        : document.querySelector(
              'a[href="/login"],a[href="/i/flow/login"],input[autocomplete="username"]',
            ) || location.pathname.includes("/i/flow/login")
          ? "waiting_login"
          : "waiting_browser",
    };
  if (account.toLowerCase() !== expected.toLowerCase())
    return { state: "account_mismatch", account };
  if (!primary) return { state: "waiting_browser" };
  if (action.kind === "identity") return { state: "ready", account };
  const prior = globalThis.__personaTargets;
  if (["expand", "original", "detail"].includes(action.kind)) {
    const t = prior?.targets?.get(action.target);
    if (
      !t ||
      prior.url !== location.href ||
      !t.element.isConnected ||
      t.element.outerHTML !== t.signature ||
      t.kind !== action.kind
    )
      return {
        state: "ready",
        account,
        snapshot: "目标已失效，请重新读取快照。",
        records: [],
        targets: [],
      };
    if (action.kind === "detail")
      return {
        state: "ready",
        account,
        navigate: t.url,
        records: [],
        targets: [],
      };
    t.element.click();
    await new Promise((r) => setTimeout(r, 1200));
  }
  if (action.kind === "scroll") {
    window.scrollBy(0, Math.max(400, innerHeight * 0.8));
    await new Promise((r) => setTimeout(r, 1700));
  }
  const targets = new Map();
  const visible = [];
  const nonce = crypto.randomUUID();
  function target(element, kind, url = "") {
    const id = nonce + ":" + targets.size;
    targets.set(id, { element, kind, url, signature: element.outerHTML });
    visible.push({ id, kind, label: element.innerText?.slice(0, 80) || kind });
  }
  const records = [];
  for (const article of primary.querySelectorAll(
    'article[data-testid="tweet"]',
  )) {
    const time = article.querySelector("time");
    const link = time?.closest("a");
    const match = link
      ?.getAttribute("href")
      ?.match(/^\/([A-Za-z0-9_]+)\/status\/(\d+)$/);
    if (!match) continue;
    const author = match[1],
      id = match[2];
    if (author.toLowerCase() !== account.toLowerCase()) continue;
    const textEl = article.querySelector('[data-testid="tweetText"]');
    // Quoted tweet text is not the author's commentary. Only text before first
    // nested quoted card is eligible; quote/context is stored separately.
    const quote = article.querySelector(
      '[data-testid="quoteTweet"],div[role="link"][tabindex="0"]',
    );
    const ownText =
      textEl && !(quote && quote.contains(textEl)) ? textEl.innerText : "";
    const social =
      article.querySelector('[data-testid="socialContext"]')?.innerText || "";
    const truncated = article.querySelector(
      '[data-testid="tweet-text-show-more-link"]',
    );
    if (truncated) target(truncated, "expand");
    const original = [
      ...article.querySelectorAll('button,[role="button"]'),
    ].find((e) =>
      /^(Show original|显示原文|查看原文)$/.test(e.innerText.trim()),
    );
    if (original) target(original, "original");
    target(
      link,
      "detail",
      new URL(link.getAttribute("href"), location.origin).href,
    );
    const before = textEl ? article.innerText.split(textEl.innerText)[0] : "";
    const reply = /Replying to|回复\s?@|回复给|正在回复/.test(before);
    records.push({
      id,
      author,
      text: ownText,
      kind: reply ? "reply" : "post",
      created_at: time.dateTime,
      context: quote?.innerText?.slice(0, 4000) || "",
      truncated: !!truncated,
      translated:
        !!original || /Translated from|翻译自|由.*翻译/.test(article.innerText),
      repost: /reposted|转帖|转推/.test(social),
      advertisement:
        !!article.querySelector('[data-testid="placementTracking"]') ||
        /^(Ad|广告|Promoted)$/m.test(article.innerText),
    });
  }
  globalThis.__personaTargets = { url: location.href, targets };
  const onProfile = new RegExp(
    "^/" + expected + "(?:/with_replies)?/?$",
    "i",
  ).test(location.pathname);
  const profile = onProfile
    ? {
        name:
          primary.querySelector('[data-testid="UserName"]')?.innerText || "",
        bio:
          primary.querySelector('[data-testid="UserDescription"]')?.innerText ||
          "",
        location:
          primary.querySelector('[data-testid="UserLocation"]')?.innerText ||
          "",
        joined:
          primary.querySelector('[data-testid="UserJoinDate"]')?.innerText ||
          "",
        url: "https://x.com/" + expected,
      }
    : undefined;
  return {
    state: "ready",
    account,
    profile,
    records,
    targets: visible,
    snapshot: JSON.stringify({
      page: location.pathname,
      profile,
      records,
    }).slice(0, 20000),
  };
}
