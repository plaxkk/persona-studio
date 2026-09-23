import PersonaImport from "../components/PersonaImport";
import { useState } from "react";
import { Send, Save, Upload, Sparkles } from "lucide-react";
import { api } from "../api";
import type { Studio, Actions, Persona } from "../types";
import { Heading, Field, Button, Badge } from "../components/UI";
export default function PersonaPage({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const [form, setForm] = useState<Persona>(data.overview.persona),
    [chat, setChat] = useState(""),
    [feedback, setFeedback] = useState(""),
    [busy, setBusy] = useState(false),
    [versions, setVersions] = useState<{ version: number; body: Persona }[]>(
      [],
    );
  const pending = data.overview.tasks.some(
    (t) => t.kind === "chat" && ["queued", "running"].includes(t.status),
  );
  return (
    <>
      <Heading
        title="认识你的角色"
        description="决定它关心什么、如何表达，再用聊天慢慢找到它的声音。"
        action={<Badge>虚构 AI 人格 · v{form.version}</Badge>}
      />
      <PersonaImport actions={actions} onApply={setForm} />
      <div className="persona-grid">
        <section className="surface">
          <h2>人格设定</h2>
          <details>
            <summary
              onClick={() =>
                void api<{ version: number; body: Persona }[]>(
                  "/persona/versions",
                ).then(setVersions)
              }
            >
              查看历史版本与回退
            </summary>
            <p className="muted">
              选择旧版本可载入下方表单，检查后点击“保存人格”。历史版本仍保留，已导入的风格语料可在内容资料中另行管理。
            </p>
            {versions
              .filter((v) => v.version !== form.version)
              .map((v) => (
                <Button
                  key={v.version}
                  onClick={() => setForm({ ...v.body, version: form.version })}
                >
                  载入 v{v.version}
                </Button>
              ))}
          </details>
          <form
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void actions.run(async () => {
                const p = await api<Persona>("/persona", "PUT", form);
                setForm(p);
              }, "人格已保存");
            }}
          >
            {(
              ["name", "identity", "voice", "interests", "boundaries"] as const
            ).map((key, i) => (
              <Field
                key={key}
                label={
                  [
                    "角色名字",
                    "它是谁",
                    "说话的方式",
                    "兴趣与话题",
                    "不想涉及的内容",
                  ][i]
                }
                hint={
                  key === "identity"
                    ? "保持清晰的虚构身份，不冒充真实人物。"
                    : undefined
                }
              >
                {key === "name" ? (
                  <input
                    value={form[key]}
                    onChange={(e) =>
                      setForm({ ...form, [key]: e.target.value })
                    }
                    required
                    maxLength={60}
                  />
                ) : (
                  <textarea
                    rows={key === "voice" ? 4 : 3}
                    value={form[key]}
                    onChange={(e) =>
                      setForm({ ...form, [key]: e.target.value })
                    }
                    placeholder={
                      key === "interests"
                        ? "例如：日常观察、摄影、游戏、设计"
                        : undefined
                    }
                  />
                )}
              </Field>
            ))}
            <Button type="submit" tone="primary">
              <Save size={16} />
              保存人格
            </Button>
          </form>
        </section>
        <div className="stack">
          <section className="surface chat-surface">
            <div className="section-title">
              <h2>和它聊一聊</h2>
              <Badge>
                {data.overview.settings.engine === "hermes"
                  ? "Hermes"
                  : "OpenClaw"}
              </Badge>
            </div>
            <p className="muted">
              试聊不会发布到 X，暂停自动任务时也可以使用。
            </p>
            <div className="chat-log" aria-live="polite">
              {!data.messages.length && (
                <div className="chat-intro">
                  <Sparkles size={24} />
                  <p>
                    试着问它：
                    <br />
                    “今天有什么想说的？”
                  </p>
                </div>
              )}
              {data.messages.map((m) => (
                <div key={m.id} className={`bubble ${m.role}`}>
                  <small>
                    {m.role === "user" ? "你" : data.overview.persona.name}
                  </small>
                  <p>{m.text}</p>
                </div>
              ))}
              {pending && <p className="muted">正在组织语言…</p>}
            </div>
            <form
              className="chat-input"
              onSubmit={(e) => {
                e.preventDefault();
                void actions.run(async () => {
                  await api("/generate", "POST", { kind: "chat", text: chat });
                  setChat("");
                });
              }}
            >
              <input
                aria-label="试聊消息"
                placeholder="对它说点什么…"
                value={chat}
                onChange={(e) => setChat(e.target.value)}
                maxLength={12000}
              />
              <Button
                type="submit"
                tone="primary"
                disabled={!chat.trim() || pending}
                aria-label="发送试聊"
              >
                <Send size={17} />
              </Button>
            </form>
          </section>
          <section className="surface">
            <h2>让它更像你想象的样子</h2>
            <Field label="给角色的反馈">
              <textarea
                rows={3}
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="例如：可以更直接，不用每次都安慰对方。"
              />
            </Field>
            <Button
              disabled={!feedback.trim()}
              onClick={() =>
                void actions.run(async () => {
                  await api("/persona/feedback", "POST", { text: feedback });
                  setFeedback("");
                }, "反馈将用于后续写作")
              }
            >
              记住这条反馈
            </Button>
            <div className="divider" />
            <Field
              label="导入表达素材"
              hint="TXT、Markdown、JSON、JSONL、CSV。语料会先净化，再用于风格参考。"
            >
              <input
                type="file"
                accept=".txt,.md,.json,.jsonl,.csv"
                disabled={busy}
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  if (file.size > 1_000_000) {
                    actions.notify("请选择 1 MB 以内的文本文件。", true);
                    return;
                  }
                  setBusy(true);
                  await actions.run(async () => {
                    const r = await api<{ imported: number }>(
                      "/persona/corpus",
                      "POST",
                      { filename: file.name, content: await file.text() },
                    );
                    actions.notify(`已导入 ${r.imported} 条净化后的素材`);
                  });
                  setBusy(false);
                  e.target.value = "";
                }}
              />
            </Field>
            {busy && (
              <p className="muted">
                <Upload size={14} />
                正在蒸馏素材…
              </p>
            )}
          </section>
        </div>
      </div>
    </>
  );
}
