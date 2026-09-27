export const CLOUD_ORIGIN = "https://persona-studio-plaxkk.vercel.app";
export function allowedRoute(path, method) {
  if (typeof path !== "string" || path.length > 2000 || /[\\%#]/.test(path))
    return false;
  const [pathname, query] = path.split("?");
  if (query && (method !== "GET" || !["/memory", "/events"].includes(pathname)))
    return false;
  if (
    method === "GET" &&
    /^\/persona-imports(?:\/(?:preflight|[a-f0-9]{32}(?:\/export)?))?$/.test(
      pathname,
    )
  )
    return true;
  if (
    method === "POST" &&
    /^\/persona-imports(?:\/[a-f0-9]{32}\/(?:pause|resume|cancel|analyze|apply|delete))?$/.test(
      pathname,
    )
  )
    return true;
  if (method === "GET")
    return /^\/(auth\/(status|session)|health|overview|settings|persona(\/versions)?|memory|engines|connections|interactions|messages|creations(\/[a-f0-9]{32})?|tasks(\/[a-f0-9-]+)?|drafts(\/[a-f0-9-]+\/versions)?|events|diagnostics)$/.test(
      pathname,
    );
  if (method === "POST")
    return /^\/(auth\/logout|pause|persona\/(feedback|corpus)|engines\/(hermes|openclaw|codex)\/(verify|select)|sync|interactions\/[0-9]+\/ignore|generate|creations(\/[a-f0-9]{32}\/(turn|finalize))?|tasks\/[a-f0-9-]+\/cancel|drafts(\/[a-f0-9-]+\/(opened|confirm|archive))?|backup)$/.test(
      pathname,
    );
  return (
    method === "PUT" &&
    /^\/(settings|persona|creations\/[a-f0-9]{32}|drafts\/[a-f0-9-]+)$/.test(pathname)
  );
}
export async function desktopMessage(message, sender) {
  if (
    sender.frameId !== 0 ||
    sender.tab?.incognito ||
    new URL(sender.url).origin !== CLOUD_ORIGIN
  )
    return { ok: false };
  if (message?.type === "desktop.open") {
    await chrome.tabs.create({ url: chrome.runtime.getURL("popup.html") });
    return { ok: true };
  }
  const { device } = await chrome.storage.session.get("device");
  if (!device || device.expires * 1000 <= Date.now())
    return {
      ok: false,
      code: "authorization",
      message: "请在连接助手中授权此网站访问本机工作室。",
    };
  const origin = new URL(device.local);
  if (
    origin.protocol !== "http:" ||
    !["127.0.0.1", "localhost"].includes(origin.hostname) ||
    origin.origin !== device.local
  )
    return { ok: false, code: "authorization" };
  const status = message?.type === "desktop.status";
  const path = status ? "/auth/session" : message.path;
  const method = status ? "GET" : message.method;
  if (
    (!status && message?.type !== "desktop.api") ||
    !allowedRoute(path, method)
  )
    return {
      ok: false,
      code: "forbidden",
      message: "此操作只能在本机设置中完成。",
    };
  const payload =
    message.body === undefined ? undefined : JSON.stringify(message.body);
  if (payload && new TextEncoder().encode(payload).length > 1100000)
    return { ok: false, code: "size", message: "内容过大。" };
  // Disconnect locally even when the backend is offline; never retain a reusable
  // browser capability after the user requests revocation.
  if (path === "/auth/logout") await chrome.storage.session.remove("device");
  try {
    const response = await fetch(device.local + "/api/v1" + path, {
      method,
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-Studio-Device": device.token,
      },
      body: method === "GET" ? undefined : payload,
      signal: AbortSignal.timeout(100000),
    });
    const data = await response.json();
    if (response.status === 401 || path === "/auth/logout")
      await chrome.storage.session.remove("device");
    if (status)
      return {
        ok: response.ok,
        code: response.ok ? "ready" : "authorization",
        extensionVersion: chrome.runtime.getManifest().version,
      };
    return { ok: true, status: response.status, data };
  } catch {
    return {
      ok: false,
      code: "offline",
      message:
        "本机工作室未连接。请确认服务已启动，再重试；操作结果不确定时请先查看内容记录。",
    };
  }
}
