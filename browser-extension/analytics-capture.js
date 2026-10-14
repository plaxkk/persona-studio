// Runs in X's MAIN world before its app starts. It retains only Analytics
// business responses, never cookies, request headers or authorization data.
(() => {
  const pageKind = location.pathname === "/i/account_analytics/content" ? "content" :
    location.pathname === "/i/account_analytics/overview" ? "overview" : null;
  if (location.origin !== "https://x.com" ||
      !pageKind ||
      window.__personaStudioTakeAnalytics) return;

  const pending = [];
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const MAX_RESPONSE_BYTES = 8_000_000;
  const MAX_AGE_MS = 90_000;

  const take = () => {
    const cutoff = Date.now() - MAX_AGE_MS;
    while (pending.length && pending[0].captured_at_ms < cutoff) pending.shift();
    return pending.shift() || null;
  };
  Object.defineProperty(window, "__personaStudioTakeAnalytics", {
    value: take, configurable: false, writable: false,
  });

  const queryFor = (method, url) => {
    try {
      const target = new URL(String(url), location.href);
      if (["GET", "POST"].includes(String(method).toUpperCase()) &&
          target.origin === location.origin &&
          /^\/i\/api\/graphql\/[^/]+\/(contentPageQuery|accountOverviewDailyQuery)$/.test(target.pathname)) {
        const queryKind = target.pathname.endsWith("/contentPageQuery") ? "content" : "overview";
        if (queryKind !== pageKind) return null;
        const variables = JSON.parse(target.searchParams.get("variables") || "{}");
        if (pageKind === "content" && variables && typeof variables === "object" &&
            variables.from_time != null && variables.to_time != null &&
            Number.isInteger(variables.max_results) &&
            variables.max_results >= 1 && variables.max_results <= 10_000) {
          return {
            query_kind: "content",
            from_time: String(variables.from_time),
            to_time: String(variables.to_time),
            max_results: variables.max_results,
          };
        } else if (pageKind === "overview" && variables && typeof variables === "object" &&
                   ["current_from_iso", "current_to_iso", "prev_from_iso", "prev_to_iso"]
                     .every((key) => typeof variables[key] === "string")) {
          return {
            query_kind: "overview",
            from_time: variables.current_from_iso,
            to_time: variables.current_to_iso,
            prev_from: variables.prev_from_iso,
            prev_to: variables.prev_to_iso,
          };
        }
      }
    } catch { /* Other X requests are outside the Analytics collector. */ }
    return null;
  };

  const capture = (query, text) => {
    try {
      if (typeof text !== "string" ||
          new TextEncoder().encode(text).length > MAX_RESPONSE_BYTES) return;
      const response = JSON.parse(text);
      if (!response || typeof response.data !== "object" || !response.data) return;
      pending.push({ ...query, observed_at: Math.floor(Date.now() / 1000),
        captured_at_ms: Date.now(), payload: { data: response.data } });
      while (pending.length > 2) pending.shift();
    } catch { /* Changed or non-JSON X responses are not treated as data. */ }
  };

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__personaStudioAnalyticsQuery = queryFor(method, url);
    return originalOpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function (...args) {
    const query = this.__personaStudioAnalyticsQuery;
    if (query) this.addEventListener("loadend", () => {
      if (this.status !== 200) return;
      capture(query, this.responseText);
    }, { once: true });
    return originalSend.apply(this, args);
  };

  if (typeof window.fetch === "function") {
    const originalFetch = window.fetch;
    window.fetch = function (input, init) {
      const query = queryFor(init?.method || input?.method || "GET",
        typeof input === "string" ? input : input?.url);
      const request = originalFetch.apply(this, arguments);
      if (!query) return request;
      return request.then((response) => {
        if (response.status === 200) {
          try {
            response.clone().text().then((text) => capture(query, text)).catch(() => {});
          } catch { /* A non-cloneable response is ignored. */ }
        }
        return response;
      });
    };
  }
})();
