import { CLOUD_ORIGIN, desktopMessage } from "./desktop.js";
// Credentials travel directly from Chrome's cookie API to the authorized local server.
// They are never returned to a web page, logged, or persisted by this extension.
export function localOrigin(value) {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "http:" ||
      !["127.0.0.1", "localhost"].includes(url.hostname) ||
      url.username ||
      url.password
    )
      return null;
    return url.origin;
  } catch {
    return null;
  }
}
let busy = false;
let changedWhileBusy = false;
let processingId = null;
async function clearPending() {
  await chrome.storage.session.remove("pending");
  await chrome.alarms.clear("x-login");
}
async function tryConnect() {
  if (busy) {
    changedWhileBusy = true;
    return;
  }
  busy = true;
  try {
    const { pending } = await chrome.storage.session.get("pending");
    if (!pending) return;
    const { origin, enabled } = await chrome.storage.local.get([
      "origin",
      "enabled",
    ]);
    if (
      !enabled ||
      origin !== pending.origin ||
      Date.now() >= pending.expires * 1000
    ) {
      await clearPending();
      return;
    }
    const [auth, csrf] = await Promise.all(
      ["auth_token", "ct0"].map((name) =>
        chrome.cookies.get({ url: "https://x.com/", name }),
      ),
    );
    if (!auth?.value || !csrf?.value) {
      if (!pending.loginOpened) {
        pending.loginOpened = true;
        await chrome.storage.session.set({ pending });
        void chrome.tabs
          .create({ url: "https://x.com/i/flow/login" })
          .catch(() => {});
      }
      return;
    }
    const fingerprint = Array.from(
      new Uint8Array(
        await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(JSON.stringify([auth.value, csrf.value])),
        ),
      ),
    )
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    if (pending.waitingForChange) {
      if (pending.waitingForChange !== fingerprint) {
        await chrome.storage.session.set({
          outcome: { id: pending.id, loginReady: true },
        });
        await clearPending();
      }
      return;
    }
    let waitingForLogin = false;
    processingId = pending.id;
    // Fail closed on redirects: never forward a cookie to another origin.
    try {
      const response = await fetch(origin + "/api/v1/browser/complete", {
        method: "POST",
        credentials: "omit",
        redirect: "error",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: pending.token,
          auth_token: auth.value,
          ct0: csrf.value,
        }),
        signal: AbortSignal.timeout(25000),
      });
      const result = response.ok ? await response.json() : {};
      if (result.code === "needs_login") {
        waitingForLogin = true;
        await chrome.storage.session.set({
          pending: {
            ...pending,
            waitingForChange: fingerprint,
            loginOpened: true,
          },
          outcome: { id: pending.id, waitingLogin: true },
        });
        void chrome.tabs
          .create({ url: "https://x.com/i/flow/login" })
          .catch(() => {});
      } else {
        await chrome.storage.session.set({
          outcome: { id: pending.id, error: !response.ok },
        });
      }
    } catch {
      await chrome.storage.session.set({
        outcome: { id: pending.id, error: true },
      });
    } finally {
      processingId = null;
      if (!waitingForLogin) await clearPending();
    }
  } catch {
    // No exception text is logged; network errors may contain sensitive request details.
    await clearPending();
  } finally {
    busy = false;
    if (changedWhileBusy) {
      changedWhileBusy = false;
      void tryConnect();
    }
  }
}
chrome.runtime.onMessageExternal.addListener((message, sender, respond) => {
  (async () => {
    if (sender.url && new URL(sender.url).origin === CLOUD_ORIGIN)
      return desktopMessage(message, sender);
    const origin = localOrigin(sender.url);
    const config = await chrome.storage.local.get(["origin", "enabled"]);
    if (!origin || sender.frameId !== 0 || sender.tab?.incognito)
      return { ok: false };
    const approved = config.enabled === true && config.origin === origin;
    if (message?.type === "ping") return { ok: true, approved, version: 1 };
    if (!approved) return { ok: false };
    if (message?.type === "status") {
      // Cookie events can be delayed across service-worker suspension. Poll only
      // while an authorized panel has an outstanding pairing; unchanged cookies
      // never trigger another authentication request.
      void tryConnect();
      const { outcome } = await chrome.storage.session.get("outcome");
      return {
        ok: true,
        error: outcome?.id === message.id && outcome.error === true,
        waitingLogin:
          outcome?.id === message.id && outcome.waitingLogin === true,
        loginReady: outcome?.id === message.id && outcome.loginReady === true,
        busy: processingId === message.id,
      };
    }
    if (message?.type === "cancel") {
      const { pending } = await chrome.storage.session.get("pending");
      if (pending?.id === message.id) await clearPending();
      return { ok: true };
    }
    if (
      message?.type !== "connect" ||
      !/^[A-Za-z0-9_-]{32,128}$/.test(message.token || "") ||
      !/^[a-f0-9]{32}$/.test(message.id || "") ||
      !Number.isFinite(message.expires) ||
      message.expires * 1000 <= Date.now() ||
      message.expires * 1000 > Date.now() + 301000
    )
      return { ok: false };
    if (busy) return { ok: false };
    await chrome.storage.session.set({
      pending: {
        origin,
        token: message.token,
        id: message.id,
        expires: message.expires,
      },
    });
    await chrome.alarms.create("x-login", { periodInMinutes: 0.5 });
    void tryConnect();
    return { ok: true };
  })()
    .then(respond)
    .catch(() => respond({ ok: false }));
  return true;
});
chrome.cookies.onChanged.addListener(({ cookie, removed }) => {
  if (
    !removed &&
    ["x.com", ".x.com"].includes(cookie.domain) &&
    ["auth_token", "ct0"].includes(cookie.name)
  )
    void tryConnect();
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "x-login") void tryConnect();
});
chrome.runtime.onStartup.addListener(() => {
  void clearPending();
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.enabled || changes.origin))
    void clearPending();
});
