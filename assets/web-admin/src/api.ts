import { desktopMode, desktopCall } from "./desktop";
let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export async function api<T = Record<string, unknown>>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  if (desktopMode) {
    const reply = await desktopCall({
      type: "desktop.api",
      path,
      method,
      body,
    });
    if (!reply.ok || reply.status === 401 || path === "/auth/logout") {
      if (reply.code !== "forbidden" && reply.code !== "size")
        window.dispatchEvent(
          new Event(
            path === "/auth/logout" ? "desktop-logout" : "desktop-disconnected",
          ),
        );
      if (path === "/auth/logout" && reply.ok) return reply.data as T;
      throw new Error(reply.message || "本机连接已断开，请重新授权。");
    }
    if ((reply.status || 500) >= 400)
      throw new Error(reply.data?.detail?.message || "操作未完成，请重试。");
    return reply.data as T;
  }
  const response = await fetch("/api/v1" + path, {
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(method === "GET" ? {} : { "X-CSRF-Token": csrf }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("session-expired"));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : data.detail?.message || "保存失败，请检查输入后重试。",
    );
  }
  return data as T;
}
export const statusLabel: Record<string, string> = {
  ready: "已验证",
  handled: "已处理",
  invalid_identity: "账号身份不可用",
  identity_mismatch: "账号身份不匹配",
  risk_skipped: "已跳过风险内容",
  invalid_output: "没有有效输出",
  not_configured: "待配置",
  not_connected: "未连接",
  not_verified: "待验证",
  needs_login: "需要重新登录",
  partial: "部分来源可用",
  rate_limited: "暂时限流",
  client_incompatible: "X 读取接口需要更新",
  network_error: "服务器无法连接 X",
  source_error: "来源暂不可用",
  timeout: "连接超时",
  connection_error: "连接异常",
  webhook_conflict: "已有其他 Telegram 接收服务",
  engine_failed: "连接失败",
  planned: "后续接入",
  queued: "排队中",
  running: "处理中",
  succeeded: "已完成",
  failed: "未完成",
  cancelled: "已取消",
  new: "待处理",
  ignored: "已忽略",
  skipped: "已跳过",
  draft: "待处理",
  confirmed: "用户确认完成",
  archived: "已归档",
  ok: "最近读取成功",
};
export const label = (value: string) => statusLabel[value] || value;
export const time = (value: number) =>
  value
    ? new Date(value * 1000).toLocaleString("zh-CN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "尚未同步";
export function safeXUrl(value: string): string | null {
  try {
    const u = new URL(value);
    if (
      !["x.com", "www.x.com", "twitter.com", "www.twitter.com"].includes(
        u.hostname,
      ) ||
      u.protocol !== "https:" ||
      !/^\/(?:[A-Za-z0-9_]{1,15}|i\/web)\/status\/\d+\/?$/.test(u.pathname)
    )
      return null;
    return "https://x.com" + u.pathname;
  } catch {
    return null;
  }
}
