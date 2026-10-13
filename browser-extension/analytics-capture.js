// Runs in X's MAIN world before its app starts. It retains only Content
// business responses, never cookies, request headers or authorization data.
(() => {
  if (location.origin !== "https://x.com" ||
      location.pathname !== "/i/account_analytics/content" ||
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

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__personaStudioContentUrl = null;
    try {
      const target = new URL(String(url), location.href);
      if (["GET", "POST"].includes(String(method).toUpperCase()) &&
          target.origin === location.origin &&
          /^\/i\/api\/graphql\/[^/]+\/contentPageQuery$/.test(target.pathname)) {
        const variables = JSON.parse(target.searchParams.get("variables") || "{}");
        if (variables && typeof variables === "object" &&
            variables.from_time != null && variables.to_time != null &&
            Number.isInteger(variables.max_results) &&
            variables.max_results >= 1 && variables.max_results <= 10_000) {
          this.__personaStudioContentUrl = {
            from_time: String(variables.from_time),
            to_time: String(variables.to_time),
            max_results: variables.max_results,
          };
        }
      }
    } catch { /* Other X requests are outside the Content collector. */ }
    return originalOpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function (...args) {
    const query = this.__personaStudioContentUrl;
    if (query) this.addEventListener("loadend", () => {
      if (this.status !== 200) return;
      try {
        const text = this.responseText;
        if (new TextEncoder().encode(text).length > MAX_RESPONSE_BYTES) return;
        const response = JSON.parse(text);
        if (!response || typeof response.data !== "object" || !response.data) return;
        pending.push({ ...query, observed_at: Math.floor(Date.now() / 1000),
          captured_at_ms: Date.now(), payload: { data: response.data } });
        while (pending.length > 2) pending.shift();
      } catch { /* Changed or non-JSON X responses are not treated as data. */ }
    }, { once: true });
    return originalSend.apply(this, args);
  };
})();
