import { useCallback, useEffect, useState } from "react";
import {
  House,
  UserRound,
  Inbox,
  PenLine,
  Library,
  Settings,
  LogOut,
  Menu,
  X,
  ArrowRight,
  Leaf,
  ShieldCheck,
  LoaderCircle,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { api, setCsrf } from "./api";
import type {
  Studio,
  Page,
  Actions,
  Overview,
  Draft,
  Post,
  Connections,
  Message,
} from "./types";
import { Button, Field } from "./components/UI";
import { flushDrafts } from "./pending";
import { TaskActivity } from "./components/TaskActivity";
import Home from "./pages/Home";
import PersonaPage from "./pages/PersonaPage";
import InboxPage from "./pages/Inbox";
import Compose from "./pages/Compose";
import History from "./pages/History";
import { BrowserConnection } from "./components/BrowserConnection";
import SettingsPage from "./pages/SettingsPage";
const nav: { id: Page; label: string; icon: typeof House }[] = [
  { id: "home", label: "工作室", icon: House },
  { id: "persona", label: "我的人格", icon: UserRound },
  { id: "inbox", label: "互动收件箱", icon: Inbox },
  { id: "compose", label: "写推文", icon: PenLine },
  { id: "history", label: "内容记录", icon: Library },
  { id: "settings", label: "设置", icon: Settings },
];
export default function App() {
  const [auth, setAuth] = useState<"loading" | "setup" | "login" | "ready">(
      "loading",
    ),
    [brand, setBrand] = useState("人格工作室"),
    [needsToken, setNeedsToken] = useState(false),
    [password, setPassword] = useState(""),
    [confirm, setConfirm] = useState(""),
    [token, setToken] = useState(""),
    [page, setPage] = useState<Page>("home"),
    [data, setData] = useState<Studio | null>(null),
    [toast, setToast] = useState<{ text: string; error: boolean } | null>(null),
    [busy, setBusy] = useState(false),
    [mobile, setMobile] = useState(false);
  const notify = useCallback(
    (text: string, error = false) => setToast({ text, error }),
    [],
  );
  const refresh = useCallback(async () => {
    const [overview, drafts, posts, connections, messages] = await Promise.all([
      api<Overview>("/overview"),
      api<Draft[]>("/drafts"),
      api<Post[]>("/interactions"),
      api<Connections>("/connections"),
      api<Message[]>("/messages"),
    ]);
    setData({ overview, drafts, posts, connections, messages });
    setBrand(overview.settings.brand);
  }, []);
  useEffect(() => {
    const expired = () => {
      setAuth("login");
      setData(null);
      setCsrf("");
    };
    window.addEventListener("session-expired", expired);
    (async () => {
      try {
        const s = await api<{
          initialized: boolean;
          needs_setup_token: boolean;
          brand: string;
        }>("/auth/status");
        setBrand(s.brand);
        setNeedsToken(s.needs_setup_token);
        if (!s.initialized) {
          setAuth("setup");
          return;
        }
        try {
          const session = await api<{ csrf: string }>("/auth/session");
          setCsrf(session.csrf);
          setAuth("ready");
        } catch {
          setAuth("login");
        }
      } catch (e) {
        notify((e as Error).message, true);
        setAuth("login");
      }
    })();
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  useEffect(() => {
    if (auth !== "ready") return;
    refresh().catch((e) => notify(e.message, true));
    const stream = new EventSource("/api/v1/events/stream");
    let pending = false;
    const update = () => {
      if (!pending) {
        pending = true;
        refresh()
          .catch(() => {})
          .finally(() => {
            pending = false;
          });
      }
    };
    stream.addEventListener("refresh", update);
    return () => stream.close();
  }, [auth, refresh]);
  useEffect(() => {
    if (!toast || toast.error) return;
    const timer = setTimeout(() => setToast(null), 5500);
    return () => clearTimeout(timer);
  }, [toast]);
  const actions: Actions = {
    refresh,
    notify,
    navigate: (p) => {
      void flushDrafts()
        .then(() => {
          setPage(p);
          setMobile(false);
          window.scrollTo(0, 0);
        })
        .catch((e) => notify(e.message, true));
    },
    run: async <T,>(action: () => Promise<T>, success?: string) => {
      try {
        const result = await action();
        if (success) notify(success);
        await refresh();
        return result;
      } catch (e) {
        notify((e as Error).message, true);
        return undefined;
      }
    },
  };
  async function login() {
    setBusy(true);
    try {
      if (auth === "setup" && password !== confirm)
        throw new Error("两次密码不一致。");
      const r = await api<{ csrf: string }>(
        auth === "setup" ? "/auth/setup" : "/auth/login",
        "POST",
        { password, setup_token: token },
      );
      setCsrf(r.csrf);
      setAuth("ready");
      setPassword("");
      setConfirm("");
      setToken("");
      setToast(null);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  const notification = toast && (
    <div
      role={toast.error ? "alert" : "status"}
      className={`toast ${toast.error ? "error" : ""}`}
    >
      {toast.error ? <AlertCircle size={19} /> : <CheckCircle2 size={19} />}
      <span>{toast.text}</span>
      <button aria-label="关闭提示" onClick={() => setToast(null)}>
        <X size={16} />
      </button>
    </div>
  );
  if (auth === "loading")
    return (
      <div className="loading">
        <LoaderCircle className="spin" />
        正在打开工作室…
      </div>
    );
  if (auth !== "ready")
    return (
      <div className="auth-page">
        <div className="auth-story">
          <a className="brand" href="/">
            <span className="brand-icon">
              <Leaf size={22} />
            </span>
            {brand}
          </a>
          <div>
            <p className="eyebrow">给想象中的角色，一个表达的地方</p>
            <h1>
              有自己的性格。
              <br />
              也有自己的声音。
            </h1>
            <p>
              读懂互动，整理想法，慢慢培养一个独特的虚构人格。
              <br />
              最后的发布决定，始终由你掌握。
            </p>
            <div className="auth-flow">
              <span>收集声音</span>
              <ArrowRight size={16} />
              <span>准备文案</span>
              <ArrowRight size={16} />
              <span>由你发布</span>
            </div>
          </div>
          <small>你的数据保存在自己的设备上。</small>
        </div>
        <div className="auth-form">
          <div className="auth-card">
            <span className="small-icon">
              <ShieldCheck size={26} />
            </span>
            <h2>{auth === "setup" ? "创建你的工作室" : "欢迎回到工作室"}</h2>
            <p>
              {auth === "setup"
                ? "设置管理员密码，开始培养你的第一个角色。"
                : "输入密码，继续未完成的想法。"}
            </p>
            <form
              className="form-stack"
              onSubmit={(e) => {
                e.preventDefault();
                void login();
              }}
            >
              <Field
                label="管理员密码"
                hint={
                  auth === "setup"
                    ? "至少 10 个字符；凭据不会显示在工作台。"
                    : undefined
                }
              >
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  minLength={10}
                  maxLength={256}
                  required
                  autoComplete={
                    auth === "setup" ? "new-password" : "current-password"
                  }
                />
              </Field>
              {auth === "setup" && (
                <Field label="再次输入密码">
                  <input
                    type="password"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    minLength={10}
                    required
                    autoComplete="new-password"
                  />
                </Field>
              )}
              {auth === "setup" && needsToken && (
                <Field
                  label="服务器初始化口令"
                  hint="在服务器数据目录的 setup-token 文件中获取。"
                >
                  <input
                    type="password"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                  />
                </Field>
              )}
              <Button tone="primary" type="submit" busy={busy}>
                {auth === "setup" ? "创建工作室" : "进入工作室"}
                <ArrowRight size={17} />
              </Button>
            </form>
            <p className="auth-note">
              半自动工作台 · 不替你执行任何 X 发布操作
            </p>
          </div>
        </div>
        {notification}
      </div>
    );
  return (
    <div className="app-shell">
      <header className="mobile-bar">
        <button aria-label="打开导航" onClick={() => setMobile(!mobile)}>
          <Menu size={22} />
        </button>
        <strong>{brand}</strong>
        <Leaf size={20} />
      </header>
      {mobile && (
        <button
          aria-label="关闭导航"
          className="nav-overlay"
          onClick={() => setMobile(false)}
        />
      )}
      <aside className={`sidebar ${mobile ? "open" : ""}`}>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            actions.navigate("home");
          }}
        >
          <span className="brand-icon">
            <Leaf size={22} />
          </span>
          <span>
            {brand}
            <small>PERSONA STUDIO</small>
          </span>
        </a>
        <div className="nav-label">创作与日常</div>
        <nav>
          {nav.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              aria-label={label}
              onClick={() => actions.navigate(id)}
              aria-current={page === id ? "page" : undefined}
              className={page === id ? "active" : ""}
            >
              <Icon size={19} />
              <span>{label}</span>
              {id === "inbox" && !!data?.overview.counts.new && (
                <span className="nav-count">{data.overview.counts.new}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="mode-note">
            <ShieldCheck size={17} />
            <div>
              <strong>半自动，安心创作</strong>
              <p>所有 X 操作由你完成</p>
            </div>
          </div>
          <div className="sidebar-profile">
            <span className="avatar">
              {data?.overview.persona.name[0] || "人"}
            </span>
            <div>
              <strong>{data?.overview.persona.name || "你的角色"}</strong>
              <small>
                {data?.overview.settings.x_username
                  ? "@" + data.overview.settings.x_username
                  : "还没有连接 X"}
              </small>
            </div>
            <button
              aria-label="退出登录"
              onClick={() =>
                void (async () => {
                  try {
                    await flushDrafts();
                    await api("/auth/logout", "POST", {});
                    setCsrf("");
                    setAuth("login");
                    setData(null);
                  } catch (e) {
                    notify((e as Error).message, true);
                  }
                })()
              }
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        <div className="topline">
          <span>{nav.find((n) => n.id === page)?.label}</span>
          <span>
            <span
              className={`live-dot ${data?.overview.settings.paused ? "paused" : ""}`}
            />
            {data?.overview.settings.paused
              ? "同步与生成已暂停"
              : "正在准备内容"}
          </span>
        </div>
        {data && (
          <BrowserConnection visible={page === "settings"} actions={actions} />
        )}
        {data ? (
          <div className="page" key={page}>
            <TaskActivity tasks={data.overview.tasks} actions={actions} />
            {page === "home" ? (
              <Home data={data} actions={actions} />
            ) : page === "persona" ? (
              <PersonaPage data={data} actions={actions} />
            ) : page === "inbox" ? (
              <InboxPage data={data} actions={actions} />
            ) : page === "compose" ? (
              <Compose data={data} actions={actions} />
            ) : page === "history" ? (
              <History data={data} actions={actions} />
            ) : (
              <SettingsPage data={data} actions={actions} />
            )}
          </div>
        ) : (
          <div className="loading">
            <LoaderCircle className="spin" />
            正在读取工作室…
          </div>
        )}
      </main>
      {notification}
    </div>
  );
}
