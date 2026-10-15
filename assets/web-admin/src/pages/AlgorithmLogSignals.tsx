import type { ReactNode } from "react";
import { Badge, Field, Heading } from "../components/UI";
import raw from "../data/algorithmSignals.json";

// 这一页**一个请求都不发**。清单是构建期就固化进仓库的（`src/data/algorithmSignals.json`），
// 所以切到「当前逻辑」不会产生任何 /api/v1/… —— 这是这个标签立得住的理由，也是它
// 和「改动记录」那一半最大的不同。它唯一需要的外部状态是「工作室读到过哪条上游提交」，
// 那个由「改动记录」那一半递上来（见下面的 UpstreamRead），它自己去要就破功了。
const NO_DATA = "暂无数据";

// 「改动记录」那一半读到上游状态后递上来的三个字段。只递这几个，不递整份 ledger
// ——这一页只需要能回答「清单钉的那条提交，本机读到过吗」。
export type UpstreamRead = {
  last_sha: string;
  last_checked: number | null;
  shas: string[];
  checked: number;
};

type Source = {
  file: string;
  line: number;
  value_line: number;
  kind: string;
  span: string;
};
type Entry = {
  id: string;
  official: string;
  symbol: string;
  name: string;
  note: string;
  condition: string;
  value: number | boolean | null;
  value_raw: string;
  value_type: string;
  unit: string;
  target: string[];
  direction: string;
  group: string;
  order: number;
  source: Source;
};
type Group = {
  id: string;
  title: string;
  note: string;
  ranked?: boolean;
  rank_by?: string;
};
type Inventory = {
  upstream: {
    repo: string;
    branch: string;
    sha: string;
    dirty: boolean;
    read_at: string;
    license: string;
    paths: string[];
  };
  notes: { weights: string; engagement: string };
  groups: Group[];
  entries: Entry[];
  conflicts: { symbol: string; sources: { file: string; line: number; value: number | null }[] }[];
  not_listed: { kind: string; title: string; detail: string; evidence: string[] }[];
  visibility: {
    verdict: { file: string; line: number };
    audience_enum: { file: string; line: number; non_followers_condition_line: number };
    levels: { id: string; file: string; line: number; rules: number }[];
    audiences: Record<string, number>;
    non_follower_rules: { rule: string; file: string; line: number }[];
    recommendations_only: string[];
    home_only: string[];
  };
  unclassified: string[];
};

const S = raw as unknown as Inventory;

const UNIT: Record<string, string> = {
  weight: "权重",
  boost: "加成",
  multiplier: "乘子",
  gate: "开关",
  constant: "常数",
  count: "个数",
  seconds: "秒",
  milliseconds: "毫秒",
  minutes: "分钟",
  hours: "小时",
  days: "天",
  position: "名次",
  threshold: "门槛值",
};
const DIRECTION: Record<string, string> = {
  positive: "正向",
  negative: "负向",
  neutral: "中性",
};
const TARGET: Record<string, string> = { flow: "流量", follower: "涨粉" };
const KIND: Record<string, string> = {
  withheld: "上游主动剔除",
  not_public: "上游没有公开",
  unread: "我们没读到",
  out_of_scope: "不在这一页的范围",
};
// 档位名是从 verdict.rs / levels.rs 里读出来的，不是术语表：`Verdict` 只有
// 「保留」和「不给看」两大类，不给看那一边再分三档。
const VERDICT = [
  ["ALLOW", "正常参与排序"],
  ["NOTICE", "能看，但带提示（例如需要点开才显示）"],
  ["INTERSTITIAL", "要跨过一道中间页才看得到"],
  ["DROP", "直接不给看——命中就结束，后面的规则不再评估"],
] as const;

const short = (sha: string) => sha.slice(0, 7);

// `.notice` 在 styles.css:757 上是 `display:flex; align-items:center; gap:10px`——那是给
// 「一个图标 + 一句话」那种提示准备的。里面的子节点一多（一句带加粗或带代码的话就会），
// flex 会把每个子节点摆成一行横排，整段话被拆成几栏，读不成句。这一页的提示全是整段的
// 话，所以统一塞进一个 span：flex 只看到一个子节点，就回到了正常段落。
// 不动那条全局样式——别的页面上有靠它横排的提示，改它得先数一遍影响面（已单独记）。
function Note({ warn, children }: { warn?: boolean; children: ReactNode }) {
  return (
    <p className={warn ? "notice warning" : "notice"}>
      <span>{children}</span>
    </p>
  );
}

// 清单里的解释是人手写的 markdown：为了在 JSON 里读得清，用了**加粗**和 `代码`。
// 原样铺到页面上会把记号一起显示出来（页面上出现「**预测概率**」），那是漏原文。
// 这里只认这两种记号，别的原样——这一页的解释全是我们自己写的，需要的就是这两种。
const MARK = /(\*\*[^*]+\*\*|`[^`]+`)/g;

function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split(MARK).map((part, index) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={index}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          return <code key={index}>{part.slice(1, -1)}</code>;
        }
        return part;
      })}
    </>
  );
}

function value(entry: Entry) {
  // 解析不出来就写「暂无数据」，**绝不写 0**。0 是一个事实声明，我们不知道
  // 那个事实；把「我们没解析出来」写成 0 是编数字。
  if (entry.value === null) {
    return (
      <>
        <strong className="muted">{NO_DATA}</strong>
        <small className="muted">（上游原文 {entry.value_raw}，我们没解析出数值）</small>
      </>
    );
  }
  if (typeof entry.value === "boolean") {
    return <strong>{entry.value ? "开" : "关"}</strong>;
  }
  return <strong>{entry.value}</strong>;
}

function source(entry: Entry) {
  return (
    <code>
      {entry.source.file}:{entry.source.line}
    </code>
  );
}

// 清单钉的那条提交，和本机读到的那条比。**只在能证实时才说「落后」**：面板手上
// 没有 git，做不了祖先推断，所以分成「证实落后」「证实一致」「说不清」三种，
// 宁可说说不清，也不猜方向。
function comparison(read: UpstreamRead | null) {
  if (!read) return "unread";
  if (!read.last_sha) return "idle";
  if (read.last_sha === S.upstream.sha) return "same";
  if (read.shas.includes(S.upstream.sha)) return "stale";
  return "unknown";
}

function Drift({ read }: { read: UpstreamRead | null }) {
  const pinned = short(S.upstream.sha);
  switch (comparison(read)) {
    case "unread":
      return (
        <Note>
          这一页不读上游（这是它的卖点：切过来不发任何请求），所以还没比过。切到
          「改动记录」读一次，回来这里就会拿工作室上次读到的提交，和清单钉的这条比一下。
        </Note>
      );
    case "idle":
      return (
        <Note>
          工作室检查过 {read?.checked} 次，但还没有一次读成功，所以「本机现在看到的是
          哪条提交」答不上来——这次比不了。清单钉的是 <code>{pinned}</code>。
        </Note>
      );
    case "same":
      return (
        <Note>
          清单对的就是本机读到的上游：<code>{pinned}</code>。这份清单没有落后。
        </Note>
      );
    case "stale":
      return (
        <Note warn>
          <strong>清单落后于上游。</strong>工作室读到过清单钉的那条{" "}
          <code>{pinned}</code>，而最近读到的是 <code>{short(read?.last_sha || "")}</code>
          ——也就是说在那之后上游又动过。下面所有数值都是 <code>{pinned}</code> 那天的，
          不是现在线上的。
        </Note>
      );
    default:
      return (
        <Note warn>
          方向说不清：工作室最近读到的是 <code>{short(read?.last_sha || "")}</code>，
          而清单钉的 <code>{pinned}</code> 不在工作室最近读到的那几条里。它可能比本机
          读到的都新，也可能在更早的时候读到过、已经掉出了只留最近几十条的记录窗口——
          这两种本机分不开，面板手上也没有 git，做不了祖先推断，所以这里不写「落后」，
          只把两条都摆出来。清单的数值属于 <code>{pinned}</code> 那天。
        </Note>
      );
  }
}

export default function AlgorithmLogSignals({
  tabs,
  active,
  read,
}: {
  tabs?: ReactNode;
  active: boolean;
  read: UpstreamRead | null;
}) {
  const upstream = S.upstream;
  const by = S.groups.map((group) => ({
    group,
    rows: S.entries.filter((entry) => entry.group === group.id),
  }));

  return (
    <>
      <Heading
        eyebrow="06D 算法更新跟踪"
        title="源码明细与适用范围"
        description={
          "这是供核对的源码清单：某一版 X 官方开源代码里，与流量和涨粉" +
          "有关的逻辑按重要等级列在这里，每条都指得出文件和行号。数值是源码里的数值" +
          "（上游源码里的默认值），我们没有自己算一个综合分。上游没有版本号、也没有 tag，" +
          "所以「当前版本」只能钉一条 commit —— 下面写清楚钉的是哪条。" +
          "这个标签不发任何请求，数字全部来自构建期固化进仓库的清单。"
        }
        action={<Badge tone="green">钉在 {short(upstream.sha)}</Badge>}
      />
      {active && tabs}
      <div className="stack">
        <section className="surface">
          <div className="section-title">
            <h2>这份清单对的是哪条提交</h2>
            <span className="muted">
              {S.entries.length} 条 · 抽自 {upstream.paths.length} 个文件
            </span>
          </div>
          <dl className="bench-metrics">
            <div>
              <dt>上游仓库</dt>
              <dd>{upstream.repo}</dd>
            </div>
            <div>
              <dt>分支</dt>
              <dd>{upstream.branch}</dd>
            </div>
            <div>
              <dt>钉在哪条提交</dt>
              <dd>
                <code>{upstream.sha}</code>
              </dd>
            </div>
            <div>
              <dt>读于</dt>
              <dd>{upstream.read_at}</dd>
            </div>
            <div>
              <dt>许可</dt>
              <dd>{upstream.license}</dd>
            </div>
            <div>
              <dt>这些数值是谁给的</dt>
              <dd>官方给的数值（上游源码里的默认值）</dd>
            </div>
          </dl>
          <Drift read={read} />
          <Field
            label="抽的是这几个文件"
            hint="清单只认这几个文件里的参数；它们之外的东西（召回、缓存、Kafka、模型结构）不在这一页的范围。"
          >
            <ul className="score-rules">
              {upstream.paths.map((path) => (
                <li key={path} className="muted">
                  <code>{path}</code>
                </li>
              ))}
            </ul>
          </Field>
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>读这张表之前，先读这两句</h2>
          </div>
          {/* 这两句是上游自己写在代码里的。不把它们摆在最前面，下面的数字就会
              被当成「1 个举报抵 355 个赞」那样读懂——那正是上游明说不对的读法。 */}
          <Field
            label="权重乘的是什么"
            hint="上游在 home-mixer/params/param.rs 和 xai-value-model/scoring.rs 里亲手写了这条警告。"
          >
            <p>
              <Rich text={S.notes.weights} />
            </p>
          </Field>
          <Field label="什么样的互动才算数">
            <p>
              <Rich text={S.notes.engagement} />
            </p>
          </Field>
        </section>

        {by.map(({ group, rows }) => (
          <section className="surface" key={group.id}>
            <div className="section-title">
              <h2>{group.title}</h2>
              <span className="muted">
                {rows.length} 条
                {group.ranked && group.rank_by === "desc" && " · 按官方数值降序"}
                {group.rank_by === "abs" && " · 按绝对值降序"}
              </span>
            </div>
            <Note>
              <Rich text={group.note} />
            </Note>
            <ul className="score-rules">
              {rows.map((entry) => (
                <li key={entry.id}>
                  <strong>
                    {value(entry)} <small className="muted">{UNIT[entry.unit] || entry.unit}</small>
                  </strong>{" "}
                  · {entry.name}
                  <br />
                  <small className="muted">
                    {DIRECTION[entry.direction] || entry.direction}
                    {entry.target.length > 0 &&
                      ` · ${entry.target.map((one) => TARGET[one] || one).join("、")}`}
                    {" · 官方标识符 "}
                    <code>{entry.official}</code>
                    {" · 出处 "}
                    {source(entry)}
                  </small>
                  {entry.condition && (
                    <>
                      <br />
                      <small className="muted">
                        什么条件下才算：<Rich text={entry.condition} />
                      </small>
                    </>
                  )}
                  <p className="muted">
                    <Rich text={entry.note} />
                  </p>
                </li>
              ))}
            </ul>
          </section>
        ))}

        <section className="surface">
          <div className="section-title">
            <h2>可见性限制：能不能被看到</h2>
            <span className="muted">不是权重，但同样决定有没有人看得见</span>
          </div>
          <Note>
            这一节的东西不参与打分。它们先把内容<strong>拿掉</strong>——所以在讨论权重之前，
            得先有资格进入排序。
          </Note>
          <Field
            label="四种判定"
            hint={`枚举出自 ${S.visibility.verdict.file}:${S.visibility.verdict.line}。`}
          >
            <ul className="score-rules">
              {VERDICT.map(([code, explain]) => (
                <li key={code}>
                  <code>{code}</code>：{explain}
                </li>
              ))}
            </ul>
          </Field>
          <Field
            label="五个档位各有多少条规则"
            hint="同一套规则在不同场景下挂的条数不一样；数字大的那个档位拿掉得更多。"
          >
            <ul className="score-rules">
              {S.visibility.levels.map((level) => (
                <li key={level.id}>
                  <strong>{level.id}</strong>：{level.rules} 条
                  <small className="muted">
                    {"（"}
                    <code>
                      {level.file}:{level.line}
                    </code>
                    {"）"}
                  </small>
                </li>
              ))}
            </ul>
            {/* 这是算出来的事实，不是印象：推荐位那份清单把关注流那份整份包住了。
                所以不存在「只在关注流被拿掉、推荐位反而留着」的规则。 */}
            <Note>
              推荐位（
              {S.visibility.levels.find((one) => one.id === "TIMELINE_HOME_RECOMMENDATIONS")?.rules ?? NO_DATA}
              ）比关注流（
              {S.visibility.levels.find((one) => one.id === "TIMELINE_HOME")?.rules ?? NO_DATA}
              ）多拿掉 {S.visibility.recommendations_only.length} 条；反过来多出来的那份是{" "}
              {S.visibility.home_only.length} 条——也就是说，<strong>没有哪条规则只在关注流里生效</strong>。
              推荐位是关注流的严格更严版本，不是另一套。下面是多出来的那些（出处都在{" "}
              <code>{S.visibility.levels[0]?.file}</code> 里，逐条对得上）。
            </Note>
            <ul className="score-rules">
              {S.visibility.recommendations_only.map((rule) => (
                <li key={rule} className="muted">
                  <code>{rule}</code>
                </li>
              ))}
            </ul>
          </Field>
          <Field
            label={`只对「没关注这个人」的看客生效的规则（${S.visibility.non_follower_rules.length} 条）`}
            hint={`判据写在 ${S.visibility.audience_enum.file}:${S.visibility.audience_enum.non_followers_condition_line}：非关注者 = 看客不是作者本人，且看客没关注作者。关注了就看不到这条限制。`}
          >
            <ul className="score-rules">
              {S.visibility.non_follower_rules.map((rule) => (
                <li key={`${rule.file}:${rule.line}`}>
                  <code>{rule.rule}</code>
                  <small className="muted">
                    {"（"}
                    <code>
                      {rule.file}:{rule.line}
                    </code>
                    {"）"}
                  </small>
                </li>
              ))}
            </ul>
          </Field>
          <Field
            label="每条规则对谁生效（全仓库统计）"
            hint="枚举的四个取值，右边是在这套规则库里各出现多少次。"
          >
            <ul className="score-rules">
              {Object.entries(S.visibility.audiences).map(([who, count]) => (
                <li key={who}>
                  <code>{who}</code>：{count} 次
                </li>
              ))}
            </ul>
          </Field>
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>「查不到」不等于「没有」</h2>
            <span className="muted">{S.not_listed.length} 条</span>
          </div>
          <Note>
            这几条是这一页<strong>应该覆盖、但拿不到</strong>的东西。每条都写清楚是
            「上游明说不公开」还是「我们没读到」——前者不是缺口，后者才是。把它们写出来，
            是为了让这一页的空白不冒充「没有」。
          </Note>
          {S.not_listed.map((item) => (
            <Field
              key={item.title}
              label={item.title}
              hint={KIND[item.kind] || item.kind}
            >
              <p>
                <Rich text={item.detail} />
              </p>
              <ul className="score-rules">
                {item.evidence.map((one) => (
                  <li key={one} className="muted">
                    <Rich text={one} />
                  </li>
                ))}
              </ul>
            </Field>
          ))}
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>两份文件给出的值不一样</h2>
            <span className="muted">{S.conflicts.length} 个</span>
          </div>
          <Note warn>
            同一个参数在 <code>home-mixer/params/param.rs</code> 和{" "}
            <code>vm-ranker/params.rs</code> 里各写了一份默认值，而这两份对不上。
            本机不知道线上到底用哪一份，所以<strong>两个都摊出来，不挑一个信</strong>。上面那张
            表里的取值统一按 <code>home-mixer</code> 那一份（推荐流用它的），
            只写在 vm-ranker 里的参数照收。
          </Note>
          <ul className="score-rules">
            {S.conflicts.map((row) => (
              <li key={row.symbol}>
                <strong>{row.symbol}</strong>：
                {row.sources.map((source, index) => (
                  <span key={`${source.file}:${source.line}`}>
                    {index > 0 && " / "}
                    {source.value === null ? NO_DATA : source.value}
                    <small className="muted">
                      {"（"}
                      <code>
                        {source.file}:{source.line}
                      </code>
                      {"）"}
                    </small>
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </section>

        {S.unclassified.length > 0 && (
          <section className="surface">
            <div className="section-title">
              <h2>清单需要重新生成</h2>
              <span className="muted">上游出现了我们没表态的权重参数</span>
            </div>
            <Note warn>
              这一版的清单里，下面这些名字在上游是带权重含义的，但我们的清单没表态。
              正常情况下生成脚本会<strong>拒绝写出清单</strong>，所以看到这一节就说明有人绕过了那道闸。
            </Note>
            <ul className="score-rules">
              {S.unclassified.map((one) => (
                <li key={one} className="muted">
                  <code>{one}</code>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="surface">
          <div className="section-title">
            <h2>这一页没说什么</h2>
          </div>
          <Field
            label="这是规则，不是效果"
            hint="规则和「照它做就能涨粉」之间隔着一整个线上系统，这一页跨不过去。"
          >
            <ul className="score-rules">
              <li className="muted">
                这一页没有任何效果数字。我们的号在采纳某条逻辑之后流量和粉丝涨了多少，
                一律是<strong>{NO_DATA}</strong>——不是 0，是还没有量过。
              </li>
              <li className="muted">
                它说的是一份<strong>公开的开源代码</strong>里的默认值。线上跑的到底是哪一版、
                配置有没有被改过，本机看不到。
              </li>
              <li className="muted">
                清单钉在 <code>{short(upstream.sha)}</code>。上游动过之后，上面的数字在
                被重新解析之前<strong>都属于那一天</strong>——页头那条提示就是为此存在的。
              </li>
            </ul>
          </Field>
        </section>
      </div>
    </>
  );
}
