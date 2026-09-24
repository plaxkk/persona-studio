import { useEffect, useState } from "react";
import { desktopCall } from "../desktop";
import { Button } from "./UI";
import App from "../App";
import { Download, HelpCircle, X } from "lucide-react";
import {
  EXTENSION_VERSION,
  EXTENSION_DOWNLOAD,
  extensionNeedsUpdate,
} from "../extension-info";
export function DesktopGate() {
  const [state, setState] = useState("checking");
  const [connectedOnce, setConnectedOnce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("正在检测本机连接…");
  const [extensionVersion, setExtensionVersion] = useState<string>();
  const [help, setHelp] = useState(false);
  const check = async () => {
    setBusy(true);
    const r = await desktopCall({ type: "desktop.status" });
    setExtensionVersion(r.extensionVersion);
    setState(r.ok ? "ready" : r.code || "offline");
    if (r.ok) {
      setConnectedOnce(true);
      window.dispatchEvent(new Event("desktop-connected"));
    }
    setMessage(
      r.message ||
        (r.code === "authorization"
          ? "请打开连接助手，点击“授权并打开在线工作室”。"
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
    const upgrade = () => setHelp(true);
    window.addEventListener("desktop-upgrade-required", upgrade);
    return () => {
      window.removeEventListener("desktop-upgrade-required", upgrade);
      window.removeEventListener("desktop-disconnected", lost);
      window.removeEventListener("desktop-logout", logout);
    };
  }, []);
  return (
    <>
      {connectedOnce && (
        <div className="desktop-session" hidden={state !== "ready"}>
          <div className="desktop-strip">
            <span className="desktop-connection-label">已连接这台电脑</span>
            {extensionNeedsUpdate(extensionVersion) && (
              <span className="extension-update-label">连接助手需更新</span>
            )}
            <a
              className="extension-download"
              href={EXTENSION_DOWNLOAD}
              download
            >
              <Download size={16} />
              下载连接助手{" "}
              <span className="extension-version">v{EXTENSION_VERSION}</span>
            </a>
            <button
              className="extension-help-toggle"
              aria-expanded={help}
              aria-controls="extension-help"
              onClick={() => setHelp(!help)}
            >
              <HelpCircle size={15} />
              安装 / 更新指南
            </button>
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
          {help && (
            <section
              id="extension-help"
              className="extension-help-panel surface"
              aria-label="连接助手安装与更新"
            >
              <div className="section-title">
                <h2>安装 / 更新连接助手</h2>
                <Button
                  aria-label="关闭更新指南"
                  onClick={() => setHelp(false)}
                >
                  <X size={18} />
                </Button>
              </div>
              <p>
                当前版本：{extensionVersion || "旧版，未提供版本信息"} ·
                最新版本：{EXTENSION_VERSION}
              </p>
              <ol>
                <li>点击上方「下载连接助手」，解压 ZIP 文件。</li>
                <li>
                  在 Chrome 地址栏输入 <code>chrome://extensions</code>
                  ，开启开发者模式。
                </li>
                <li>
                  首次安装：选择「加载已解压的扩展程序」。更新：用新文件替换原扩展目录，再点击扩展卡片的「重新加载」。只下载
                  ZIP 不会自动更新。
                </li>
                <li>
                  打开浏览器工具栏中的连接助手，点击「授权并打开在线工作室」。
                </li>
              </ol>
              <Button busy={busy} disabled={busy} onClick={() => void check()}>
                我已更新，重新检测
              </Button>
            </section>
          )}
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
            <a className="button primary" href={EXTENSION_DOWNLOAD} download>
              <Download size={18} />
              下载连接助手 v{EXTENSION_VERSION}
            </a>
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
              <a href={EXTENSION_DOWNLOAD} download>
                下载本机连接助手 {EXTENSION_VERSION}
              </a>
              ，解压后在 <code>chrome://extensions</code>{" "}
              开启开发者模式，加载已解压的扩展程序。已安装旧版时点扩展卡片的“重新加载”。
            </li>
            <li>
              确认{" "}
              <a href="http://127.0.0.1:18880" target="_blank" rel="noreferrer">
                本机工作室
              </a>
              正在运行，且已安装本机连接程序。
            </li>
            <li>打开连接助手，点击“授权并打开在线工作室”，无需输入密码。</li>
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
