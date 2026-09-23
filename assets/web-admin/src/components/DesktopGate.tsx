import { useEffect, useState } from "react";
import { desktopCall } from "../desktop";
import { Button } from "./UI";
import App from "../App";
export function DesktopGate() {
  const [state, setState] = useState("checking");
  const [connectedOnce, setConnectedOnce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("正在检测本机连接…");
  const check = async () => {
    setBusy(true);
    const r = await desktopCall({ type: "desktop.status" });
    setState(r.ok ? "ready" : r.code || "offline");
    if (r.ok) {
      setConnectedOnce(true);
      window.dispatchEvent(new Event("desktop-connected"));
    }
    setMessage(
      r.message ||
        (r.code === "authorization"
          ? "请在连接助手中用本机管理员密码授权。"
          : "请确认本机工作室已启动。"),
    );
    setBusy(false);
  };
  useEffect(() => {
    void check();
    const lost = () => {
      setState("offline");
      setMessage("连接已断开，请重新连接。本机保存的草稿仍然保留。");
    };
    const logout = () => {
      setConnectedOnce(false);
      lost();
    };
    window.addEventListener("desktop-logout", logout);
    window.addEventListener("desktop-disconnected", lost);
    return () => {
      window.removeEventListener("desktop-disconnected", lost);
      window.removeEventListener("desktop-logout", logout);
    };
  }, []);
  return (
    <>
      {connectedOnce && (
        <div className="desktop-session" hidden={state !== "ready"}>
          <div className="desktop-strip">
            已连接这台电脑 · Agent 与数据在本机运行{" "}
            <button
              onClick={async () => {
                await desktopCall({
                  type: "desktop.api",
                  path: "/auth/logout",
                  method: "POST",
                  body: {},
                });
                window.dispatchEvent(new Event("desktop-logout"));
              }}
            >
              断开
            </button>
          </div>
          <App />
        </div>
      )}
      {state !== "ready" && (
        <main className="desktop-gate surface">
          <h1>把你的电脑接入人格工作室</h1>
          <p>
            网页负责操作面板，你电脑里的 Hermes / OpenClaw
            负责执行。人设、草稿和账号凭据保存在本机。
          </p>
          <p role="status">{message}</p>
          <div className="actions">
            <Button disabled={busy} onClick={() => void check()}>
              {busy ? "检测中…" : "我已授权，连接这台电脑"}
            </Button>
            <Button
              onClick={async () => {
                const r = await desktopCall({ type: "desktop.open" });
                if (!r.ok)
                  setMessage(
                    "请先下载扩展，并在 Chrome 扩展管理页安装或更新。",
                  );
              }}
            >
              打开连接助手
            </Button>
          </div>
          <ol>
            <li>
              <a href="/downloads/persona-studio-browser.zip" download>
                下载本机连接助手 1.1
              </a>
              ，解压后在 <code>chrome://extensions</code>{" "}
              开启开发者模式，加载已解压的扩展程序。已安装旧版时点扩展卡片的“重新加载”。
            </li>
            <li>
              确认{" "}
              <a href="http://127.0.0.1:18880" target="_blank" rel="noreferrer">
                本机工作室
              </a>
              正在运行，并已设置管理员密码。
            </li>
            <li>
              打开连接助手，在“连接在线面板”中输入本机管理员密码，点击“授权并打开在线工作室”。密码只发给本机服务。
            </li>
          </ol>
          <p className="muted">
            仅支持这台电脑的 Chrome / Edge；手机、无痕模式及 Codex
            内置浏览器暂不支持。电脑关机或休眠时无法执行任务。授权有效 24
            小时，关闭浏览器后需重新授权。
          </p>
        </main>
      )}
    </>
  );
}
