import { useCallback, useEffect, useState } from "react";
import { Eye, Trash2, UserPlus } from "lucide-react";
import { api, time } from "../api";
import type { Actions } from "../types";
import { Badge, Button, Empty, Field, Heading } from "../components/UI";

// 不知道就写不知道。一个没复核过的队列如果渲染成「精确率 0%」，它就和
// 「复核了 20 个、一个都没中」长得一模一样，而这是两件事。
const NO_DATA = "暂无数据";
// 三个结论词由工作室给出（`statuses`），这里只是兜底：拿不到就照原名显示，
// 不自己编一个中文说法。
const STATUS_LABEL: Record<string, string> = {
  watching: "待复核",
  confirmed: "确实有问题",
  dismissed: "误报",
};
const STATUS_TONE: Record<string, string> = {
  confirmed: "green",
};
// 分数在 06C-2 里就定成三个，不许合成一个总分，所以这里也是三行。
const SCORE_LABEL: Record<string, string> = {
  authenticity: "真实性",
  professionalism: "专业性",
  monetization: "盈利能力",
};
const CONFIDENCE: Record<string, string> = {
  ok: "样本够",
  low_confidence: "样本偏少",
  insufficient_data: "样本不足",
};
// 标签层的线。工作室给的是键名，键名要变成人能读的话只能面板来做；认不出的键
// 照原名显示——将来工作室加一条线，这里不该把它藏起来。
const LINE: Record<string, string> = {
  min_replies: "回复样本下限",
  shell_flag_rate: "空壳互动者占比",
  cliche_flag_rate: "模板回复占比",
  reply_like_floor: "回复/点赞比",
  pod_repeat_rate: "同一批人反复出现的占比",
  pod_followers_floor: "互动者的粉丝中位数",
  shill_link_share: "带链接帖占比",
  review_cap: "队列显示上限",
};
// 哪几个是 0–1 的比例（要写成百分号），哪几个是直接可读的数。
const RATE = new Set([
  "shell_flag_rate",
  "cliche_flag_rate",
  "pod_repeat_rate",
  "shill_link_share",
]);
type RowFlag = { code: string; layer: string; evidence: string };
type Row = {
  handle: string;
  added_at: number;
  hits: number;
  last_hit: number | null;
  // 命中过的规则名，跨多次运行累加。标签会被重算覆盖，这本账不会。
  hits_json: string[];
  status: string;
  note: string;
  action: string;
  updated: number;
  last_observed: number | null;
  run_confidence: string | null;
  authenticity: number | null;
  professionalism: number | null;
  monetization: number | null;
  // 只留**最近这一次运行**的标签：观察室要说的是「它现在为什么在榜上」，
  // 一个上次命中、这次已经消失的标不该继续挂着。
  flags: RowFlag[];
};
type Ledger = {
  watchlist: Row[];
  cap: number;
  total: number;
  truncated: boolean;
  review: {
    reviewed: number;
    confirmed: number;
    dismissed: number;
    pending: number;
    precision: number | null;
  };
  codes: { code: string; label: string }[];
  // 方案点名要、但这条通道做不了的规则。留着是为了让「查过了、没有」和
  // 「查不了」在复核的人眼里不是同一件事。
  blocked: { id: string; code: string; blocked: string }[];
  statuses: { status: string; label: string }[];
  rule_version: string;
  model_version: string;
  model_note: string;
  thresholds: Record<string, number>;
  sample_thresholds: {
    days: number;
    limit: number;
    min_post_sample: number;
    min_reply_sample: number;
    reads: Record<string, string>;
  };
};

const pct = (value: number) =>
  `${(value * 100).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}%`;
const score = (value: number | null) =>
  value === null || value === undefined ? NO_DATA : `${value} / 100`;

export default function Watchroom({ actions }: { actions: Actions }) {
  const [ledger, setLedger] = useState<Ledger | null>(null),
    [loading, setLoading] = useState(true),
    [selected, setSelected] = useState(""),
    [note, setNote] = useState(""),
    [action, setAction] = useState(""),
    [handle, setHandle] = useState("");

  const load = useCallback(async (keep?: string) => {
    const next = await api<Ledger>("/accounts/watchroom");
    setLedger(next);
    const pick =
      (keep && next.watchlist.some((row) => row.handle === keep) ? keep : "") ||
      next.watchlist[0]?.handle ||
      "";
    setSelected(pick);
    // 复核意见跟着选中的人走。换一个人不换草稿，「保存」就会把上一个人的
    // 说法写到这个人头上。
    const row = next.watchlist.find((one) => one.handle === pick);
    setNote(row?.note || "");
    setAction(row?.action || "");
  }, []);

  useEffect(() => {
    void actions.run(() => load()).finally(() => setLoading(false));
  }, [actions, load]);

  const select = (row: Row) => {
    setSelected(row.handle);
    setNote(row.note);
    setAction(row.action);
  };

  const verdict = (value: string) =>
    void actions.run(async () => {
      await api(`/accounts/watchroom/${selected}/review`, "POST", {
        verdict: value,
        note,
        action,
      });
      await load(selected);
    }, `已记为「${STATUS_LABEL[value] || value}」`);

  const drop = () =>
    void actions.run(async () => {
      await api(`/accounts/watchroom/${selected}`, "DELETE");
      await load();
    }, "已移出观察室，它的历史账也跟着没了");

  const add = () =>
    void actions.run(async () => {
      await api(`/accounts/watchroom/${handle.trim()}/watch`, "POST", {
        note: "",
      });
      setHandle("");
      await load();
    }, "已加入观察室");

  const rows = ledger?.watchlist || [];
  const board = ledger?.review;
  const row = rows.find((one) => one.handle === selected);
  const label = (code: string) =>
    ledger?.codes.find((one) => one.code === code)?.label || code;

  return (
    <>
      <Heading
        eyebrow="06C 账号观察室"
        title="被标了的人，人再看一遍"
        description={
          "硬标签命中过的账号会自动落到这里，也可以自己加。这是一本账、不是一个结论：" +
          "只有人给的结论（确实有问题 / 误报）才能量出这个标准不准——所以「还没人看过」" +
          "和「精确率是 0%」在这里是两句话。标签绕过分数的门槛，一个账号可能一个分数都" +
          "算不出来，标照样成立。"
        }
        action={
          board ? (
            <Badge tone={board.pending ? "" : "green"}>
              {board.pending ? `待复核 ${board.pending}` : "都复核过了"}
            </Badge>
          ) : undefined
        }
      />
      <div className="stack">
        <section className="surface">
          <div className="section-title">
            <h2>复核进度</h2>
            {ledger && (
              <span className="muted">
                规则版本 {ledger.rule_version || NO_DATA}
              </span>
            )}
          </div>
          {loading ? (
            <p className="muted">正在读取观察室…</p>
          ) : !ledger ? (
            <p className="muted">{NO_DATA}</p>
          ) : (
            <>
              <dl className="bench-metrics">
                <div>
                  <dt>队列里</dt>
                  <dd>
                    {ledger.total} 个
                    {ledger.truncated && <small className="muted"> 只显示前 {ledger.cap}</small>}
                  </dd>
                </div>
                <div>
                  <dt>待复核</dt>
                  <dd>{board?.pending ?? NO_DATA}</dd>
                </div>
                <div>
                  <dt>已复核</dt>
                  <dd>{board?.reviewed ?? NO_DATA}</dd>
                </div>
                <div>
                  <dt>确实有问题</dt>
                  <dd>{board?.confirmed ?? NO_DATA}</dd>
                </div>
                <div>
                  <dt>误报</dt>
                  <dd>{board?.dismissed ?? NO_DATA}</dd>
                </div>
                <div>
                  <dt>精确率</dt>
                  <dd>
                    {/* 一个都没复核过时它不是 0：0 会说这个标全是错的，
                        而事实是还没有人量过。 */}
                    {board?.precision === null || board === undefined ? (
                      <span className="muted">{NO_DATA}</span>
                    ) : (
                      pct(board.precision)
                    )}
                  </dd>
                </div>
              </dl>
              <p className="notice">
                精确率 = 确实有问题 ÷ 已复核。方案 §六 的评估方式就是这一列：每周抽{" "}
                {ledger.cap} 个账号人工看一遍，看精确率，不看分数分布好不好看。
                {ledger.model_note && ` ${ledger.model_note}。`}
              </p>
              {ledger.truncated && (
                <p className="notice warning">
                  队列一共有 {ledger.total} 个账号，这里按「待复核优先」只显示前{" "}
                  {ledger.cap} 条——不是只有这么多。复核掉一些，后面的会自己浮上来。
                </p>
              )}
            </>
          )}
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>队列</h2>
            <span className="muted">待复核的排在最前，复核过的排在后面</span>
          </div>
          {!rows.length ? (
            <Empty
              title="观察室里还没有人"
              description="账号读取时命中了硬标签就会自动进来；也可以在上面填一个用户名自己加。"
            />
          ) : (
            <div className="weekly-list">
              {rows.map((one) => (
                <button
                  key={one.handle}
                  className={`weekly-list-item ${one.handle === selected ? "active" : ""}`}
                  onClick={() => select(one)}
                >
                  <span>
                    <strong>@{one.handle}</strong>
                    <small>
                      {STATUS_LABEL[one.status] || one.status} · 命中 {one.hits} 次
                      {one.last_hit !== null && ` · 最近 ${time(one.last_hit)}`}
                    </small>
                  </span>
                  <span className="muted">
                    {one.flags.length ? (
                      <span className="flag-chips">
                        {one.flags.map((flag) => (
                          <span key={flag.code} className="flag-chip">
                            <strong>{label(flag.code)}</strong>
                          </span>
                        ))}
                      </span>
                    ) : (
                      // 「这次没命中」不等于「没问题」：它的账在下面。
                      <span>这次运行没有命中</span>
                    )}
                  </span>
                </button>
              ))}
            </div>
          )}
          <form
            className="form-stack spaced-small"
            onSubmit={(e) => {
              e.preventDefault();
              if (handle.trim()) add();
            }}
          >
            <Field
              label="自己加一个账号"
              hint="不带 @。命中会自动进来，这颗是给「还没读过、但看着就不对」的账号留的门。"
            >
              <input
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                maxLength={15}
                placeholder="例如：lifesinger"
              />
            </Field>
            <Button type="submit" disabled={!handle.trim()}>
              <UserPlus size={16} />
              加入观察室
            </Button>
          </form>
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>{selected ? `@${selected} 的复核` : "复核"}</h2>
            {row && (
              <span className="muted">
                加入于 {time(row.added_at)}
                {row.last_observed !== null && ` · 上次读取 ${time(row.last_observed)}`}
              </span>
            )}
          </div>
          {!row ? (
            <Empty
              title="还没选账号"
              description="上面选一个账号，这里会把它为什么在榜上、算出来的三个分、以及人给的结论列在一起。"
            />
          ) : (
            <>
              <Field
                label="现在为什么在榜上"
                hint="只显示最近这一次运行的标签；命中过的规则名会一直留在账上，标签本身会被重算覆盖。"
              >
                {row.flags.length ? (
                  <ul className="score-rules">
                    {row.flags.map((flag) => (
                      <li key={flag.code}>
                        <strong>{label(flag.code)}</strong>（{flag.layer}）：
                        {flag.evidence}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">
                    最近这次读取一条都没命中，{NO_DATA}。
                    {row.hits > 0 &&
                      `它当初是因为下面这些规则被放进来的，一共命中过 ${row.hits} 次。`}
                  </p>
                )}
              </Field>
              <Field label="命中的规则（工作室内部名字）">
                {row.hits_json.length ? (
                  <div className="actions">
                    {row.hits_json.map((ident) => (
                      <code key={ident} className="badge">
                        {ident}
                      </code>
                    ))}
                  </div>
                ) : (
                  <span className="muted">
                    还没有命中过任何规则（是手动加进来的）。
                  </span>
                )}
              </Field>
              <Field
                label="三个分"
                hint="标签不读这三个分。它们可能全是「暂无数据」而标签照样成立——这正是「硬标签」的意思。"
              >
                <dl className="bench-metrics">
                  {(
                    ["authenticity", "professionalism", "monetization"] as const
                  ).map((key) => (
                    <div key={key}>
                      <dt>{SCORE_LABEL[key]}</dt>
                      <dd>
                        {score(row[key])}
                        {row.run_confidence && (
                          <small className="muted">
                            {" "}
                            · {CONFIDENCE[row.run_confidence] || row.run_confidence}
                          </small>
                        )}
                      </dd>
                    </div>
                  ))}
                </dl>
                {row.last_observed === null && (
                  <p className="muted">
                    还没读过这个账号，所以没有分数可算。
                  </p>
                )}
              </Field>
              <form
                className="form-stack spaced-small"
                onSubmit={(e) => {
                  e.preventDefault();
                  verdict("confirmed");
                }}
              >
                <Field
                  label="复核意见"
                  hint="写「我怎么判断的」——下次看到同一个标，靠的是这几行字。"
                >
                  <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    maxLength={200}
                    rows={3}
                    placeholder="例如：简介是空的、回复全是同一个句式，主页只有转发。"
                  />
                </Field>
                <Field
                  label="要做什么"
                  hint="只写动作，不用写结果。工作室不会替你做这一步。"
                >
                  <input
                    value={action}
                    onChange={(e) => setAction(e.target.value)}
                    maxLength={200}
                    placeholder="例如：不再回复它；把这个标记进误报样本。"
                  />
                </Field>
                <div className="actions">
                  {/* 三个按钮都要写明 type：`Button` 没有默认 type，在 form 里
                      不写就是 submit，点「误报」会当成点「确实有问题」。 */}
                  <Button tone="primary" type="submit">
                    <Eye size={16} />
                    确实有问题
                  </Button>
                  <Button type="button" onClick={() => verdict("dismissed")}>
                    误报
                  </Button>
                  <Button type="button" onClick={() => verdict("watching")}>
                    退回待复核
                  </Button>
                  <Button type="button" onClick={drop}>
                    <Trash2 size={16} />
                    移出观察室
                  </Button>
                </div>
              </form>
            </>
          )}
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>这个房间在量什么</h2>
          </div>
          {!ledger ? (
            <p className="muted">正在读取…</p>
          ) : (
            <>
              <Field label="四个风险硬标签">
                <div className="actions">
                  {ledger.codes.map((one) => (
                    <span key={one.code} className="flag-chip">
                      <strong>{one.label}</strong>
                      <small>{one.code}</small>
                    </span>
                  ))}
                </div>
              </Field>
              <Field
                label="标签层用的线"
                hint="命中与没命中的逐条说明写在每个账号下面。哪条线是方案给的、哪条是本机定的，也在那里。"
              >
                <ul className="score-rules">
                  {Object.entries(ledger.thresholds).map(([key, value]) => (
                    <li key={key}>
                      {LINE[key] || key}：
                      {RATE.has(key) ? pct(value) : value}
                    </li>
                  ))}
                </ul>
              </Field>
              <Field
                label="采集层用的线"
                hint="另一层的事：上面那组决定「标什么」，这一组决定「读多少才算数」。两组不同名、也不同数。"
              >
                <ul className="score-rules">
                  <li>采样窗口：{ledger.sample_thresholds.days} 天</li>
                  <li>公开读取上限：{ledger.sample_thresholds.limit} 条</li>
                  <li>
                    样本门槛：原帖 {ledger.sample_thresholds.min_post_sample} 条、收到的回复{" "}
                    {ledger.sample_thresholds.min_reply_sample} 条
                  </li>
                </ul>
              </Field>
              {/* 少了这一块，「名单上没出现这个标」会被读成「这件事没发生」。 */}
              <Field
                label="这四个标里，这一路查不了的"
                hint="方案点名要、但这条通道真的读不到。留着是为了让「查过了、没有」不是「查不了」。"
              >
                <ul className="score-rules">
                  {ledger.blocked.map((one) => (
                    <li key={one.id} className="muted">
                      <strong>{one.code}</strong>（{one.id}）：{one.blocked}
                    </li>
                  ))}
                </ul>
              </Field>
            </>
          )}
        </section>
      </div>
    </>
  );
}
