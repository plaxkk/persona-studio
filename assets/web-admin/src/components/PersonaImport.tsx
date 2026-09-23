import { useEffect, useState } from "react";
import { api } from "../api";
import { desktopMode } from "../desktop";
import { Button, Field } from "./UI";
import type { Actions, Persona } from "../types";
type ImportJob = {
  id: string;
  account: string;
  status: string;
  message: string;
  target: number;
  counts: { read: number; usable: number; post: number; reply: number };
  duplicates: number;
  actions: number;
  current_version: number;
  current_persona: Persona;
  profile: Record<string, string>;
  candidate: {
    persona: Omit<Persona, "version">;
    observations: { type: string; statement: string; source_ids: string[] }[];
    limitations: string;
    examples: string[];
  };
  excluded: Record<string, number>;
};
type Preflight = {
  ready: boolean;
  message: string;
  account: string;
  model: string;
  browser_connected: boolean;
  version?: string;
};
const statuses: Record<string, string> = {
  queued: "准备开始",
  collecting: "浏览并采集",
  distilling: "分析与蒸馏",
  waiting_browser: "等待浏览器",
  waiting_login: "等待登录",
  challenge: "等待完成验证",
  account_mismatch: "需要切换账号",
  stalled: "暂未发现更多内容",
  paused: "已暂停",
  interrupted: "服务重启，等待继续",
  failed: "本次未完成",
  insufficient: "样本不足",
  preview: "人设预览",
  applied: "已应用",
  cancelled: "已取消",
};
const fields = [
  "name",
  "identity",
  "voice",
  "interests",
  "boundaries",
] as const;
const labels = ["角色名字", "身份定位", "说话方式", "兴趣话题", "表达边界"];
export default function PersonaImport({
  actions,
  onApply,
  expectedAccount,
}: {
  actions: Actions;
  expectedAccount: string;
  onApply: (p: Persona) => void;
}) {
  const [check, setCheck] = useState<Preflight | null>(null),
    [jobs, setJobs] = useState<ImportJob[]>([]),
    [selected, setSelected] = useState(""),
    [consent, setConsent] = useState(false),
    [target, setTarget] = useState(300),
    [busy, setBusy] = useState(false),
    [edited, setEdited] = useState<ImportJob["candidate"]["persona"] | null>(
      null,
    ),
    [compareVersion, setCompareVersion] = useState(0),
    [error, setError] = useState("");
  const job = jobs.find((j) => j.id === selected) || jobs[0];
  async function refresh() {
    const result = await api<ImportJob[]>("/persona-imports");
    setJobs(result);
  }
  useEffect(() => {
    let alive = true;
    const poll = () => {
      if (alive) void refresh().catch(() => {});
    };
    poll();
    const timer = setInterval(poll, 2500);
    void api<Preflight>("/persona-imports/preflight")
      .then((v) => {
        if (alive) setCheck(v);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    setEdited(job?.candidate?.persona || null);
    setCompareVersion(job?.current_version || 0);
  }, [job?.id, !!job?.candidate?.persona]);
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  const active =
    job && ["queued", "collecting", "distilling"].includes(job.status);
  const waiting =
    job &&
    [
      "paused",
      "waiting_browser",
      "waiting_login",
      "challenge",
      "account_mismatch",
      "stalled",
      "interrupted",
      "failed",
      "insufficient",
    ].includes(job.status);
  async function download(skill: boolean) {
    const r = await api<{ markdown: string; skill: string }>(
      `/persona-imports/${job.id}/export`,
    );
    const url = URL.createObjectURL(
      new Blob([skill ? r.skill : r.markdown], {
        type: "text/markdown;charset=utf-8",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = skill ? "SKILL.md" : "persona.md";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="surface" style={{ marginBottom: 24 }}>
      <h2>从我的 X 生成人设</h2>
      <p>
        让本机 Codex 在专用 Chrome 标签页阅读 @
        {check?.account || job?.account || expectedAccount || "待连接账号"}{" "}
        的资料、推文和回复。采集期间不会修改当前人设。
      </p>
      <p className="muted">
        浏览器操作在本机执行；必要正文会发送给 Codex
        使用的模型服务，消耗相应额度。默认目标为 150 条原创／引用附言和 150
        条回复，可互补，不代表全部历史。不读取私信，不执行发帖或互动。
      </p>
      <p>
        {check?.message ||
          (error ? "等待连接助手更新或授权" : "正在检查连接助手和 Codex…")}{" "}
        {check?.model && `模型：${check.model}`}
      </p>
      {!check?.browser_connected && (
        <p>
          <a
            href={
              desktopMode
                ? "/downloads/persona-studio-browser.zip"
                : "/api/v1/browser/extension.zip"
            }
          >
            下载连接助手 1.3
          </a>{" "}
          · 解压后在 Chrome 扩展管理页加载或重新加载，再在扩展中授权连接。
        </p>
      )}
      <div className="button-row">
        <Button
          disabled={busy}
          onClick={() =>
            void run(async () =>
              setCheck(await api<Preflight>("/persona-imports/preflight")),
            )
          }
        >
          重新检查
        </Button>
        <select
          aria-label="采集目标"
          value={target}
          onChange={(e) => setTarget(Number(e.target.value))}
        >
          <option value={20}>20 条 · 小批量验证</option>
          <option value={100}>100 条</option>
          <option value={300}>300 条</option>
        </select>
      </div>
      <label>
        <input
          type="checkbox"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
        />{" "}
        我同意将本次必要正文交给所示模型分析
      </label>
      <div>
        <Button
          tone="primary"
          disabled={!check?.ready || !consent || busy}
          onClick={() =>
            void run(async () => {
              const j = await api<ImportJob>("/persona-imports", "POST", {
                target,
                consent,
              });
              setSelected(j.id);
            })
          }
        >
          开始读取我的 X
        </Button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {jobs.length > 1 && (
        <select
          aria-label="采集记录"
          value={job?.id || ""}
          onChange={(e) => setSelected(e.target.value)}
        >
          {jobs.map((j) => (
            <option key={j.id} value={j.id}>
              @{j.account} · {statuses[j.status]} · {j.counts.usable} 条
            </option>
          ))}
        </select>
      )}
      {job && (
        <div aria-live="polite" className="stack">
          <h3>{statuses[job.status] || job.status}</h3>
          <p>
            有效 {job.counts.usable} / {job.target} 条，原创／引用附言{" "}
            {job.counts.post} 条，回复 {job.counts.reply} 条。已读取{" "}
            {job.counts.read} 个唯一帖子，重复遇到 {job.duplicates} 次，浏览动作{" "}
            {job.actions} / 240。
          </p>
          {job.message && <p>{job.message}</p>}
          {!!Object.keys(job.excluded).length && (
            <p>
              排除：
              {Object.entries(job.excluded)
                .map(
                  ([k, v]) =>
                    `${({ truncated: "正文不完整", translated: "未获取原文", repost: "纯转帖", no_text: "无文字", advertisement: "广告" } as Record<string, string>)[k] || k} ${v}`,
                )
                .join("，")}
            </p>
          )}
          <div className="button-row">
            {active && (
              <Button
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    api(`/persona-imports/${job.id}/pause`, "POST"),
                  )
                }
              >
                暂停
              </Button>
            )}
            {waiting && job.status !== "insufficient" && (
              <Button
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    api(`/persona-imports/${job.id}/resume`, "POST"),
                  )
                }
              >
                继续并重新核对账号
              </Button>
            )}
            {waiting && job.counts.usable >= 20 && (
              <Button
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    api(`/persona-imports/${job.id}/analyze`, "POST"),
                  )
                }
              >
                用已有样本生成
              </Button>
            )}
            {(active || waiting) && (
              <Button
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    api(`/persona-imports/${job.id}/cancel`, "POST"),
                  )
                }
              >
                取消采集
              </Button>
            )}
          </div>
          {edited && (
            <>
              <h3>
                人设预览 ·{" "}
                {job.status === "applied" ? "已应用" : "尚未自动应用"}
              </h3>
              <p>
                与当前 v{compareVersion}{" "}
                比较。下面可修改候选内容，应用后会保存新版本。
              </p>
              {fields.map((k, i) => (
                <Field key={k} label={labels[i]}>
                  <details>
                    <summary>当前内容</summary>
                    <p style={{ whiteSpace: "pre-wrap" }}>
                      {job.current_persona[k]}
                    </p>
                  </details>
                  <textarea
                    rows={k === "name" ? 1 : 4}
                    value={edited[k]}
                    onChange={(e) =>
                      setEdited({ ...edited, [k]: e.target.value })
                    }
                  />
                </Field>
              ))}
              <h3>依据与采样限制</h3>
              {job.candidate.observations.map((o, i) => (
                <p key={i}>
                  {
                    {
                      profile: "资料自述",
                      observation: "样本观察",
                      inference: "不确定推断",
                    }[o.type as "profile"]
                  }
                  ：{o.statement}{" "}
                  {o.source_ids.map((id) => (
                    <a
                      key={id}
                      href={`https://x.com/${job.account}/status/${id}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      原帖依据{" "}
                    </a>
                  ))}
                </p>
              ))}
              <p>{job.candidate.limitations}</p>
              <h3>仿写草稿（新生成，非历史原帖）</h3>
              {job.candidate.examples.map((e, i) => (
                <p key={i} style={{ whiteSpace: "pre-wrap" }}>
                  {e}
                </p>
              ))}
              {job.current_version !== compareVersion && (
                <p role="alert">
                  当前人设已有更新。请重新比较后应用。
                  <Button
                    onClick={() => setCompareVersion(job.current_version)}
                  >
                    确认已比较 v{job.current_version}
                  </Button>
                </p>
              )}
              <div className="button-row">
                <Button
                  tone="primary"
                  disabled={
                    busy ||
                    job.status !== "preview" ||
                    job.current_version !== compareVersion
                  }
                  onClick={() =>
                    void run(async () => {
                      const p = await api<Persona>(
                        `/persona-imports/${job.id}/apply`,
                        "POST",
                        { persona: edited, expected_version: compareVersion },
                      );
                      onApply(p);
                      actions.notify("已保存新版本与风格语料");
                    })
                  }
                >
                  应用人设
                </Button>
                <Button onClick={() => void run(() => download(false))}>
                  下载人设 Markdown
                </Button>
                <Button onClick={() => void run(() => download(true))}>
                  下载 Skill
                </Button>
              </div>
            </>
          )}
          {!active && (
            <Button
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    "删除本次原始采集材料？已经应用的人设与风格语料会保留。",
                  )
                )
                  void run(() =>
                    api(`/persona-imports/${job.id}/delete`, "POST"),
                  );
              }}
            >
              删除本次采集材料
            </Button>
          )}
          <small className="muted">
            原始材料保留 7
            天。关闭网页不影响任务；关闭浏览器或重启服务后需手动继续。登录与验证码请在专用
            X 标签页完成。
          </small>
        </div>
      )}
    </section>
  );
}
