import { readPersonaPage } from "./persona-page.js";
let socket,
  connecting = false;
const delay = (ms) => new Promise((r) => setTimeout(r, ms));
export async function operate(action) {
  const key = "persona-task-" + action.import_id;
  let saved = (await chrome.storage.session.get(key))[key];
  let tab;
  if (saved) {
    try {
      tab = await chrome.tabs.get(saved.tab);
    } catch {
      if (saved.generation === action.generation)
        return { state: "waiting_browser" };
      saved = undefined;
    }
    if (tab && !tab.url?.startsWith("https://x.com/"))
      return { state: "waiting_browser" };
  }
  if (!tab) {
    tab = await chrome.tabs.create({
      url: "about:blank",
      active: true,
    });
    await chrome.storage.session.set({
      [key]: { tab: tab.id, generation: action.generation },
    });
    await delay(400);
    await chrome.tabs.update(tab.id, {
      url: "https://x.com/" + action.account,
    });
  }
  if (saved?.last === action.id) return saved.result;
  for (let i = 0; i < 30; i++) {
    const t = await chrome.tabs.get(tab.id);
    if (t.status === "complete") break;
    await delay(300);
  }
  await delay(1000);
  const run = async (a) => {
    const result = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "ISOLATED",
      func: readPersonaPage,
      args: [a, action.account],
    });
    return result[0]?.result || { state: "waiting_browser" };
  };
  // Poll read-only readiness, never repeat a click/scroll while waiting.
  const readWhenReady = async (a) => {
    let result = { state: "waiting_browser" };
    for (let attempt = 0; attempt < 30; attempt++) {
      try {
        const current = await chrome.tabs.get(tab.id);
        result =
          current.status === "complete"
            ? await run(a)
            : { state: "waiting_browser" };
      } catch {
        result = { state: "waiting_browser" };
      }
      if (result.state !== "waiting_browser") return result;
      await delay(500);
    }
    return result;
  };
  // Re-check signed-in identity before any navigation/action, including resume.
  let result = await readWhenReady({ kind: "identity" });
  if (result.state === "ready") {
    if (["profile", "posts", "replies"].includes(action.kind)) {
      const url =
        "https://x.com/" +
        action.account +
        (action.kind === "replies" ? "/with_replies" : "");
      const current = await chrome.tabs.get(tab.id);
      if (current.url !== url) {
        await chrome.tabs.update(tab.id, { url });
        await delay(500);
      }
      result = await readWhenReady({ kind: "snapshot" });
    } else {
      result = await run(action);
      if (result.navigate) {
        const url = new URL(result.navigate);
        if (
          url.origin !== "https://x.com" ||
          !/^\/[A-Za-z0-9_]+\/status\/\d+$/.test(url.pathname)
        )
          return { state: "waiting_browser" };
        await chrome.tabs.update(tab.id, { url: url.href });
        await delay(500);
        result = await readWhenReady({ kind: "snapshot" });
      }
    }
  }
  await chrome.storage.session.set({
    [key]: {
      tab: tab.id,
      generation: action.generation,
      last: action.id,
      result,
    },
  });
  return result;
}
export async function connectPersonaBrowser() {
  if (
    connecting ||
    socket?.readyState === WebSocket.OPEN ||
    socket?.readyState === WebSocket.CONNECTING
  )
    return;
  connecting = true;
  try {
    const { device } = await chrome.storage.session.get("device");
    if (!device || device.expires * 1000 < Date.now()) return;
    const base = new URL(device.local);
    if (
      base.protocol !== "http:" ||
      !["127.0.0.1", "localhost"].includes(base.hostname) ||
      base.origin !== device.local
    )
      return;
    socket = new WebSocket(
      device.local.replace(/^http:/, "ws:") + "/api/v1/persona-browser",
    );
    socket.onopen = () =>
      socket.send(
        JSON.stringify({
          token: device.token,
          version: chrome.runtime.getManifest().version,
        }),
      );
    socket.onmessage = async (event) => {
      const current = socket;
      try {
        const { action } = JSON.parse(event.data);
        if (!action) {
          await delay(1500);
          if (current.readyState === 1)
            current.send(JSON.stringify({ type: "heartbeat" }));
          return;
        }
        let data;
        try {
          data = await operate(action);
        } catch {
          data = { state: "waiting_browser" };
        }
        if (current.readyState === 1)
          current.send(
            JSON.stringify({
              type: "result",
              id: action.id,
              import_id: action.import_id,
              generation: action.generation,
              data,
            }),
          );
      } catch {
        current.close();
      }
    };
  } finally {
    connecting = false;
  }
}
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.device) {
    socket?.close();
    socket = undefined;
    void connectPersonaBrowser();
  }
});
chrome.alarms.create("persona-browser-reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "persona-browser-reconnect") void connectPersonaBrowser();
});
void connectPersonaBrowser();
