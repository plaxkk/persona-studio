import { EXTENSION_ID } from "./browser-id";
export const desktopMode = import.meta.env.VITE_STUDIO_DESKTOP === "true";
type Reply = {
  ok?: boolean;
  extensionVersion?: string;
  code?: string;
  message?: string;
  status?: number;
  data?: any;
};
export function desktopCall(message: unknown): Promise<Reply> {
  return new Promise((resolve) => {
    const runtime = (window as any).chrome?.runtime;
    if (!runtime?.sendMessage)
      return resolve({
        ok: false,
        code: "extension",
        message: "请先安装本机连接助手。",
      });
    const timer = setTimeout(
      () =>
        resolve({
          ok: false,
          code: "offline",
          message: "本机响应超时，请检查连接。",
        }),
      105000,
    );
    try {
      runtime.sendMessage(EXTENSION_ID, message, (reply: Reply) => {
        clearTimeout(timer);
        resolve(
          runtime.lastError
            ? {
                ok: false,
                code: "extension",
                message: "未检测到连接助手，请安装或更新扩展。",
              }
            : reply || { ok: false, code: "offline" },
        );
      });
    } catch {
      clearTimeout(timer);
      resolve({ ok: false, code: "extension" });
    }
  });
}
