import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ScanFace, RefreshCw, Telescope } from "lucide-react";
import { api, safeXUrl, time } from "../api";
import type { Studio, Actions } from "../types";
import { Badge, Button, Empty, External, Field, Heading } from "../components/UI";

// 不知道就写不知道。一个未知的特征如果渲染成 0，,它就和一个"算出来是 0"的特征
// 长得一模一样了，而这两件事在 06C-2 里的处理方式完全不同。
const NO_DATA = "暂无数据";
// 层名由工作室给出（采集方案的三层 + 自己的回复），面板只负责排序。
const LAYERS = [
  "A 账号静态特征",
  "B 内容与互动统计",
  "C 互动文本与互动者质量",
  "D 自己的回复",
];
const CONFIDENCE: Record<string, { text: string; green: boolean }> = {
  ok: { text: "样本够", green: true },
  low_confidence: { text: "样本偏少", green: false },
  insufficient_data: { text: "样本不足", green: false },
};
// 方案只允许三个分，不许合成一个总分，所以这里是三行而不是一行。
const SCORE_LABEL: Record<string, string> = {
  authenticity: "真实性",
  professionalism: "专业性",
  monetization: "盈利能力",
};
// 扩展名返回的状态词是给机器看的，这里换成给操作者看的话。
const STATE: Record<string, string> = {
  ready: "读到了",
  waiting_browser: "浏览器没有响应",
  waiting_login: "浏览器没有登录 X",
  account_mismatch: "读到的不是这个号",
  query_mismatch: "页面回答的不是这次查询",
  invalid_request: "请求不合法",
  source_error: "页面结构读不出来",
};
const STATUS: Record<string, string> = {
  queued: "排队中",
  sent: "读取中",
  done: "已结束",
};
type Feature = {
  name: string;
  label: string;
  layer: string;
  unit: string;
  value: number | null;
  known: boolean;
  note: string;
  sample_size: number | null;
};
type ScoreRule = {
  id: string;
  kind: "cap" | "delta";
  amount: number;
  fired: boolean;
  because: string;
};
type SkippedRule = { id: string; blocked: string };
type FlagCheck = { id: string; code: string; fired: boolean; because: string };
// 方案 §三 第 4 步的四个账号级风险硬标签。标签名和它的中文说法都由工作室给出，
// 面板不自己编一套——同一个 code 在两处出现两个说法，读的人会以为是两件事。
type Flag = {
  code: string;
  label: string;
  layer: string;
  rules: string[];
  evidence: string;
  confidence: string;
};
type Flags = {
  rule_version: string;
  model_version: string;
  model_note: string;
  confidence: string;
  flags: Flag[];
  checks: FlagCheck[];
  skipped: SkippedRule[];
  blocked: SkippedRule[];
};
type ScoreEntry = {
  base: number;
  value: number | null;
  label: string;
  reason: string;
  rules: ScoreRule[];
  skipped: SkippedRule[];
};
type Score = {
  feature_id: string;
  rule_version: string;
  model_version: string;
  labels_json: {
    rule_version?: string;
    model_note?: string;
    confidence?: string;
    scores?: Record<string, ScoreEntry>;
    thresholds?: Record<string, number>;
  };
};
type Run = {
  id: string;
  handle: string;
  observed_at: number;
  window_days: number;
  from_date: string;
  to_date: string;
  coverage_gap: string;
  confidence: string;
  sample_counts_json: {
    counts?: Record<string, number>;
    failures?: Record<string, string>;
    review?: {
      high_engagement?: string[];
      ordinary?: string[];
      links?: string[];
    };
  };
  values: Feature[];
  score?: Score | null;
  flags?: Flags | null;
};
type Row = {
  handle: string;
  observed_at: number | null;
  confidence: string | null;
  coverage_gap: string;
  features: number;
  known: number;
  samples: number;
  captured_at: number | null;
};
type Read = {
  status: string;
  state: string;
  complete: boolean;
  coverage_gap: string;
};
type Detail = {
  handle: string;
  window: { days: number; from: string; to: string };
  plan: { handle: string; since: string; until: string; reads: Record<string, string> };
  samples: Record<string, number>;
  reads: Record<string, Read>;
  run: Run | null;
  score: Score | null;
  // 和分数一样提到顶层：`run` 为 null（没读过）和 `flags` 为 null（这次运行早于
  // 硬标签上线）是两种不同的答案，面板要说得出是哪一种。
  flags: Flags | null;
  thresholds: {
    days: number;
    limit: number;
    min_post_sample: number;
    min_reply_sample: number;
    reads: Record<string, string>;
  };
};

const show = (value: number | null, unit: string) =>
  value === null || value === undefined
    ? NO_DATA
    : `${value.toLocaleString("zh-CN", { maximumFractionDigits: 3 })}${unit}`;

export default function AccountsObserve({
  data,
  actions,
  tabs,
  active,
  onOpenReview,
}: {
  data: Studio;
  actions: Actions;
  // 标签行由外壳拼好递进来，插在标题和内容之间——标题右边的徽标是这一半自己的
  // 状态，把它抬到外壳就得连状态一起搬，不划算。
  tabs?: ReactNode;
  // 自己是不是当前那个标签。不是就不读——这里只是挂着留住状态。
  active: boolean;
  onOpenReview?: (handle: string) => void;
}) {
  const [list, setList] = useState<Row[]>([]),
    [detail, setDetail] = useState<Detail | null>(null),
    [selected, setSelected] = useState(""),
    [handle, setHandle] = useState(""),
    [loading, setLoading] = useState(true);
  const busy = data.overview.tasks.filter(
    (t) => ["queued", "running"].includes(t.status),
  );
  const running = busy.some((t) => t.kind === "account_quality");

  const open = useCallback(async (who: string) => {
    setDetail(await api<Detail>(`/accounts/quality/${who}`));
    setSelected(who);
  }, []);

  const load = useCallback(
    async (keep?: string) => {
      const listing = await api<{ accounts: Row[] }>("/accounts/quality");
      setList(listing.accounts);
      // A refresh keeps the operator on whoever they were reading; only when
      // that account is gone (or none was chosen) does it move to the first.
      const pick =
        (keep && listing.accounts.some((a) => a.handle === keep) ? keep : "") ||
        listing.accounts[0]?.handle ||
        "";
      if (!pick) {
        setSelected("");
        setDetail(null);
        return;
      }
      await open(pick);
    },
    [open],
  );

  // 背景刷新（桌面端每 4 秒一次）会重跑这个 effect。用 ref 拿"人现在看的是哪个
  // 号"，别写进依赖里：写进去的话每次点一个人就会重读一次整张列表，而且重读会
  // 把人从他刚选的那个号上挪走。
  const shown = useRef(selected);
  shown.current = selected;

  useEffect(() => {
    if (!active) return;
    void actions.run(() => load(shown.current)).finally(() => setLoading(false));
  }, [actions, load, active]);

  // 四个读取在浏览器里要跑几分钟，面板自己盯着，读完了结果会自己出现。
  useEffect(() => {
    if (!running || !active) return;
    const timer = setInterval(() => {
      void load(shown.current).catch(() => {});
    }, 4000);
    return () => clearInterval(timer);
  }, [running, active, load]);

  const start = (who: string) =>
    void actions.run(async () => {
      await api(`/accounts/quality/read/${who}`, "POST");
      await load(who);
    }, "已让浏览器开始读取这个账号");

  const run = detail?.run;
  const failed = run?.sample_counts_json?.failures || {};
  const review = run?.sample_counts_json?.review || {};
  // 三个分本身是 account_scores 的三个列，但可解释的部分在 labels_json 里：
  // 每条规则用的数、跑没跑、没跑是因为什么。面板显示的是这一份。
  const ledger = {
    version: run?.score?.labels_json?.rule_version || "",
    model_note: run?.score?.labels_json?.model_note || "",
    scores: Object.entries(run?.score?.labels_json?.scores || {}) as [
      string,
      ScoreEntry,
    ][],
  };
  // 未知的特征也要显示，而且要把为什么未知写在旁边——这一层的价值就在这里。
  const grouped = new Map<string, Feature[]>();
  for (const feature of run?.values || []) {
    grouped.set(feature.layer, [...(grouped.get(feature.layer) || []), feature]);
  }
  const layers = [
    ...LAYERS.filter((layer) => grouped.has(layer)),
    ...[...grouped.keys()].filter((layer) => !LAYERS.includes(layer)),
  ];

  return (
    <>
      <Heading
        eyebrow="06C 账号观察"
        title="这个号像不像真人"
        description={"先把“这个账号的行为特征读不读得到”回答清楚，再按固定规则给出真实性 / 专业性 /"
          + "盈利能力三个 0–100 分，最后按规则打风险硬标签。分数是「先一票否决、再加减分」"
          + "算出来的连续分，硬标签说的是「这件事本身被看见了」，两件事互不代替；"
          + "命中过的账号会进「复核室」，等人工复核。"}
        action={
          run?.confidence ? (
            <Badge tone={CONFIDENCE[run.confidence]?.green ? "green" : ""}>
              {CONFIDENCE[run.confidence]?.text || run.confidence}
            </Badge>
          ) : undefined
        }
      />
      {/* 只有当前这一半长标签行：两半都长的话，藏起来的那一行虽然看不见、也进
          不了无障碍树，但 DOM 里就是两份同样的 tablist。 */}
      {active && tabs}
      <div className="stack">
        <section className="surface">
          <div className="section-title">
            <h2>看过的账号</h2>
            {running && <Badge>浏览器正在读取</Badge>}
          </div>
          {loading ? (
            <p className="muted">正在读取名单…</p>
          ) : list.length ? (
            <div className="weekly-list">
              {list.map((row) => (
                <button
                  key={row.handle}
                  className={`weekly-list-item ${row.handle === selected ? "active" : ""}`}
                  onClick={() => void actions.run(() => open(row.handle))}
                >
                  <span>
                    <strong>@{row.handle}</strong>
                    {row.observed_at !== null && (
                      <small>读于 {time(row.observed_at)}</small>
                    )}
                  </span>
                  <span className="muted">
                    {/* 「一个数都算不出来」和「算出来了但样本偏少」不是同一件事。 */}
                    {row.observed_at === null
                      ? `只采到样本，尚未算特征 · ${row.samples} 条`
                      : `特征 ${row.known}/${row.features} 可算 · 样本 ${row.samples} 条`}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="muted">
              <ScanFace size={14} /> 还没有观察过任何账号，在下面填一个用户名读一次。
            </p>
          )}
          <form
            className="form-stack spaced-small"
            onSubmit={(e) => {
              e.preventDefault();
              const who = handle.trim();
              if (who) start(who);
            }}
          >
            <Field label="X 用户名" hint="不带 @。被观察的通常是别人，也可以是自己的号。">
              <input
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                maxLength={15}
                placeholder="例如：lifesinger"
              />
            </Field>
            <Button type="submit" disabled={!handle.trim()}>
              <ScanFace size={16} />
              读这个账号
            </Button>
          </form>
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>{selected ? `@${selected} 的特征` : "特征"}</h2>
            {run && (
              <span className="muted">
                读于 {time(run.observed_at)} · 窗口 {run.from_date} ~ {run.to_date}
              </span>
            )}
          </div>
          {!detail ? (
            <Empty
              title="还没选账号"
              description="上面选一个或填一个用户名，读回来之后这里会列出四个读取各自读到了什么、哪些量算得出来。"
            />
          ) : (
            <>
              <div className="actions spaced-small">
                <Button
                  tone="primary"
                  busy={running}
                  onClick={() => start(selected)}
                >
                  <RefreshCw size={16} />
                  重新读取
                </Button>
                {/* 命中的账号会自动进复核室；这一颗是给「还没读过、但看着就不对」的
                    账号留的门，也是那些只采到样本、算不出特征时的唯一入口。加完直接
                    切到复核室选好它——「去另一个菜单里找」是合并之前的事了。 */}
                <Button
                  onClick={() =>
                    void actions.run(async () => {
                      await api(`/accounts/watchroom/${selected}/watch`, "POST", {
                        note: "",
                      });
                      // 先等这次写入落地再跳：复核室一挂上就读队列，读在写前面
                      // 就看不到刚加进去的这个号，会落到别人身上、还弹一句
                      // 「不在前 50 个里」。但也不等 run 返回——run 后面还有一整
                      // 轮刷新（四个接口），那样会整整晚一个刷新周期才切过去。
                      onOpenReview?.(selected);
                    }, "已加入复核室")
                  }
                >
                  <Telescope size={16} />
                  加入复核室
                </Button>
                <span className="muted">
                  会开一个 X 页面读取主页与三个搜索，只读、不发布、不互动。
                </span>
              </div>
              {running && (
                <p className="notice">
                  正在读：浏览器取回结果要几分钟，读完这里会自动出现，不用刷新页面。
                </p>
              )}

              {/* 四个读取分开列，因为"读到了但是读取本身缺了一块"和"这个量算不出来"
                  是两件事，混在一起就没人能判断下一步该补什么。 */}
              <dl className="bench-metrics">
                {Object.entries(detail.thresholds.reads).map(([kind, label]) => {
                  const read = detail.reads[kind];
                  return (
                    <div key={kind}>
                      <dt>{label}</dt>
                      <dd>
                        {detail.samples[kind] || 0} 条
                        {read && (
                          <small className="muted">
                            {" "}
                            · {STATUS[read.status] || read.status}
                            {read.state && ` · ${STATE[read.state] || read.state}`}
                          </small>
                        )}
                      </dd>
                    </div>
                  );
                })}
              </dl>
              {Object.entries(failed).length > 0 && (
                <p className="notice warning">
                  这几个读取没能回答：
                  {Object.entries(failed)
                    .map(([kind, why]) => `${detail.thresholds.reads[kind] || kind}（${why}）`)
                    .join("；")}
                </p>
              )}
              {run?.coverage_gap && (
                <p className="notice warning">这次没读全：{run.coverage_gap}</p>
              )}

              {!run ? (
                <p className="muted">
                  还没有读过这个账号，{NO_DATA}。上面的「重新读取」会跑一次。
                </p>
              ) : (
                <>
                  <p className="notice">
                    下面每题都是读过的东西直接数出来的量，分数就是站在这些量上算的：先一票否决
                    （封顶）、再加减分，<strong>没有权重</strong>。哪条规则跑过、哪条没评，都跟在每个分后面。
                    样本门槛是原帖 {detail.thresholds.min_post_sample} 条、收到的回复{" "}
                    {detail.thresholds.min_reply_sample} 条，不到就标「样本偏少」；
                    公开读取上限 {detail.thresholds.limit} 条。
                  </p>

                  {/* 分数在特征之前：它是这一片的结论，而下面每一层都是它的证据。
                      一个分显示「暂无数据」时，缺的是证据，不是这个账号得了 0 分。 */}
                  <Field
                    label="三个分"
                    hint={
                      run.score
                        ? `规则版本 ${ledger.version || run.score.rule_version}；`
                          + `${ledger.model_note || "模型层未建"}。`
                          + "分数在工作室里算，面板只显示它算出来的账。"
                        : "这一片的分数由工作室按固定规则算，面板只显示结果。"
                    }
                  >
                    {ledger.scores.length ? (
                      <div className="score-ledger">
                        {ledger.scores.map(([key, one]) => (
                          <div key={key} className="score-block">
                            <h3 className="section-subtitle">
                              {SCORE_LABEL[key] || key}
                              {" · "}
                              {one.value === null ? (
                                <span className="muted">{NO_DATA}</span>
                              ) : (
                                <>
                                  {one.value} / 100
                                  {one.label && (
                                    <span className="muted"> · {one.label}</span>
                                  )}
                                </>
                              )}
                            </h3>
                            {one.value === null && (
                              <p className="muted">{one.reason}</p>
                            )}
                            <ul className="score-rules">
                              {one.rules
                                .filter((rule) => rule.fired)
                                .map((rule) => (
                                  <li key={rule.id}>
                                    <strong>
                                      {rule.kind === "cap"
                                        ? `封顶 ${rule.amount}`
                                        : `${rule.amount > 0 ? "加" : "减"} ${Math.abs(rule.amount)}`}
                                    </strong>
                                    {"："}
                                    {rule.because}
                                  </li>
                                ))}
                              {one.rules
                                .filter((rule) => !rule.fired)
                                .map((rule) => (
                                  <li key={rule.id} className="muted">
                                    看过、没触发：{rule.because}
                                  </li>
                                ))}
                              {one.skipped.map((rule) => (
                                <li key={rule.id} className="muted">
                                  这一次没评：{rule.blocked}
                                </li>
                              ))}
                            </ul>
                          </div>
                        ))}
                      </div>
                    ) : (
                      // 06C-1 时代算过的运行没有分数行。显示「暂无数据」而不是把
                      // 整块藏起来，否则它会看起来像这一片从来没有分数这个概念。
                      <p className="muted">
                        这次运行没有分数（早于三维分数上线），{NO_DATA}；重新读取一次就会补上。
                      </p>
                    )}
                  </Field>

                  {/* 硬标签不跟在某个分后面：它绕过分数门槛，自己就是一层结论。
                      这一块要说清三件事——命中了几条、看过哪些没触发、哪些这一
                      路读取根本查不了。少了第三件，「没标出来」会被读成「没问题」。 */}
                  <Field
                    label="风险硬标签"
                    hint={
                      detail.flags
                        ? `规则版本 ${detail.flags.rule_version}；`
                          + `${detail.flags.model_note || "模型层未建"}。`
                          + "标签绕过分数的门槛：只要证据在场、样本够，标就成立。"
                        : "这一片的标签由工作室按固定规则打，面板只显示结果。"
                    }
                  >
                    {!detail.flags ? (
                      // 和分数一样，06C-2 时代算过的运行没有标签。
                      <p className="muted">
                        这次运行没有标签（早于硬标签上线），{NO_DATA}；重新读取一次就会补上。
                      </p>
                    ) : (
                      <>
                        {detail.flags.flags.length ? (
                          <div className="flag-chips">
                            {detail.flags.flags.map((one) => (
                              <span key={one.code} className="flag-chip">
                                <strong>{one.label}</strong>
                                <small>{one.code}</small>
                              </span>
                            ))}
                          </div>
                        ) : (
                          <p className="muted">
                            这次一条硬标签都没命中。下面列的是看过哪些、哪些没看。
                          </p>
                        )}
                        <ul className="score-rules">
                          {detail.flags.flags.map((one) => (
                            <li key={one.code}>
                              <strong>{one.label}</strong>（{one.layer}）：{one.evidence}
                            </li>
                          ))}
                          {/* 三句不同的话：看过、没触发 / 这一次没评 / 这条通道查不了。 */}
                          {detail.flags.checks
                            .filter((one) => !one.fired)
                            .map((one) => (
                              <li key={one.id} className="muted">
                                看过、没触发：{one.because}
                              </li>
                            ))}
                          {detail.flags.skipped.map((one) => (
                            <li key={one.id} className="muted">
                              这一次没评：{one.blocked}
                            </li>
                          ))}
                          {detail.flags.blocked.map((one) => (
                            <li key={one.id} className="muted">
                              这条通道查不了：{one.blocked}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </Field>
                  {layers.map((layer) => (
                    <div key={layer}>
                      <h3 className="section-subtitle">{layer}</h3>
                      {/* 每行一个特征：左名称、中数值、右「为什么没有值」。
                          没有值的特征也照样列出来，那个理由就是这一层的答案。 */}
                      <div className="quality-features">
                        {grouped.get(layer)!.map((feature) => (
                          <div key={feature.name} className="quality-row">
                            <span className="quality-label" title={feature.name}>
                              {feature.label}
                            </span>
                            <span className="quality-value">
                              {feature.known ? (
                                show(feature.value, feature.unit)
                              ) : (
                                <span className="muted">{NO_DATA}</span>
                              )}
                              {feature.sample_size !== null && (
                                <small className="muted">
                                  {" "}
                                  · 样本 {feature.sample_size}
                                </small>
                              )}
                            </span>
                            <span className="quality-note">
                              {feature.known ? "" : feature.note}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}

                  {/* 人工复核的那三批。前两批是抽样、抽出来是固定的（换个人看也是
                      同一批）；第三批是带链接/卖货帖**全部**，方案 §三 第 2 步 说
                      「否则会被一条爆款带偏」，所以它不抽样也不截断。 */}
                  {review.high_engagement?.length ||
                  review.ordinary?.length ||
                  review.links?.length ? (
                    <Field
                      label="人工复核样本"
                      hint="高互动优先 + 其余均分，抽出来的是固定的，换个人看也是同一批；带链接/卖货帖全部列出，一条不落。"
                    >
                      <div className="actions">
                        {(
                          [
                            ["high_engagement", "高互动"],
                            ["ordinary", "普通"],
                            ["links", "带链接"],
                          ] as const
                        ).map(([bucket, label]) => (
                          <span key={bucket} className="muted">
                            {label}：
                            {(review[bucket] || []).map((post_id) => {
                              const url = safeXUrl(
                                `https://x.com/${selected}/status/${post_id}`,
                              );
                              return url ? (
                                <External key={post_id} url={url}>
                                  {post_id.slice(-6)}
                                </External>
                              ) : (
                                <span key={post_id}>{post_id.slice(-6)}</span>
                              );
                            })}
                          </span>
                        ))}
                      </div>
                    </Field>
                  ) : null}

                  <Field
                    label="这次用的查询"
                    hint={`由工作室按 ${detail.thresholds.days} 天窗口生成，不经过手输。`}
                  >
                    {Object.keys(detail.plan.reads).length ? (
                      <code className="bench-query">
                        {Object.entries(detail.plan.reads)
                          .map(([, query]) => query)
                          .join("\n")}
                      </code>
                    ) : (
                      <span className="muted">{NO_DATA}</span>
                    )}
                  </Field>
                </>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
