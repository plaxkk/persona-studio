import { desktopMode } from "../desktop";
import { useEffect, useState } from "react";
import {
  Check,
  Link2,
  Plug,
  ShieldCheck,
  Save,
  Download,
  ChevronRight,
} from "lucide-react";
import { api, label, time } from "../api";
import type { Studio, Actions, Engine } from "../types";
import { Heading, Field, Button, Badge } from "../components/UI";
function EngineCard({
  engine,
  active,
  actions,
}: {
  engine: Engine;
  active: boolean;
  actions: Actions;
}) {
  const [model, setModel] = useState(engine.config.model || ""),
    [url, setUrl] = useState(engine.config.base_url || ""),
    [key, setKey] = useState("");
  if (!engine.supported)
    return (
      <div className="engine-planned">
        <strong>{engine.id === "codex" ? "Codex" : "Claude Code"}</strong>
        <Badge>后续接入</Badge>
      </div>
    );
  return (
    <article className={`engine-card ${active ? "selected" : ""}`}>
      <div className="section-title">
        <h3>{engine.id === "hermes" ? "Hermes" : "OpenClaw"}</h3>
        {active ? (
          <Badge tone="green">
            <Check size={13} />
            正在使用
          </Badge>
        ) : (
          <Badge>{engine.installed ? label(engine.status) : "未安装"}</Badge>
        )}
      </div>
      <p className="muted">
        {engine.installed
          ? "已检测到本机引擎"
          : "未检测到引擎，请按部署指引安装后重试"}{" "}
        · {label(engine.status)}
      </p>
      {desktopMode ? (
        <p className="description">
          模型配置和密钥请在{" "}
          <a href="http://127.0.0.1:18880" target="_blank" rel="noreferrer">
            本机设置
          </a>
          中修改。
        </p>
      ) : (
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void actions.run(async () => {
              await api("/engines/" + engine.id, "PUT", {
                model,
                base_url: url,
                api_key: key,
              });
              setKey("");
            }, "已保存，请测试连接");
          }}
        >
          <Field label={engine.id + " 模型名称"}>
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="模型 ID"
              required
            />
          </Field>
          <Field
            label={engine.id + " 模型接口地址"}
            hint="OpenAI 兼容接口；远程地址使用 HTTPS。"
          >
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…/v1"
              required
            />
          </Field>
          <Field
            label={engine.id + " API Key"}
            hint={
              engine.key_present
                ? "已保存；留空保持原值。"
                : "仅保存在你的服务器，不会回显。"
            }
          >
            <input
              type="password"
              autoComplete="new-password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={
                engine.key_present
                  ? "已保存 · 如需更换请输入新值"
                  : "输入 API Key"
              }
            />
          </Field>
          <Button type="submit">
            <Save size={15} />
            保存配置
          </Button>
        </form>
      )}
      <div className="actions spaced-small">
        <Button
          disabled={!engine.installed}
          onClick={() =>
            void actions.run(
              () => api("/engines/" + engine.id + "/verify", "POST", {}),
              "已开始真实模型连接测试",
            )
          }
        >
          <Plug size={15} />
          测试连接
        </Button>
        {!active && (
          <Button
            disabled={engine.status !== "ready"}
            onClick={() =>
              void actions.run(
                () => api("/engines/" + engine.id + "/select", "POST", {}),
                "后续任务将使用新引擎",
              )
            }
          >
            切换到此引擎
          </Button>
        )}
      </div>
      {engine.checked > 0 && (
        <small className="muted">最近测试：{time(engine.checked)}</small>
      )}
      {!desktopMode && engine.key_present && (
        <button
          className="text-button danger spaced-small"
          onClick={() =>
            void actions.run(
              () => api("/engines/" + engine.id + "/credentials", "DELETE"),
              "模型密钥已删除",
            )
          }
        >
          删除此引擎的密钥
        </button>
      )}
    </article>
  );
}
export default function SettingsPage({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const s = data.overview.settings;
  const [tab, setTab] = useState("connections"),
    [username, setUsername] = useState(data.connections.x.username),
    [auth, setAuth] = useState(""),
    [ct0, setCt0] = useState(""),
    [cookies, setCookies] = useState<unknown[] | null>(null),
    [owner, setOwner] = useState(data.connections.telegram.owner_id),
    [tg, setTg] = useState(""),
    [enabled, setEnabled] = useState(data.connections.telegram.enabled),
    [settings, setSettings] = useState({
      brand: s.brand,
      sync_interval: s.sync_interval,
      timezone: s.timezone,
      auto_replies: s.auto_replies,
      auto_posts: s.auto_posts,
      per_round: s.per_round,
    }),
    [diagnostic, setDiagnostic] = useState("");
  useEffect(() => {
    setUsername(data.connections.x.username);
  }, [data.connections.x.username]);
  return (
    <>
      <Heading
        title="让工作室按你的方式运转"
        description="账号连接、写作引擎与节奏，都由你掌握。"
      />
      <div className="tabs">
        {[
          ["connections", "账号连接"],
          ["engines", "写作引擎"],
          ["general", "运行与备份"],
        ].map(([id, title]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {title}
          </button>
        ))}
      </div>
      {tab === "connections" && desktopMode && (
        <section className="surface">
          <h2>账号连接保留在本机</h2>
          <p>
            X：{label(data.connections.x.status)} · @
            {data.connections.x.username || "尚未连接"}
          </p>
          <p>Telegram：{label(data.connections.telegram.status)}</p>
          <p>
            请在{" "}
            <a href="http://127.0.0.1:18880" target="_blank" rel="noreferrer">
              本机工作室的设置页
            </a>
            管理 X、Telegram 和浏览器登录。在线面板不会读取 cookie 或模型密钥。
          </p>
        </section>
      )}
      {tab === "connections" && !desktopMode && (
        <div className="settings-grid">
          <section className="surface">
            <div className="section-title">
              <h2>连接 X</h2>
              <Badge
                tone={data.connections.x.status === "ready" ? "green" : ""}
              >
                {label(data.connections.x.status)}
              </Badge>
            </div>
            <p className="description">
              只读取账号互动。发帖、回复、点赞和关注，都由你在 X 完成。
            </p>
            <details>
              <summary>手动连接或导入 Cookie-Editor 文件</summary>
              <form
                className="form-stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  void actions.run(async () => {
                    await api("/connections/x", "PUT", {
                      username,
                      auth_token: auth,
                      ct0,
                      cookies,
                    });
                    setAuth("");
                    setCt0("");
                    setCookies(null);
                  }, "已保存，验证账号后即可读取互动");
                }}
              >
                <Field
                  label="X 用户名"
                  hint="不需要 @；验证时会核对实际登录账号。"
                >
                  <input
                    value={username}
                    onChange={(e) =>
                      setUsername(e.target.value.replace(/^@/, ""))
                    }
                    placeholder="你的账号用户名"
                    required
                    maxLength={15}
                  />
                </Field>
                <Field
                  label="auth_token"
                  hint={
                    data.connections.x.credentials.X_AUTH_TOKEN
                      ? "已保存，留空保持原值。"
                      : "从你手动导出的 Cookie-Editor 凭据中获取。"
                  }
                >
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={auth}
                    onChange={(e) => setAuth(e.target.value)}
                  />
                </Field>
                <Field label="ct0">
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={ct0}
                    onChange={(e) => setCt0(e.target.value)}
                  />
                </Field>
                <Field
                  label="或导入 Cookie-Editor JSON"
                  hint="只接受 x.com / twitter.com 的 auth_token 与 ct0。不会读取浏览器数据库。"
                >
                  <input
                    type="file"
                    accept=".json"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      try {
                        if (file.size > 100000) throw new Error();
                        const content = JSON.parse(await file.text());
                        if (!Array.isArray(content)) throw new Error();
                        setCookies(content);
                        actions.notify("导出文件已读取，点击保存后安全写入");
                      } catch {
                        actions.notify(
                          "文件格式不正确，请使用 Cookie-Editor JSON 导出。",
                          true,
                        );
                      }
                    }}
                  />
                </Field>
                <Button tone="primary" type="submit">
                  <Save size={16} />
                  保存连接
                </Button>
              </form>
              <div className="actions spaced-small">
                <Button
                  onClick={() =>
                    void actions.run(
                      () => api("/connections/x/verify", "POST", {}),
                      "X 账号身份已验证",
                    )
                  }
                >
                  <ShieldCheck size={16} />
                  验证账号
                </Button>
                <button
                  className="text-button danger"
                  onClick={() =>
                    void actions.run(
                      () => api("/connections/x", "DELETE"),
                      "X 凭据已删除，同步已暂停",
                    )
                  }
                >
                  删除凭据
                </button>
              </div>
            </details>
            <p className="security-note">
              <ShieldCheck size={16} />
              登录凭据保存在本机，不会交给写作引擎。
            </p>
          </section>
          <section className="surface">
            <div className="section-title">
              <h2>Telegram 私人遥控器</h2>
              <Badge>可选</Badge>
            </div>
            <p className="description">
              接收草稿提醒，与角色试聊。只有你可以发送指令，群聊不接入。
            </p>
            <form
              className="form-stack"
              onSubmit={(e) => {
                e.preventDefault();
                void actions.run(async () => {
                  await api("/connections/telegram", "PUT", {
                    owner_id: owner,
                    token: tg,
                    enabled,
                  });
                  setTg("");
                }, "Telegram 配置已保存");
              }}
            >
              <Field label="你的 Telegram 数字 ID">
                <input
                  value={owner}
                  onChange={(e) => setOwner(e.target.value)}
                  inputMode="numeric"
                  placeholder="只有这个用户可以控制角色"
                />
              </Field>
              <Field
                label="Bot Token"
                hint={
                  data.connections.telegram.credentials.TELEGRAM_BOT_TOKEN
                    ? "已保存；留空保持原值。"
                    : "由 BotFather 创建机器人后获得。"
                }
              >
                <input
                  type="password"
                  autoComplete="new-password"
                  value={tg}
                  onChange={(e) => setTg(e.target.value)}
                />
              </Field>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                />
                启用 Telegram 私人遥控器
              </label>
              <Button type="submit">
                <Save size={15} />
                保存 Telegram 设置
              </Button>
            </form>
            <p className="muted spaced-small">
              状态：{label(data.connections.telegram.status)}
            </p>
            <div className="help-box">
              <strong>你的私人指令</strong>
              <p>
                /status 查看状态
                <br />
                /drafts 查看最近草稿
                <br />
                /pause 暂停自动任务
                <br />
                发送其他文字，与角色试聊
              </p>
            </div>
            <button
              className="text-button danger"
              onClick={() =>
                void actions.run(
                  () => api("/connections/telegram", "DELETE"),
                  "Telegram 凭据已删除",
                )
              }
            >
              删除 Telegram 凭据
            </button>
          </section>
        </div>
      )}
      {tab === "engines" && (
        <>
          <div className="notice">
            <Plug size={17} />
            引擎只负责理解与写作。切换后保留人设、记忆和草稿，正在进行的任务继续使用原引擎。
          </div>
          <div className="settings-grid">
            {data.overview.engines
              .filter((e) => e.supported)
              .map((e) => (
                <EngineCard
                  key={e.id}
                  engine={e}
                  active={s.engine === e.id}
                  actions={actions}
                />
              ))}
          </div>
          <div className="planned-grid">
            {data.overview.engines
              .filter((e) => !e.supported)
              .map((e) => (
                <EngineCard
                  key={e.id}
                  engine={e}
                  active={false}
                  actions={actions}
                />
              ))}
          </div>
        </>
      )}
      {tab === "general" && (
        <div className="settings-grid">
          <section className="surface">
            <h2>工作室与运行节奏</h2>
            <form
              className="form-stack"
              onSubmit={(e) => {
                e.preventDefault();
                void actions.run(
                  () => api("/settings", "PUT", settings),
                  "运行设置已保存",
                );
              }}
            >
              <Field label="工作室名称">
                <input
                  value={settings.brand}
                  onChange={(e) =>
                    setSettings({ ...settings, brand: e.target.value })
                  }
                  required
                />
              </Field>
              <Field label="时区">
                <input
                  value={settings.timezone}
                  onChange={(e) =>
                    setSettings({ ...settings, timezone: e.target.value })
                  }
                />
              </Field>
              <Field label="同步间隔">
                <select
                  value={settings.sync_interval}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      sync_interval: Number(e.target.value),
                    })
                  }
                >
                  <option value={900}>每 15 分钟</option>
                  <option value={1800}>每 30 分钟</option>
                  <option value={3600}>每 60 分钟</option>
                </select>
              </Field>
              {(["auto_replies", "auto_posts", "per_round"] as const).map(
                (key, i) => (
                  <Field
                    key={key}
                    label={
                      [
                        "每天自动准备的回复草稿",
                        "每天自动准备的原创草稿",
                        "每轮最多准备的互动草稿",
                      ][i]
                    }
                  >
                    <input
                      type="number"
                      min="0"
                      max={[100, 20, 10][i]}
                      value={settings[key]}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          [key]: Number(e.target.value),
                        })
                      }
                    />
                  </Field>
                ),
              )}
              <p className="muted">
                这些是生成预算，不是自动发送次数。手动生成单独计数。
              </p>
              <Button tone="primary" type="submit">
                <Save size={16} />
                保存节奏
              </Button>
            </form>
          </section>
          <div className="stack">
            <section className="surface">
              <h2>备份与诊断</h2>
              <p className="description">
                备份草稿、人设、聊天和操作记录。凭据不包含在数据库备份内。
              </p>
              <Button
                onClick={() =>
                  void actions.run(
                    () => api("/backup", "POST", {}),
                    "备份已保存在本机数据目录",
                  )
                }
              >
                <Download size={16} />
                创建本机备份
              </Button>
              <div className="divider" />
              <Button
                onClick={() =>
                  void actions.run(async () =>
                    setDiagnostic(
                      JSON.stringify(await api("/diagnostics"), null, 2),
                    ),
                  )
                }
              >
                <ChevronRight size={16} />
                查看脱敏诊断
              </Button>
              {diagnostic && <pre className="diagnostic">{diagnostic}</pre>}
            </section>
            <section className="surface">
              <h2>模型任务用量</h2>
              <p className="muted">按工作室时区统计任务，不等于供应商账单。</p>
              {data.overview.usage.length ? (
                data.overview.usage.map((u, i) => (
                  <p key={i}>
                    {u.day} ·{" "}
                    {u.kind === "post"
                      ? "原创"
                      : u.kind === "reply"
                        ? "回复"
                        : u.kind === "chat"
                          ? "试聊"
                          : "连接测试"}{" "}
                    · {u.automatic ? "自动" : "手动"}：{u.count} 次
                  </p>
                ))
              ) : (
                <p className="muted">暂时没有模型任务。</p>
              )}
            </section>
          </div>
        </div>
      )}
    </>
  );
}
