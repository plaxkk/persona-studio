import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { EXTENSION_ID } from "../browser-id";
import { Button } from "./UI";
import type { Actions } from "../types";

type Reply = {
  ok?: boolean;
  approved?: boolean;
  error?: boolean;
  waitingLogin?: boolean;
  loginReady?: boolean;
  busy?: boolean;
};
type Pair = { id: string; token: string; expires: number };
type Runtime = {
  lastError?: { message?: string };
  sendMessage: (
    id: string,
    message: unknown,
    callback: (reply: Reply) => void,
  ) => void;
};
function bridge(message: unknown): Promise<Reply> {
  return new Promise((resolve) => {
    const runtime = (window as unknown as { chrome?: { runtime?: Runtime } })
      .chrome?.runtime;
    if (!runtime?.sendMessage) return resolve({});
    const timer = setTimeout(() => resolve({}), 2500);
    try {
      runtime.sendMessage(EXTENSION_ID, message, (reply) => {
        clearTimeout(timer);
        resolve(runtime.lastError ? {} : reply || {});
      });
    } catch {
      clearTimeout(timer);
      resolve({});
    }
  });
}
export function BrowserConnection({
  visible,
  actions,
}: {
  visible: boolean;
  actions: Actions;
}) {
  const [available, setAvailable] = useState(false);
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("正在检测浏览器连接助手…");
  const current = useRef<Pair | null>(null);
  const mounted = useRef(true);
  const loginRetries = useRef(0);
  const sequence = useRef(0);
  const actionsRef = useRef(actions);
  actionsRef.current = actions;
  const start = async () => {
    if (current.current) return;
    setBusy(true);
    const attempt = ++sequence.current;
    try {
      const p = await api<Pair>("/browser/pair", "POST", {});
      if (sequence.current !== attempt || !mounted.current) {
        await api("/browser/pair/" + p.id, "DELETE");
        return;
      }
      current.current = p;
      const reply = await bridge({ type: "connect", ...p });
      if (sequence.current !== attempt) {
        await bridge({ type: "cancel", id: p.id });
        return;
      }
      if (!reply.ok) {
        await api("/browser/pair/" + p.id, "DELETE");
        throw new Error(
          "扩展尚未授权或正在处理其他连接，请打开扩展菜单后重试。",
        );
      }
      setMessage(
        "正在检测 X 登录。若打开了 X 登录页，请完成登录；本页会自动更新。",
      );
    } catch (e) {
      current.current = null;
      setBusy(false);
      setMessage((e as Error).message);
    }
  };
  const cancel = async () => {
    sequence.current += 1;
    const p = current.current;
    if (p) {
      try {
        await api("/browser/pair/" + p.id, "DELETE");
        await bridge({ type: "cancel", id: p.id });
      } catch (e) {
        setMessage((e as Error).message);
        return;
      }
    }
    current.current = null;
    setBusy(false);
    setMessage("本次连接已取消。可在扩展菜单停止以后打开面板时自动连接。");
  };
  useEffect(() => {
    mounted.current = true;
    let checking = false;
    let active = true;
    void bridge({ type: "ping" }).then((reply) => {
      if (!active) return;
      setAvailable(!!reply.ok);
      setApproved(!!reply.approved);
      if (reply.approved) void start();
      else
        setMessage(
          reply.ok
            ? "请在浏览器工具栏打开连接助手，允许此工作室连接。"
            : "在登录 X 的 Chrome 或 Edge 中安装连接助手，即可免填 cookie。",
        );
    });
    const timer = setInterval(async () => {
      const p = current.current;
      if (!p || checking) return;
      checking = true;
      try {
        const result = await api<{
          status: string;
          message: string;
          username: string;
        }>("/browser/pair/" + p.id);
        if (!mounted.current || current.current?.id !== p.id) return;
        if (result.status === "verifying")
          setMessage("已检测到登录，正在核对 X 账号身份…");
        if (
          result.status === "failed" &&
          Date.now() < p.expires * 1000 &&
          loginRetries.current < 2
        ) {
          const ext = await bridge({ type: "status", id: p.id });
          if (ext.loginReady) {
            current.current = null;
            loginRetries.current += 1;
            await start();
            return;
          }
          if (ext.waitingLogin || ext.busy) {
            setMessage(
              "X 登录已失效，请在打开的 X 页面完成登录，工作室会自动重新连接。",
            );
            return;
          }
        }
        if (!["waiting", "verifying"].includes(result.status)) {
          current.current = null;
          setBusy(false);
          setMessage(
            result.status === "connected"
              ? "已从浏览器连接 @" + result.username + "，无需填写 cookie。"
              : result.message || "连接已取消，请重试。",
          );
          if (result.status === "connected") await actionsRef.current.refresh();
        } else if (result.status === "waiting") {
          const ext = await bridge({ type: "status", id: p.id });
          if (ext.error) {
            await cancel();
            setMessage("浏览器未能连接本机服务，请检查工作室地址后重试。");
          }
        }
      } catch (e) {
        if (mounted.current) setMessage((e as Error).message);
      } finally {
        checking = false;
      }
    }, 1500);
    return () => {
      active = false;
      mounted.current = false;
      clearInterval(timer);
    };
  }, []);
  return (
    <section
      className="surface browser-connection"
      hidden={!visible}
      aria-label="浏览器自动连接"
    >
      <h2>用当前浏览器连接 X</h2>
      <p className="description">
        一次授权，以后打开工作室即可连接；未登录时会引导你登录 X。
      </p>
      <p role="status">{message}</p>
      <div className="actions">
        {available && approved && (
          <Button disabled={busy} onClick={() => void start()}>
            {busy ? "等待登录与验证…" : "重新连接浏览器"}
          </Button>
        )}
        {busy && <Button onClick={() => void cancel()}>取消本次连接</Button>}
        <a href="/api/v1/browser/extension.zip" download>
          下载连接助手
        </a>
      </div>
      <details className="spaced-small">
        <summary>首次安装（只需一次）</summary>
        <ol>
          <li>
            在 Chrome 打开 <code>chrome://extensions</code>，或在 Edge 打开{" "}
            <code>edge://extensions</code>，开启“开发者模式”。
          </li>
          <li>
            解压下载文件，点击“加载已解压的扩展程序”，选择解压后的文件夹。
          </li>
          <li>
            打开工具栏里的“人格工作室 · X
            连接助手”，点击“允许连接并打开工作室”。
          </li>
        </ol>
        <p className="muted">
          请在同一个浏览器用户配置中登录 X 和工作室。Codex
          内置浏览器、无痕窗口不支持此扩展；目前仅支持本机部署。账号信息会自动填好，cookie
          明文不会显示在页面。
        </p>
      </details>
    </section>
  );
}
