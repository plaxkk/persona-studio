import { CLOUD_ORIGIN } from "./desktop.js";
const input = document.querySelector("#origin");
const status = document.querySelector("#status");
const config = await chrome.storage.local.get(["origin", "enabled"]);
input.value = config.origin || "http://127.0.0.1:18880";
status.textContent = config.enabled
  ? "已授权：打开工作室后自动连接。"
  : "尚未授权，点击上方按钮开始。";
document.querySelector("#allow").onclick = async () => {
  let origin;
  try {
    const url = new URL(input.value);
    if (
      url.protocol !== "http:" ||
      !["localhost", "127.0.0.1"].includes(url.hostname) ||
      url.username ||
      url.password ||
      url.pathname !== "/" ||
      url.search ||
      url.hash
    )
      throw new Error();
    origin = url.origin;
  } catch {
    status.textContent =
      "请输入本机 http://127.0.0.1:端口 或 http://localhost:端口 地址。";
    return;
  }
  await chrome.storage.local.set({ origin, enabled: true });
  await chrome.tabs.create({ url: origin });
  window.close();
};
document.querySelector("#revoke").onclick = async () => {
  await chrome.storage.local.set({ enabled: false });
  await chrome.storage.session.remove("pending");
  await chrome.alarms.clear("x-login");
  status.textContent = "自动连接已停止。已保存的账号凭据可在工作室设置中删除。";
};

document.querySelector("#desktop-allow").onclick = async () => {
  const button = document.querySelector("#desktop-allow");
  button.disabled = true;
  try {
    const local = new URL(input.value);
    if (
      local.protocol !== "http:" ||
      !["127.0.0.1", "localhost"].includes(local.hostname) ||
      local.username ||
      local.password ||
      local.pathname !== "/" ||
      local.search ||
      local.hash
    )
      throw new Error("请输入正确的本机工作室地址。");
    status.textContent = "正在验证本机管理员…";
    const response = await fetch(local.origin + "/api/v1/desktop/login", {
      method: "POST",
      credentials: "omit",
      redirect: "error",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        password: document.querySelector("#password").value,
      }),
      signal: AbortSignal.timeout(15000),
    });
    document.querySelector("#password").value = "";
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail?.message || "本机授权失败。");
    if (data.origin !== CLOUD_ORIGIN)
      throw new Error("本机服务版本不匹配，请更新。");
    await chrome.storage.session.set({
      device: { local: local.origin, token: data.token, expires: data.expires },
    });
    await chrome.tabs.create({ url: CLOUD_ORIGIN });
    window.close();
  } catch (error) {
    status.textContent =
      error instanceof TypeError
        ? "本机服务未启动或版本过旧，请先打开本机工作室。"
        : error.message;
  } finally {
    document.querySelector("#password").value = "";
    button.disabled = false;
  }
};
document.querySelector("#desktop-revoke").onclick = async () => {
  const { device } = await chrome.storage.session.get("device");
  await chrome.storage.session.remove("device");
  if (device) {
    try {
      await fetch(device.local + "/api/v1/auth/logout", {
        method: "POST",
        credentials: "omit",
        redirect: "error",
        headers: { "X-Studio-Device": device.token },
        signal: AbortSignal.timeout(5000),
      });
    } catch {}
  }
  status.textContent = "已断开在线面板。";
};
