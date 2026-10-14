// Runs in X's MAIN world before its app starts, on the search page only. It
// retains only SearchTimeline business responses -- never cookies, request
// headers or authorization data. What it keeps of the request is the query and
// the cursor, so the read can be audited against what the server asked for.
(() => {
  if (location.origin !== "https://x.com" ||
      location.pathname !== "/search" ||
      window.__personaStudioTakeSearch) return;

  const pending = [];
  const counts = { requests: 0, captured: 0, dropped: 0 };
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const MAX_RESPONSE_BYTES = 4_000_000;
  const MAX_BUFFER_BYTES = 24_000_000;
  const MAX_AGE_MS = 120_000;
  // One page per drain is the normal rhythm; the buffer only has to absorb the
  // burst, so it is sized well past a page and still bounded in memory.
  const MAX_PENDING = 16;

  const bytes = () => pending.reduce((total, entry) => total + entry.bytes, 0);

  const take = () => {
    const cutoff = Date.now() - MAX_AGE_MS;
    while (pending.length && pending[0].captured_at_ms < cutoff) pending.shift();
    return pending.shift() || null;
  };
  const inspect = () => ({ ...counts, queued: pending.length });
  Object.defineProperty(window, "__personaStudioTakeSearch", {
    value: take, configurable: false, writable: false,
  });
  Object.defineProperty(window, "__personaStudioSearchCounts", {
    value: inspect, configurable: false, writable: false,
  });

  // Where X puts the question it is asking. The cursor travels in the request
  // variables, so a page can be matched to the page that preceded it.
  const variablesFrom = (method, url, body) => {
    try {
      const target = new URL(String(url), location.href);
      if (target.origin !== location.origin ||
          !/^\/i\/api\/graphql\/[^/]+\/SearchTimeline$/.test(target.pathname)) return null;
      let raw = target.searchParams.get("variables");
      if (raw == null && String(method).toUpperCase() === "POST" && typeof body === "string") {
        // A POST body is JSON today; the form-encoded shape is cheap insurance.
        try { raw = JSON.parse(body || "{}").variables; }
        catch { raw = new URLSearchParams(body).get("variables"); }
      }
      const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (!parsed || typeof parsed !== "object") return null;
      const query = typeof parsed.rawQuery === "string" ? parsed.rawQuery.slice(0, 2000) : "";
      return {
        raw_query: query,
        cursor_sent: typeof parsed.cursor === "string" ? parsed.cursor : null,
      };
    } catch { /* Other X requests are outside the search collector. */ }
    return null;
  };

  const capture = (variables, text) => {
    if (typeof text !== "string") {
      counts.dropped += 1;
      return;
    }
    // One size measurement, used both as the gate and as the number the driver
    // adds up, so the two cannot disagree about how big a page was.
    const size = new TextEncoder().encode(text).length;
    if (size > MAX_RESPONSE_BYTES) {
      counts.dropped += 1;
      return;
    }
    let response;
    try {
      response = JSON.parse(text);
    } catch {
      // A body that is not JSON is a page the read did not get: counting it is
      // what stops an incomplete read from looking like a finished one.
      counts.dropped += 1;
      return;
    }
    if (!response || typeof response.data !== "object" || !response.data) {
      counts.dropped += 1;
      return;
    }
    const entry = {
      ...variables,
      observed_at: Math.floor(Date.now() / 1000),
      captured_at_ms: Date.now(),
      bytes: size,
      payload: { data: response.data },
    };
    // A page that would not fit is counted, never silently absent: the driver
    // reports the drop so an incomplete read cannot look like a finished one.
    if (pending.length >= MAX_PENDING || bytes() + entry.bytes > MAX_BUFFER_BYTES) {
      counts.dropped += 1;
      return;
    }
    pending.push(entry);
    counts.captured += 1;
  };

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__personaStudioSearch = { method: String(method), url: String(url) };
    return originalOpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function (...args) {
    const opened = this.__personaStudioSearch;
    // A GET carries the variables in the query string, a POST carries them in
    // the body, so the request is only fully known once `send` has its body.
    const query = opened
      ? variablesFrom(opened.method, opened.url, typeof args[0] === "string" ? args[0] : null)
      : null;
    if (query) {
      counts.requests += 1;
      this.addEventListener("loadend", () => {
        // A refused page is a page the read did not get; the driver asks for the
        // tally so it cannot report a read that missed a page as a finished one.
        if (this.status !== 200) {
          counts.dropped += 1;
          return;
        }
        capture(query, this.responseText);
      }, { once: true });
    }
    return originalSend.apply(this, args);
  };

  if (typeof window.fetch === "function") {
    const originalFetch = window.fetch;
    window.fetch = function (input, init) {
      const method = init?.method || input?.method || "GET";
      const url = typeof input === "string" ? input : input?.url;
      const query = variablesFrom(method, url, init?.body);
      const request = originalFetch.apply(this, arguments);
      if (!query) return request;
      counts.requests += 1;
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
