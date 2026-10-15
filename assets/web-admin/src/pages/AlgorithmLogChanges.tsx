import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { GitCommitHorizontal, RefreshCw } from "lucide-react";
import { api, time } from "../api";
import type { Actions } from "../types";
import { Badge, Button, Empty, Field, Heading } from "../components/UI";
import type { UpstreamRead } from "./AlgorithmLogSignals";

// 不知道就写不知道。这一页最常见的两个「不知道」必须分开写：本机还没查过上游
// （`last_checked` 为空）、以及这一次查了但没读到（`last_status` 是失败那一类）。
// 两者都不是「上游没有变化」。
const NO_DATA = "暂无数据";
// 检查结果里属于「查到了」的那几种。它们和「没查成」在页面上必须是两句话。
const CHECKED = new Set(["baseline", "not_modified", "ok", "changed"]);

type Module = {
  module: string;
  label: string;
  why: string;
  files: number;
  added: number;
  removed: number;
  paths: string[];
  paths_truncated: boolean;
};
type Event = {
  id: string;
  repo: string;
  sha: string;
  branch: string;
  commit_at: number | null;
  author: string;
  message: string;
  url: string;
  parent_sha: string;
  // 0 = 两次检查之间确实没有别的提交；null = 有别的提交，但本机数不出有几条。
  intervening: number | null;
  files_json: { path: string; added: number; removed: number }[];
  modules_json: Module[];
  files_listed: number;
  files_total: number | null;
  files_truncated: number;
  summary: string;
  coverage_gap: string;
  created: number;
  proposal_status: string | null;
  notify_status: string | null;
  notified_at: number | null;
  engine: string | null;
  engine_error: string | null;
  impact: string | null;
  impact_at: number | null;
  has_analysis: number;
};
type Detail = Event & {
  analysis: string | null;
  notify_note: string | null;
};
type Ledger = {
  repo: string;
  watching: boolean;
  state: {
    repo: string;
    branch: string;
    last_sha: string;
    last_commit_at: number | null;
    baseline: number;
    last_checked: number | null;
    last_ok: number | null;
    last_status: string;
    last_note: string;
    checked_count: number;
  };
  events: Event[];
  statuses: string[];
  status_label: Record<string, string>;
  check_labels: { status: string; label: string }[];
  thresholds: Record<string, number>;
};

const LINE: Record<string, string> = {
  check_interval: "自动检查间隔（秒）",
  file_cap: "GitHub 一次最多列几个文件",
  paths_per_module: "每个模块列几条路径",
  events_shown: "一页显示几条事件",
};

export default function AlgorithmLogChanges({
  actions,
  tabs,
  active,
  onLedger,
}: {
  actions: Actions;
  tabs?: ReactNode;
  active: boolean;
  // 「当前逻辑」那一半要用这里的 `state.last_sha` / `events[].sha` 去比清单钉的
  // 是哪条提交。它自己不发请求（那是那个标签的卖点），所以由这一半把读到的东西
  // 递上去。递上去的是三个字段，不是整份 ledger。
  onLedger?: (read: UpstreamRead) => void;
}) {
  const [ledger, setLedger] = useState<Ledger | null>(null),
    [loading, setLoading] = useState(true),
    [selected, setSelected] = useState(""),
    [detail, setDetail] = useState<Detail | null>(null),
    [impact, setImpact] = useState("");

  const load = useCallback(async (keep?: string) => {
    const next = await api<Ledger>("/algorithm/events");
    setLedger(next);
    onLedger?.({
      last_sha: next.state?.last_sha || "",
      last_checked: next.state?.last_checked ?? null,
      shas: next.events.map((row) => row.sha),
      // 「检查过几次」用来分辨「工作室读过上游、只是没读到清单那条」和
      // 「工作室根本还没开始读」。两者在页面上必须是两句话。
      checked: next.state?.checked_count ?? 0,
    });
    const pick =
      (keep && next.events.some((row) => row.id === keep) ? keep : "") ||
      next.events[0]?.id ||
      "";
    setSelected(pick);
  }, [onLedger]);

  // 挂上之后只藏不卸：人写了一半的判断不能因为切了个标签就没了。但藏起来的那
  // 一半不该继续读——所以挂载和读数据分开，`active` 管读。（06C 的写法。）
  useEffect(() => {
    if (!active) return;
    void actions.run(() => load()).finally(() => setLoading(false));
  }, [actions, load, active]);

  // 事件那一行只带「有没有分析过」，正文和人的判断都在单取那一份里。所以选中的
  // 事件是谁，就重新取谁——换一条不换草稿，「保存」会把上一条的判断写错地方。
  useEffect(() => {
    if (!active) return;
    if (!selected) {
      setDetail(null);
      setImpact("");
      return;
    }
    void actions.run(async () => {
      const next = await api<{ event: Detail }>(
        `/algorithm/proposals/${selected}`,
      );
      setDetail(next.event);
      setImpact(next.event.impact || "");
    });
  }, [actions, selected, active]);

  const check = (analyze: boolean) =>
    void actions.run(async () => {
      await api("/algorithm/check", "POST", { analyze });
      await load(selected);
    }, analyze ? "已让工作室去读一次，并顺带让引擎读一遍" : "已让工作室去读一次上游");

  const judge = (status: string) =>
    void actions.run(async () => {
      if (!detail) return;
      await api(`/algorithm/impact/${detail.sha}`, "POST", { status, impact });
      await load(selected);
      const next = await api<{ event: Detail }>(
        `/algorithm/proposals/${selected}`,
      );
      setDetail(next.event);
      setImpact(next.event.impact || "");
    }, `已记为「${ledger?.status_label[status] || status}」`);

  const toggle = () =>
    void actions.run(async () => {
      await api(`/algorithm/watching/${ledger?.watching ? "off" : "on"}`, "POST");
      await load(selected);
    });

  const state = ledger?.state;
  const events = ledger?.events || [];
  const label = (status: string) =>
    ledger?.check_labels.find((one) => one.status === status)?.label || status;
  const word = (status: string | null) =>
    status ? ledger?.status_label[status] || status : NO_DATA;

  return (
    <>
      <Heading
        eyebrow="06D 算法更新跟踪"
        title="上游动了什么，记在本机"
        description={
          "盯的是 X 官方开源的那份 For You 算法。这里只回答三件事：上游动没动、" +
          "本机从文件清单里算出来的「改了什么」、以及这次改动对我们意味着什么。" +
          "第三件事分两半写——一半是引擎读出来的，一半是人给的判断，它们不会互相覆盖。" +
          "上游可能几周都不动；不动就写「暂无数据」，不编事件。"
        }
        action={
          <Badge tone={ledger?.watching ? "green" : ""}>
            {ledger?.watching ? "自动盯着" : "没在自动盯"}
          </Badge>
        }
      />
      {active && tabs}
      <div className="stack">
        <section className="surface">
          <div className="section-title">
            <h2>上游在哪儿</h2>
            {state && (
              <span className="muted">
                检查过 {state.checked_count} 次
              </span>
            )}
          </div>
          {loading ? (
            <p className="muted">正在读取…</p>
          ) : !ledger || !state ? (
            <p className="muted">{NO_DATA}</p>
          ) : (
            <>
              <dl className="bench-metrics">
                <div>
                  <dt>仓库</dt>
                  <dd>{ledger.repo}</dd>
                </div>
                <div>
                  <dt>分支</dt>
                  <dd>{state.branch || NO_DATA}</dd>
                </div>
                <div>
                  <dt>上次看到哪条提交</dt>
                  <dd>
                    {/* 第一次检查只是把当前那条记成起点，所以「起点」和
                        「我们看过的那条」是同一件事，得说出来。 */}
                    {state.last_sha ? (
                      <>
                        <code>{state.last_sha.slice(0, 7)}</code>
                        <small className="muted">
                          {" "}
                          {state.baseline ? "· 起点" : ""}
                          {state.last_commit_at
                            ? ` · 提交于 ${time(state.last_commit_at)}`
                            : ""}
                        </small>
                      </>
                    ) : (
                      <span className="muted">还没看过</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>上次检查</dt>
                  <dd>
                    {state.last_checked ? (
                      time(state.last_checked)
                    ) : (
                      <span className="muted">还没查过</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>最近一次是查成功了吗</dt>
                  <dd>
                    {state.last_ok ? (
                      time(state.last_ok)
                    ) : (
                      <span className="muted">还没有一次读成功</span>
                    )}
                  </dd>
                </div>
              </dl>
              <Field
                label="上一次检查的结论"
                hint="这是工作室自己写的话，原始状态码在下面这张表里对得上。"
              >
                <p className={CHECKED.has(state.last_status) ? "notice" : "notice warning"}>
                  <strong>{label(state.last_status)}</strong>
                  {state.last_note ? `：${state.last_note}` : "。"}
                  {state.last_status && !CHECKED.has(state.last_status) && (
                    <>
                      {" "}
                      这一次没读到上游——「读不到」和「上游没动」不是一回事，所以
                      这一趟不写事件、也不改「上次看到哪条提交」。
                    </>
                  )}
                  {!state.last_status && " 还没有检查过。"}
                </p>
              </Field>
              <div className="actions">
                <Button type="button" tone="primary" onClick={() => check(false)}>
                  <RefreshCw size={16} />
                  现在去读一次
                </Button>
                <Button type="button" onClick={() => check(true)}>
                  <GitCommitHorizontal size={16} />
                  读一次，并让引擎读一遍
                </Button>
                <Button type="button" onClick={toggle}>
                  {ledger.watching ? "不再自动盯" : "开始自动盯"}
                </Button>
              </div>
              <p className="notice">
                自动那趟每天一次；上一次是网络或限流这类碰运气的问题时，等一小时就再试。
                手动这一趟和自动那趟用同一个去重键，所以点了不会多排一条。
              </p>
            </>
          )}
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>改动记录</h2>
            <span className="muted">新到旧，最多 {ledger?.thresholds.events_shown ?? NO_DATA} 条</span>
          </div>
          {!events.length ? (
            <Empty
              title="还没有记下过一条改动"
              description={
                state?.last_checked
                  ? "检查过了，但上游从第一次记下起点之后就没动过。真动了这里会出现一条。"
                  : "还没查过上游。上面按一次「现在去读一次」，第一次只会记下起点，之后再动才会有记录。"
              }
            />
          ) : (
            <div className="weekly-list">
              {events.map((row) => (
                <button
                  key={row.id}
                  className={`weekly-list-item ${row.id === selected ? "active" : ""}`}
                  onClick={() => setSelected(row.id)}
                >
                  <span>
                    <strong>
                      <code>{row.sha.slice(0, 7)}</code>{" "}
                      {(row.message || "（没有说明）").split("\n")[0].slice(0, 60)}
                    </strong>
                    <small>
                      {row.commit_at ? time(row.commit_at) : "提交时间拿不到"}
                      {row.author && ` · ${row.author}`}
                      {" · "}
                      {word(row.proposal_status)}
                      {row.has_analysis ? " · 引擎读过了" : " · 还没让引擎读"}
                    </small>
                  </span>
                  <span className="muted">{row.summary}</span>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>{detail ? `这次改动 ${detail.sha.slice(0, 7)}` : "这次改动"}</h2>
            {detail && (
              <span className="muted">
                记于 {time(detail.created)}
                {detail.notify_status && ` · 通知：${detail.notify_status}`}
              </span>
            )}
          </div>
          {!detail ? (
            <Empty
              title="还没选一条"
              description="上面选一条改动，这里会把它分开写成三段：本机算出来的、引擎读出来的、你给的判断。"
            />
          ) : (
            <>
              <Field
                label="本机算出来的（不猜）"
                hint="这一段只由文件清单算出来，没有任何推断。清单本身读不全的时候，下面会写明看不到了多少。"
              >
                <p>{detail.summary}</p>
                {detail.modules_json.length ? (
                  <ul className="score-rules">
                    {detail.modules_json.map((item) => (
                      <li key={item.module}>
                        <strong>{item.label}</strong>（{item.module}）：{item.why}。
                        这次动了 {item.files} 个文件（+{item.added} / −{item.removed}）
                        {item.paths.length > 0 && (
                          <>
                            ，例如{" "}
                            <code>{item.paths[0]}</code>
                            {item.paths.length > 1 &&
                              ` 等 ${item.files} 个`}
                          </>
                        )}
                        {/* 路径只列前几条，列不满的部分说清楚，不假装列全了。 */}
                        {item.paths_truncated && (
                          <span className="muted">
                            （路径只列了前 {ledger?.thresholds.paths_per_module} 条）
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">文件清单是空的，所以按模块说不出来。</p>
                )}
              </Field>
              <Field
                label="这次没看到的部分"
                hint="没有这一栏的时候，读的人只能猜「清单是全的」。"
              >
                {detail.coverage_gap ? (
                  <p className="notice warning">{detail.coverage_gap}</p>
                ) : (
                  <p className="muted">
                    这一次的文件清单是全的：GitHub 给了 {detail.files_total} 个文件，
                    一共列出来 {detail.files_listed} 个。
                  </p>
                )}
              </Field>
              <Field
                label="引擎读出来的那一半"
                hint="它读的是上面这些事实，允许答「从这些事实里读不出来」。这一栏是它说的，不是本机算的。"
              >
                {detail.analysis ? (
                  <>
                    <p>{detail.analysis}</p>
                    <p className="muted">读这条的是 {detail.engine || NO_DATA}。</p>
                  </>
                ) : detail.engine_error ? (
                  // 「让了但它没答」和「还没让」必须分开写，否则页面上就是一片空白。
                  <p className="notice warning">
                    让引擎读过了，它没答上来：{detail.engine_error}。事实那一段不受影响，已经记下了。
                  </p>
                ) : (
                  <p className="muted">
                    还没让引擎读这条。上面按「读一次，并让引擎读一遍」；不跑也不影响这次改动被记下来。
                  </p>
                )}
              </Field>
              <form
                className="form-stack spaced-small"
                onSubmit={(e) => {
                  e.preventDefault();
                  judge(ledger?.statuses[0] || "proposed");
                }}
              >
                <Field
                  label="这次的改动，我们打算怎么办"
                  hint="写的是判断和理由，不是结论。下次回看这一个模块的时候，靠的是这几行字。"
                >
                  <textarea
                    value={impact}
                    onChange={(e) => setImpact(e.target.value)}
                    maxLength={2000}
                    rows={4}
                    placeholder="例如：排序权重换了默认值，我们那批回复帖的曝光可能变，先观察一周互动率。"
                  />
                </Field>
                <div className="actions">
                  {/* 四个按钮都要写明 type：`Button` 没有默认 type，在 form 里不写
                      就是 submit，点哪一个都会当成点第一个。 */}
                  {ledger?.statuses.map((status, index) => (
                    <Button
                      key={status}
                      type={index === 0 ? "submit" : "button"}
                      tone={status === "adopted" ? "primary" : undefined}
                      onClick={
                        index === 0 ? undefined : () => judge(status)
                      }
                    >
                      {ledger.status_label[status] || status}
                    </Button>
                  ))}
                </div>
                {detail.impact_at && (
                  <p className="muted">
                    上一次判断记于 {time(detail.impact_at)}；上面的动作会覆盖它，
                    但不会动「本机算出来的」那一段。
                  </p>
                )}
              </form>
              <Field label="原始那一条" hint="对不上就去上游看原始提交。">
                <p className="muted">
                  <code>{detail.sha}</code>
                  {detail.parent_sha && (
                    <>
                      {" "}
                      · 父提交 <code>{detail.parent_sha.slice(0, 7)}</code>
                    </>
                  )}
                </p>
                <p className="muted">
                  {/* 0 和「看不到」不能混：0 是说两次检查之间确实没有别的提交。 */}
                  {detail.intervening === 0
                    ? "这条提交的父提交就是我们上次看到的那条，说明中间没有别的提交。"
                    : "这条提交的父提交不是我们上次看到的那条，中间还有别的提交，有几条本机看不到。"}
                </p>
                <p className="muted">{detail.url}</p>
              </Field>
            </>
          )}
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>这个页在量什么</h2>
          </div>
          {!ledger ? (
            <p className="muted">正在读取…</p>
          ) : (
            <>
              <Field
                label="一次检查可能是哪几种结果"
                hint="上面那七种是「查到了」，下面五种是「这一次没查成」。后者不发事件、不动「上次看到哪条提交」，但会通知一次。"
              >
                <ul className="score-rules">
                  {ledger.check_labels.map((one) => (
                    <li key={one.status} className="muted">
                      <strong>{one.label}</strong>
                      <small>（{one.status}）</small>
                      {!CHECKED.has(one.status) && " —— 这一次没读到上游"}
                    </li>
                  ))}
                </ul>
              </Field>
              <Field label="这几条线">
                <ul className="score-rules">
                  {Object.entries(ledger.thresholds).map(([key, value]) => (
                    <li key={key}>
                      {LINE[key] || key}：{value}
                    </li>
                  ))}
                </ul>
              </Field>
              <Field
                label="这一页做不到的"
                hint="写清楚是为了让「这里没有」不等于「这件事没发生」。"
              >
                <ul className="score-rules">
                  <li className="muted">
                    只看得到上游仓库的一行改动，看不到线上跑的到底是哪一版——所以
                    「上游动了」不等于「你的信息流变了」。
                  </li>
                  <li className="muted">
                    上游没公开模型权重和线上配置，所以改动只到「哪些文件动了」这一层，
                    到不了「排序分数会怎么变」。
                  </li>
                  <li className="muted">
                    两次检查之间如果跨越了多条提交，本机只读得到最新那一条，中间几条
                    看不到——事件里的「父提交」那几行会写明。
                  </li>
                </ul>
              </Field>
            </>
          )}
        </section>
      </div>
    </>
  );
}
