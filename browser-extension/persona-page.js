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
  // §4.11 needs the *engager's* profile, which is somebody else's page. This is
  // the one read in this file that is not about the signed-in account, so it
  // runs before the mismatch branch — but only that branch is skipped. The
  // nav-bar link above is still required, because it is the proof that a
  // session is open: signed out, X serves the logged-out shell, whose "profile"
  // is a login prompt and whose numbers are all absent.
  if (action.kind === "account_snapshot") {
    const target = String(action.handle || "").replace(/^@/, "");
    if (!/^[A-Za-z0-9_]{1,15}$/.test(target)) return { state: "invalid_request" };
    if (location.pathname.toLowerCase() !== `/${target.toLowerCase()}`)
      return { state: "source_error" };
    if (!primary) return { state: "waiting_browser" };
    // X writes hrefs with the account's own capitalisation, so the header links
    // are matched case-insensitively instead of by a selector built from the
    // lowercased handle; it has to be the target's own link, not a follower
    // card's, or a small account's followers number would be read as its own.
    const link = (suffix) =>
      [...primary.querySelectorAll('a[href$="/followers"],a[href$="/verified_followers"],a[href$="/following"]')].find((node) => {
        const match = node
          .getAttribute("href")
          ?.match(/^\/([A-Za-z0-9_]{1,15})\/(?:verified_)?(followers|following)$/);
        return match && match[1].toLowerCase() === target.toLowerCase() && match[2] === suffix;
      });
    const followersLink = link("followers");
    if (!followersLink) return { state: "waiting_browser" };
    // Abbreviated values such as 1.2K are unknown, never rounded into a count.
    const count = (node, words) => {
      const label = node?.getAttribute("aria-label") || node?.innerText || "";
      const match = label.match(new RegExp(`(?:^|\\s)([\\d,]+)\\s*(?:${words})`, "i"));
      return match ? Number(match[1].replaceAll(",", "")) : null;
    };
    const avatar = primary.querySelector(
      '[data-testid^="UserAvatar-Container-"] img',
    );
    return {
      state: "ready",
      account,
      handle: target,
      page: location.pathname,
      record: {
        name: primary.querySelector('[data-testid="UserName"]')?.innerText || "",
        bio: primary.querySelector('[data-testid="UserDescription"]')?.innerText || "",
        location: primary.querySelector('[data-testid="UserLocation"]')?.innerText || "",
        url: primary.querySelector('[data-testid="UserUrl"]')?.innerText || "",
        joined: primary.querySelector('[data-testid="UserJoinDate"]')?.innerText || "",
        followers: count(followersLink, "Followers|粉丝"),
        // Keep X's displayed abbreviation alongside the unknown exact count.
        // "48K" is useful context, but must never become a fabricated 48,000.
        followers_display: followersLink.innerText.trim(),
        following: count(link("following"), "Following|正在关注|关注"),
        // The header carries no post total. Reporting the field as absent, with
        // why, keeps it from being read later as "this account has 0 posts".
        posts_count: null,
        posts_count_reason: "主页头图区不显示帖子总数",
        has_avatar: !!avatar,
        default_avatar: /default_profile_images/.test(
          avatar?.getAttribute("src") || "",
        ),
        has_banner: !!primary.querySelector('a[href$="/header_photo"]'),
        verified: !!primary.querySelector('[data-testid="icon-verified"]'),
      },
    };
  }
  if (account.toLowerCase() !== expected.toLowerCase())
    return { state: "account_mismatch", account };
  if (!primary) return { state: "waiting_browser" };
  if (action.kind === "identity") return { state: "ready", account };
  if (action.kind === "metric_snapshot") {
    if (!/^[A-Za-z0-9_]{1,15}$/.test(action.author || "") || !/^\d{1,30}$/.test(action.post_id || ""))
      return { state: "invalid_request" };
    if (location.pathname.toLowerCase() !== `/${action.author.toLowerCase()}/status/${action.post_id}`)
      return { state: "source_error" };
    const path = `/${action.author.toLowerCase()}/status/${action.post_id}`;
    const article = [...primary.querySelectorAll('article[data-testid="tweet"]')].find((item) =>
      [...item.querySelectorAll('time')].some((node) => !node.closest('[data-testid="quoteTweet"]') &&
        node.closest('a')?.getAttribute('href')?.toLowerCase() === path));
    if (!article) return { state: "waiting_browser" };
    const time = [...article.querySelectorAll('time')].find((node) =>
      !node.closest('[data-testid="quoteTweet"]') && node.closest('a')?.getAttribute('href')?.toLowerCase() === path);
    const group = [...article.querySelectorAll('[role="group"]')].find((node) => !node.closest('[data-testid="quoteTweet"]'));
    const labels = [group?.getAttribute('aria-label') || '', ...[...article.querySelectorAll('[aria-label]')]
      .filter((node) => !node.closest('[data-testid="quoteTweet"]'))
      .map((node) => node.getAttribute('aria-label') || '')].join(' · ');
    const count = (en, zh) => {
      const a = labels.match(new RegExp(`(?:^|[^\\d])([\\d,]+)\\s+${en}\\b`, 'i'));
      const b = labels.match(new RegExp(`${zh}\\s*([\\d,]+)(?![\\d.,万千亿KkMm])`));
      const c = labels.match(new RegExp(`(?:^|[^\\d.])([\\d,]+)\\s*${zh}`));
      const value = (a || b || c)?.[1]?.replaceAll(',', '');
      return value && /^\d+$/.test(value) ? Number(value) : null;
    };
    const ownText = [...article.querySelectorAll('[data-testid="tweetText"]')]
      .find((node) => !node.closest('[data-testid="quoteTweet"]'));
    const quoteNode = article.querySelector('[data-testid="quoteTweet"]');
    const before = article.innerText.split(ownText?.innerText || quoteNode?.innerText || '\uffff')[0] || '';
    const kind = /Replying to|回复\s?@|回复给|正在回复/.test(before) ? 'reply' :
      quoteNode ? 'quote' : 'post';
    return { state: 'ready', account, post_id: action.post_id, author: action.author,
      record: { created_at: time?.dateTime || '', kind,
        views: count('views?', '次查看|次浏览|浏览'),
        likes: count('likes?', '喜欢|点赞'),
        replies: count('repl(?:y|ies)', '回复'),
        reposts: count('reposts?', '转发|转帖'),
        quotes: count('quotes?', '引用'),
        bookmarks: count('bookmarks?', '书签') } };
  }
  if (action.kind === "profile_snapshot") {
    if (location.pathname.toLowerCase() !== `/${account.toLowerCase()}`)
      return { state: 'source_error' };
    const followersLink = primary.querySelector(`a[href="/${account}/followers"],a[href="/${account}/verified_followers"]`);
    if (!followersLink) return { state: 'waiting_browser' };
    // Abbreviated values such as 1.2K are unknown, never rounded into a daily count.
    const label = followersLink.getAttribute('aria-label') || followersLink.innerText || '';
    const match = label.match(/(?:^|\s)([\d,]+)\s*(?:Followers|粉丝)/i);
    const followers = match ? Number(match[1].replaceAll(',', '')) : null;
    const pinned = [...primary.querySelectorAll('article[data-testid="tweet"]')].find((article) =>
      /^(Pinned|已置顶|置顶)$/m.test(article.querySelector('[data-testid="socialContext"]')?.innerText || ''));
    const pinned_tweet_id = pinned?.querySelector('time')?.closest('a')?.getAttribute('href')?.match(/\/status\/(\d+)/)?.[1] || null;
    return { state: 'ready', account, record: { followers, pinned_tweet_id,
      bio: primary.querySelector('[data-testid="UserDescription"]')?.innerText || '' } };
  }
  if (action.kind === "sync_snapshot" && /Something went wrong|Try reloading|出了点问题|重新加载/.test(primary.innerText))
    return { state: "source_error" };
  if (action.kind === "sync_snapshot" && !primary.querySelector('article[data-testid="tweet"]')) {
    // A still-loading timeline must never be reported as a successful empty read.
    const empty = primary.querySelector('[data-testid="emptyState"]');
    const emptyMessage = /No results for|No results found|No posts yet|没有找到结果|暂无帖子/.test(primary.innerText);
    if (primary.querySelector('[role="progressbar"]') || (!empty && !emptyMessage))
      return { state: "waiting_browser" };
  }
  if (action.kind === "sync_detail") {
    if (!/^[A-Za-z0-9_]{1,15}$/.test(action.author || "") || !/^\d+$/.test(action.post_id || ""))
      return { state: "invalid_request" };
    if (location.pathname.toLowerCase() !== `/${action.author.toLowerCase()}/status/${action.post_id}`)
      return { state: "waiting_browser" };
    const articles = [...primary.querySelectorAll('article[data-testid="tweet"]')];
    const key = `/${action.author.toLowerCase()}/status/${action.post_id}`;
    const index = articles.findIndex((article) =>
      [...article.querySelectorAll("time")].some((node) =>
        !node.closest('[data-testid="quoteTweet"]') &&
        node.closest("a")?.getAttribute("href")?.toLowerCase() === key));
    if (index < 0) return { state: "waiting_browser" };
    const article = articles[index];
    const expand = article.querySelector('[data-testid="tweet-text-show-more-link"]');
    if (expand) {
      expand.click();
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    function extract(node, quoted = false) {
      const text = [...node.querySelectorAll('[data-testid="tweetText"]')]
        .find((element) => quoted || !element.closest('[data-testid="quoteTweet"]'))?.innerText || "";
      const href = [...node.querySelectorAll("time")]
        .find((element) => quoted || !element.closest('[data-testid="quoteTweet"]'))
        ?.closest("a")?.getAttribute("href") || "";
      const match = href.match(/^\/([A-Za-z0-9_]+)\/status\/(\d+)$/);
      return { text: text.slice(0, 10000), author: match?.[1] || "",
        id: match?.[2] || "", url: match ? `https://x.com${href}` : "" };
    }
    const main = extract(article);
    if (!main.text || main.id !== action.post_id || main.author.toLowerCase() !== action.author.toLowerCase())
      return { state: "source_error" };
    const quoteNode = article.querySelector('[data-testid="quoteTweet"]');
    const quote = quoteNode ? extract(quoteNode, true) : null;
    const preface = article.innerText.split(main.text)[0] || "";
    const parentNeeded = /Replying to|回复\s?@|回复给|正在回复/.test(preface);
    const parent = parentNeeded && index > 0 ? extract(articles[index - 1]) : null;
    const targetAuthor = preface.match(/(?:Replying to|回复\s?给?|正在回复)\s*@([A-Za-z0-9_]+)/i)?.[1];
    const verifiedParent = parent && (!targetAuthor || parent.author.toLowerCase() === targetAuthor.toLowerCase()) ? parent : null;
    function mediaFor(node, source) {
      return [...node.querySelectorAll('[data-testid="tweetPhoto"] img, video')]
        .map((element) => ({ kind: element.tagName.toLowerCase(), source,
          description: (element.getAttribute("alt") || element.getAttribute("aria-label") || "").slice(0, 500) }));
    }
    const media = [...mediaFor(article, "original"),
      ...(verifiedParent && index > 0 ? mediaFor(articles[index - 1], "parent") : [])];
    return { state: "ready", account, post_id: action.post_id, author: action.author,
      record: { ...main, created_at: article.querySelector("time")?.dateTime || "",
        parent_needed: parentNeeded, parent: verifiedParent?.text ? verifiedParent : null,
        quote_needed: !!quoteNode, quote: quote?.text ? quote : null,
        media, media_pending: media.length > 0,
        truncated: !!article.querySelector('[data-testid="tweet-text-show-more-link"]'),
        browser_context: preface.slice(0, 2000) } };
  }
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
    if (action.kind !== "sync_snapshot" && author.toLowerCase() !== account.toLowerCase()) continue;
    if (action.kind === "sync_snapshot" && action.source === "own" && author.toLowerCase() !== account.toLowerCase()) continue;
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
      media_pending: !!article.querySelector('[data-testid="tweetPhoto"],video'),
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
  if (action.kind === "sync_snapshot") return {
    state: "ready", account, page: location.pathname + location.search,
    selected_tab: primary.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim() || "",
    records: records.slice(0, 30),
  };
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
