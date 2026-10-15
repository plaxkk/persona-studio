import type { ReactNode } from "react";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import { Badge, Heading } from "../components/UI";
import raw from "../data/algorithmSignals.json";

type View = "overview" | "weights" | "cadence";
type Signal = (typeof raw.entries)[number];

const sourceBase = `https://github.com/${raw.upstream.repo}/blob/${raw.upstream.sha}`;
const entry = (symbol: string) => raw.entries.find((one) => one.symbol === symbol);
const shown = (signal: Signal | undefined) =>
  signal?.value === null || signal?.value === undefined
    ? "暂无数据"
    : typeof signal.value === "boolean"
      ? signal.value ? "开启" : "关闭"
      : typeof signal.value === "number"
        ? `${signal.value > 0 ? "+" : ""}${signal.value}`
        : String(signal.value);
const plainValue = (signal: Signal | undefined) =>
  signal?.value === null || signal?.value === undefined ? "暂无数据" : String(signal.value);
const source = (signal: Signal) =>
  `${sourceBase}/${signal.source.file}#L${signal.source.line}`;

function Evidence({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a className="algorithm-evidence" href={href} target="_blank" rel="noopener noreferrer">
      {children} <ArrowUpRight size={13} aria-hidden="true" />
    </a>
  );
}

function Overview({ onNavigate }: { onNavigate: (view: View) => void }) {
  const stages = ["进入候选", "通过过滤", "预测与排序", "得到展示", "选择关注"];
  const diagnoses = [
    {
      number: "01 · 触达",
      title: "曝光不足",
      text: "先区分原创与回复，检查目标读者、内容时效和可见性。低曝光本身不能证明账号被限流。",
      metric: "看：可获取的推荐展示、发布时间",
    },
    {
      number: "02 · 内容",
      title: "看了但少互动",
      text: "开头是否说清问题？正文有没有实例、方法或值得讨论的判断？不要只追点赞。",
      metric: "看：阅读、回复、转发和分享",
    },
    {
      number: "03 · 关注",
      title: "互动了却少关注",
      text: "主页和近期内容需要回答：下次关注你，还能持续得到什么？单帖归因缺失就别补数字。",
      metric: "看：主页访问、同期粉丝净增",
    },
  ];

  return (
    <div className="algorithm-guide-stack">
      <section className="algorithm-hero">
        <p className="algorithm-kicker">一条帖的机会，从合适的读者开始</p>
        <h2>涨粉不是“多发几条”就会发生</h2>
        <p>
          X 为每位读者单独找候选、预测可能的行为，再决定展示顺序。算法提供被看见的机会；
          读者觉得你值得持续关注，才会成为粉丝。
        </p>
        <ol className="algorithm-flow" aria-label="从分发到关注的五个环节">
          {stages.map((stage, index) => (
            <li key={stage}>
              <span>{stage}</span>
              {index < stages.length - 1 && <ArrowRight size={15} aria-hidden="true" />}
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="algorithm-diagnosis-title">
        <div className="algorithm-section-title">
          <div>
            <p className="algorithm-kicker">先定位问题</p>
            <h2 id="algorithm-diagnosis-title">你卡在哪一环？</h2>
          </div>
          <span>从左到右排查，别用一组权重代替诊断</span>
        </div>
        <div className="algorithm-diagnoses">
          {diagnoses.map((one) => (
            <article className="algorithm-diagnosis" key={one.number}>
              <small>{one.number}</small>
              <h3>{one.title}</h3>
              <p>{one.text}</p>
              <strong>{one.metric}</strong>
            </article>
          ))}
        </div>
      </section>

      <section className="surface algorithm-audiences" aria-labelledby="algorithm-audiences-title">
        <div className="algorithm-section-title">
          <div><p className="algorithm-kicker">不同读者，不同入口</p><h2 id="algorithm-audiences-title">谁有机会看到你？</h2></div>
          <Evidence href={`${sourceBase}/README.md`}>For You 流程说明</Evidence>
        </div>
        <div className="algorithm-audience-grid">
          <p><strong>已关注你的人</strong><span>关注关系可以让你的帖进入候选，但仍要经过过滤、排序和展示。</span></p>
          <p><strong>还没关注你的人</strong><span>圈外召回让合适的读者发现你；内容与读者的相关性比“全网统一热度”更关键。</span></p>
          <p><strong>小账号的新帖</strong><span>有条件性的探索路径，门槛随版本变化；被纳入探索不等于保底曝光。</span></p>
        </div>
      </section>

      <div className="algorithm-two-column">
        <section className="surface algorithm-brief">
          <p className="algorithm-kicker">排序信号</p>
          <h2>权重乘的是预测，不是互动次数</h2>
          <p>
            回复、引用、关注、分享、点赞和负反馈都可能进入排序。但 +5 不是“做一次回复就加 5 分”，
            −234 也不是“一次举报抵几百个赞”。
          </p>
          <button type="button" className="algorithm-link-button" onClick={() => onNavigate("weights")}>
            看行为权重与作者动作 <ArrowRight size={15} aria-hidden="true" />
          </button>
          <Evidence href={`${sourceBase}/xai-value-model/scoring.rs#L26`}>X 的权重解释</Evidence>
        </section>
        <section className="surface algorithm-brief">
          <p className="algorithm-kicker">发布节奏</p>
          <h2>没有公开的“每天最优条数”</h2>
          <p>
            部分排序阶段会调整同一轮候选里同作者的多条帖子；这不是“今天第几条帖固定降权”。
            具体频次值得用自己的同主题内容比较。
          </p>
          <button type="button" className="algorithm-link-button" onClick={() => onNavigate("cadence")}>
            看频次与活跃度的证据 <ArrowRight size={15} aria-hidden="true" />
          </button>
          <Evidence href={`${sourceBase}/xai-value-model/scoring.rs#L171`}>同作者多样性公式</Evidence>
        </section>
      </div>

      <section className="algorithm-boundary">
        <strong>这页能解释什么？</strong>
        <span>
          依据公开的 For You 代码说明分发路径与默认参数；搜索、回复区、收益机制及每个人命中的线上实验，
          不能从这份代码直接推定。
        </span>
      </section>
    </div>
  );
}

const keySignals = [
  ["复制链接 / 私信分享 / 其他分享", ["ShareViaCopyLinkWeight", "ShareViaDmWeight", "ShareWeight"]],
  ["回复 / 引用", ["ReplyWeight", "QuoteWeight"]],
  ["关注作者", ["FollowAuthorWeight"]],
  ["转推 / 点赞", ["RetweetWeight", "FavoriteWeight"]],
  ["不感兴趣 / 举报", ["NotInterestedWeight", "ReportWeight"]],
] as const;

function Weights() {
  const signals = raw.entries.filter(
    (one) => one.unit === "weight" && one.source.file === "home-mixer/params/param.rs",
  );
  return (
    <div className="algorithm-guide-stack">
      <section className="algorithm-hero algorithm-hero-small">
        <p className="algorithm-kicker">先分清谁做了什么</p>
        <h2>这些数字预测的是读者行为</h2>
        <p>
          排序用的是“这位读者看到这条帖后可能做什么”，不是作者自己点赞、回复或发帖的积分。
          各行为的基准概率不同，系数不能跨动作换算成运营优先级。
        </p>
      </section>

      <section className="algorithm-two-column" aria-label="关键行为信号和作者动作">
        <div className="surface algorithm-brief">
          <p className="algorithm-kicker">读者行为 · For You 默认系数</p>
          <h2>先看关键项</h2>
          <div className="algorithm-signal-list">
            {keySignals.map(([label, symbols]) => (
              <div key={label}>
                <span>{label}</span>
                <strong className={symbols.some((symbol) => {
                  const value = entry(symbol)?.value;
                  return typeof value === "number" && value < 0;
                }) ? "negative" : ""}>
                  {symbols.map((symbol) => shown(entry(symbol))).join(" / ")}
                </strong>
              </div>
            ))}
          </div>
          <p className="algorithm-mini-note">这是公开代码的默认系数，不是实时线上分数，也不是实际互动次数。</p>
        </div>
        <div className="surface algorithm-brief">
          <p className="algorithm-kicker">作者动作 · 别混为直接加分</p>
          <h2>你做的事，走不同入口</h2>
          <div className="algorithm-action-list">
            <p><strong>发原创</strong><span>可参与相关候选及符合条件的新帖探索；没有“发一条固定加分”。</span></p>
            <p><strong>回复别人</strong><span>能建立对话；部分圈外回复会被 For You 过滤。</span></p>
            <p><strong>点赞 / 转推别人</strong><span>属于自己的阅读与社交行为，不能套用上面的读者行为系数。</span></p>
          </div>
          <Evidence href={`${sourceBase}/home-mixer/filters/oon_retweet_reply_filter.rs`}>
            回复与转推的过滤条件
          </Evidence>
        </div>
      </section>

      <section className="surface algorithm-full-table">
        <div className="algorithm-section-title">
          <div><p className="algorithm-kicker">逐项可追溯</p><h2>完整默认系数</h2></div>
          <span>{signals.length} 项 · Home Mixer · {raw.upstream.sha.slice(0, 7)}</span>
        </div>
        <p className="algorithm-mini-note">
          停留时长是连续预测值，不能和点赞概率横比。0 只表示这一路径的显式默认系数为 0；
          未列出书签独立项，不等于 X 所有系统都忽略书签。
        </p>
        <details>
          <summary>展开全部 {signals.length} 项、适用条件与源码</summary>
          <div className="algorithm-table-scroll">
            <table>
              <thead><tr><th>预测行为或信号</th><th>默认系数</th><th>条件与出处</th></tr></thead>
              <tbody>
                {signals.map((one) => (
                  <tr key={one.id}>
                    <td>{one.name}</td>
                    <td className={typeof one.value === "number" && one.value < 0 ? "negative" : ""}>{shown(one)}</td>
                    <td>
                      {one.condition && <small>{one.condition} · </small>}
                      <Evidence href={source(one)}>源码第 {one.source.line} 行</Evidence>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <section className="algorithm-boundary">
        <strong>同一参数也可能有两份默认值。</strong>
        <span>
          Home Mixer 与 VM Ranker 有 {raw.conflicts.length} 处差异，包括点赞 0.66 / 0.5。
          上表只列 Home Mixer；不同组件和实验配置不能拼成一张“唯一线上权重表”。
          <Evidence href={`${sourceBase}/vm-ranker/params.rs`}>查看 VM Ranker</Evidence>
        </span>
      </section>
    </div>
  );
}

function Cadence() {
  const followerCap = entry("ColdStartFollowerCap");
  const impressionThreshold = entry("ColdStartImpressionThreshold");
  const maxAge = entry("ColdStartMaxPostAgeSecs");
  const thompson = entry("EnableColdStartThompsonSampling");
  return (
    <div className="algorithm-guide-stack">
      <section className="algorithm-hero algorithm-hero-small">
        <p className="algorithm-kicker">频次与活跃度</p>
        <h2>多发能增加尝试次数，却没有固定日更奖励</h2>
        <p>
          公开代码解释了同作者多条候选怎样在一次推荐里相互影响，却没有给所有账号一条
          “每天发 N 条最优”或“在线越久权重越高”的公式。
        </p>
      </section>

      <div className="algorithm-two-column">
        <section className="surface algorithm-brief">
          <p className="algorithm-kicker">源码事实</p>
          <h2>同作者多样性，不是按天扣分</h2>
          <p>
            某些排序阶段会对同一轮候选里同作者的后续帖子施加衰减。当前 VM Ranker 默认参数为
            衰减 0.5、下限 0.25；若该阶段生效，第 1、2、3 条候选的示意乘数为
            1、0.625、0.4375。
          </p>
          <Evidence href={`${sourceBase}/xai-value-model/scoring.rs#L171`}>公式与适用范围</Evidence>
        </section>
        <section className="surface algorithm-brief">
          <p className="algorithm-kicker">运营推论</p>
          <h2>同一时段堆同质内容，收益可能递减</h2>
          <p>
            这里的顺序是“同一次候选集合中的第几条”，不是“今天发的第几条”。
            回复、引用、转推的展示入口也各不相同；不要把高活跃直接等同于原创的圈外分发。
          </p>
          <Evidence href={`${sourceBase}/README.md#filtering`}>For You 候选过滤</Evidence>
        </section>
      </div>

      <section className="surface algorithm-cold-start">
        <div className="algorithm-section-title">
          <div><p className="algorithm-kicker">当前公开默认参数</p><h2>新帖探索不是保底流量</h2></div>
          <span>旧笔记的门槛已随版本变化</span>
        </div>
        <div className="algorithm-thresholds">
          <div><strong>{plainValue(followerCap)}</strong><span>作者粉丝上限</span></div>
          <div><strong>{plainValue(impressionThreshold)}</strong><span>Home 展示门槛</span></div>
          <div><strong>{typeof maxAge?.value === "number" ? `${maxAge.value / 3600} 小时` : "暂无数据"}</strong><span>帖子年龄上限</span></div>
          <div><strong>{shown(thompson)}</strong><span>Thompson Sampling 默认开关</span></div>
        </div>
        <p className="algorithm-mini-note">
          还需满足候选资格和实验条件，随后仍要排序、过滤；这些数字不是“保证先给 200 次展示”。
          <Evidence href={`${sourceBase}/home-mixer/scorers/author_cold_start.rs`}>查看探索逻辑</Evidence>
        </p>
      </section>

      <section className="surface algorithm-frequency-qa">
        <p className="algorithm-kicker">五个常见问题</p>
        <h2>把“活跃”拆成不同动作</h2>
        <details open><summary>连续发帖会被降权吗？</summary><p>源码能证明同一轮候选里的同作者内容可能受到多样性调整；不能据此推断“今天第 2 条起被罚”。</p></details>
        <details><summary>每天发几条最好？</summary><p>没有公开的通用最优条数。先保持主题与形式可比，再用自己的 24/72 小时数据比较节奏。</p></details>
        <details><summary>每天在线、持续活跃会直接加曝光吗？</summary><p>公开的 For You 权重表没有“在线时长直接加分”这一项。活跃可能增加创作与交流机会，但不是已证实的固定奖励。</p></details>
        <details><summary>回复别人能带来多少流量？</summary><p>回复能在对话中被看见；部分圈外回复会被 For You 候选过滤。对话曝光不能套用原创的分发公式。</p></details>
        <details><summary>点赞、转推别人会给我的帖加分吗？</summary><p>不能把读者对某帖的预测行为权重，直接套到作者自己的社交动作上。转推的展示入口和原创也有差异。</p></details>
      </section>

      <section className="surface algorithm-experiment">
        <p className="algorithm-kicker">待验证 · 用自己的数据回答</p>
        <h2>我该隔多久发一条？</h2>
        <ol>
          <li><strong>固定可比内容。</strong>先选相近目标读者、主题和形式的原创帖。</li>
          <li><strong>比较两种节奏。</strong>例如间隔发布与同日集中发布，记录真实发布时间。</li>
          <li><strong>等待样本成熟。</strong>每组至少 10 条、每条满 72 小时，再比较每条前 24/72 小时表现。</li>
          <li><strong>分开解释结果。</strong>展示、互动、主页访问和同期粉丝净增分别看；缺的数据写“暂无数据”。</li>
        </ol>
        <p className="algorithm-mini-note">结论只适用于这组账号样本，不能写成 X 对所有人的通用最优频次。</p>
      </section>
    </div>
  );
}

export default function AlgorithmLogGuide({
  view,
  tabs,
  onNavigate,
}: {
  view: View;
  tabs: ReactNode;
  onNavigate: (view: View) => void;
}) {
  return (
    <>
      <Heading
        eyebrow="算法更新 · 运营版"
        title={view === "overview" ? "看懂流量，再决定做什么" : view === "weights" ? "行为权重怎么读" : "发帖频次与活跃度"}
        description="先讲你能据此作出的运营判断，再给出处、版本和不确定之处。"
        action={<Badge tone="green">源码快照 {raw.upstream.sha.slice(0, 7)}</Badge>}
      />
      {tabs}
      <p className="algorithm-version">
        公开 For You 代码 · 读取于 {raw.upstream.read_at} · 默认参数不等于每位读者实际命中的线上实验配置
      </p>
      {view === "overview" ? <Overview onNavigate={onNavigate} /> : view === "weights" ? <Weights /> : <Cadence />}
    </>
  );
}
