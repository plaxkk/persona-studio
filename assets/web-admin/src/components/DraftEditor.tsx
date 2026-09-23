import { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  Check,
  Copy,
  Save,
  Sparkles,
  Archive,
  RotateCcw,
} from "lucide-react";
import { registerSave } from "../pending";
import { api, safeXUrl, time } from "../api";
import type { Actions, Draft } from "../types";
import { Badge, Button, External } from "./UI";
export function DraftEditor({
  draft,
  username,
  actions,
  compact = false,
}: {
  draft: Draft;
  username: string;
  actions: Actions;
  compact?: boolean;
}) {
  const [text, setText] = useState(draft.text),
    [version, setVersion] = useState(draft.version),
    [state, setState] = useState("已保存"),
    [busy, setBusy] = useState(false),
    [fallback, setFallback] = useState(false),
    [handoff, setHandoff] = useState(false),
    [confirming, setConfirming] = useState(false),
    [resultUrl, setResultUrl] = useState(""),
    [versions, setVersions] = useState<{ version: number; text: string }[]>([]);
  const current = useRef({ text, version });
  current.current = { text, version };
  const saving = useRef<Promise<Draft> | null>(null);
  const savedText = useRef(draft.text);
  const dirty = text !== savedText.current;
  const editable = draft.status === "draft";
  useEffect(() => {
    if (current.current.text === savedText.current) {
      setText(draft.text);
      setVersion(draft.version);
      savedText.current = draft.text;
      current.current = { text: draft.text, version: draft.version };
    }
  }, [draft.version, draft.text]);
  async function save(): Promise<Draft> {
    if (saving.current) {
      await saving.current;
      if (current.current.text === savedText.current)
        return {
          ...draft,
          text: savedText.current,
          version: current.current.version,
        };
      return save();
    }
    if (!editable || current.current.text === savedText.current)
      return { ...draft, version: current.current.version };
    setState("保存中…");
    const snapshot = { ...current.current };
    const operation = (async () => {
      try {
        const saved = await api<Draft>("/drafts/" + draft.id, "PUT", {
          text: snapshot.text,
          version: snapshot.version,
        });
        savedText.current = saved.text;
        current.current.version = saved.version;
        setVersion(saved.version);
        if (current.current.text === snapshot.text) {
          setText(saved.text);
          current.current.text = saved.text;
        }
        setState("已保存");
        await actions.refresh();
        return saved;
      } catch (e) {
        setState("保存失败，内容仍在这里");
        throw e;
      }
    })();
    saving.current = operation;
    try {
      return await operation;
    } finally {
      saving.current = null;
    }
  }
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => registerSave(() => saveRef.current()), [draft.id]);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (current.current.text !== savedText.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, []);
  useEffect(() => {
    if (!dirty || !editable) return;
    setState("尚未保存");
    const timer = setTimeout(() => {
      save().catch((e) => actions.notify((e as Error).message, true));
    }, 1100);
    return () => clearTimeout(timer);
  }, [text]);
  const target =
    draft.kind === "reply"
      ? safeXUrl(draft.source_url || "") || null
      : "https://x.com/compose/post";
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      actions.notify("文案已复制");
      return true;
    } catch {
      setFallback(true);
      actions.notify("浏览器未允许复制，请选中文案手动复制。", true);
      return false;
    }
  }
  async function go() {
    if (!target) {
      actions.notify("原帖链接不可用，请先核对原帖。", true);
      return;
    }
    // Opening synchronously preserves the user gesture; no draft text is placed in a URL.
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    setBusy(true);
    try {
      const copied = await copy();
      const saved = dirty ? await save() : { text };
      setHandoff(true);
      if (saved.text !== text) {
        popup?.close();
        setFallback(true);
        actions.notify(
          "保存时已隐藏可能的凭据，请重新核对并复制当前文案。",
          true,
        );
        return;
      }
      if (copied && popup) {
        popup.location.href = target;
        await api("/drafts/" + draft.id + "/opened", "POST", {});
        actions.notify("已打开 X，发布或回复仍由你完成。");
      } else {
        popup?.close();
        actions.notify(
          copied
            ? "弹窗未打开，请使用下方链接继续。"
            : "请手动复制后，再点击下方链接。",
          !copied,
        );
      }
      await actions.refresh();
    } catch (e) {
      popup?.close();
      actions.notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className={`draft-editor ${compact ? "compact" : ""}`}>
      <div className="draft-meta">
        <Badge>{draft.kind === "reply" ? "回复草稿" : "原创草稿"}</Badge>
        <span>{time(draft.updated)}</span>
        <span className="save-state">{state}</span>
      </div>
      {draft.source_text && (
        <blockquote className="source-preview">
          <strong>@{draft.source_author}</strong>
          <p>{draft.source_text}</p>
          {target && <External url={target}>查看原帖</External>}
        </blockquote>
      )}
      <textarea
        aria-label="草稿正文"
        className="draft-text"
        value={text}
        readOnly={!editable}
        onChange={(e) => setText(e.target.value)}
        placeholder="把想说的话留在这里…"
        rows={compact ? 4 : 7}
      />
      <div className="draft-footnote">
        <span>{Array.from(text).length} 个字符 · 虚构人格内容</span>
        <span>最终内容与账号请在 X 核对</span>
      </div>
      <div className="draft-tools">
        <Button
          tone="primary"
          disabled={!text.trim() || !target}
          busy={busy}
          onClick={go}
        >
          <ArrowUpRight size={16} />
          {draft.kind === "reply" ? "复制并去回复" : "复制并去 X 发布"}
        </Button>
        <Button onClick={() => void copy()} disabled={!text}>
          <Copy size={16} />
          复制文案
        </Button>
        {editable && (
          <Button
            disabled={!dirty}
            onClick={() => void actions.run(save, "草稿已保存")}
          >
            <Save size={16} />
            保存
          </Button>
        )}
        {editable && (
          <Button
            onClick={() =>
              void actions.run(async () => {
                const saved = dirty ? await save() : { version };
                return api("/generate", "POST", {
                  kind: draft.kind,
                  text: "重新生成一个不同表达的版本",
                  post_id: draft.post_id,
                  draft_id: draft.id,
                  expected_version: saved.version,
                });
              }, "已加入生成队列")
            }
          >
            <Sparkles size={16} />
            换个写法
          </Button>
        )}
      </div>
      {(fallback || handoff) && (
        <div className="handoff">
          <strong>接下来，在 X 完成最后一步</strong>
          <p>
            预期账号：{username ? "@" + username : "尚未连接"}。X
            会使用浏览器当前登录的账号，请核对后再发送。
          </p>
          {fallback && (
            <textarea
              aria-label="手动复制文案"
              value={text}
              readOnly
              onFocus={(e) => e.target.select()}
            />
          )}
          {target && (
            <a
              href={target}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() =>
                void actions.run(() =>
                  api("/drafts/" + draft.id + "/opened", "POST", {}),
                )
              }
            >
              打开{draft.kind === "reply" ? "原帖" : " X 发布页"} ↗
            </a>
          )}
          <p className="muted">打开页面不会被记为发布成功。</p>
        </div>
      )}
      <div className="draft-secondary">
        {draft.status === "confirmed" ? (
          <Badge tone="green">
            <Check size={13} />
            用户确认完成 · 未经平台验证
          </Badge>
        ) : (
          editable && (
            <button
              className="text-button"
              onClick={() => setConfirming(!confirming)}
            >
              <Check size={15} />
              我已在 X 完成
            </button>
          )
        )}
        <button
          className="text-button"
          onClick={() =>
            void actions.run(async () => {
              setVersions(await api("/drafts/" + draft.id + "/versions"));
            })
          }
        >
          <RotateCcw size={14} />
          版本记录
        </button>
        {editable && (
          <button
            className="text-button"
            onClick={() =>
              void actions.run(async () => {
                if (dirty) await save();
                return api("/drafts/" + draft.id + "/archive", "POST", {});
              }, "已归档")
            }
          >
            <Archive size={14} />
            归档
          </button>
        )}
      </div>
      {draft.result_url && safeXUrl(draft.result_url) && (
        <p className="spaced-small">
          <External url={safeXUrl(draft.result_url)!}>
            查看你记录的 X 结果
          </External>
        </p>
      )}
      {confirming && (
        <div className="confirm-box">
          <p>此记录只代表你的确认，产品不会自动核验 X 上的发布结果。</p>
          <input
            aria-label="结果链接"
            value={resultUrl}
            onChange={(e) => setResultUrl(e.target.value)}
            placeholder="可选：粘贴你发布后的 X 帖子链接"
          />
          <Button
            onClick={() =>
              void actions.run(async () => {
                if (dirty) await save();
                await api("/drafts/" + draft.id + "/confirm", "POST", {
                  result_url: resultUrl,
                });
                setConfirming(false);
              }, "已记录为用户确认完成")
            }
          >
            确认完成
          </Button>
        </div>
      )}
      {versions.length > 0 && (
        <details open className="version-list">
          <summary>已保存的版本</summary>
          {versions.map((v) => (
            <p key={v.version}>
              <strong>v{v.version}</strong> {v.text || "空白草稿"}
            </p>
          ))}
        </details>
      )}
    </article>
  );
}
