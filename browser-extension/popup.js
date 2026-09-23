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
