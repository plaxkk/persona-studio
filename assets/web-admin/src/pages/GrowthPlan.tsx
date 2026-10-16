import { useCallback, useEffect, useState } from "react";
import { api, time } from "../api";
import type { Actions } from "../types";
import { Badge, Button, Field, Heading } from "../components/UI";

type Milestone = { id: number; target: number; deadline: string | null; stage_start: number | null;
  remaining: number | null; progress: number | null; completed_at: number | null };
type PlanAction = { id?: string; title: string; problem: string; measure: string; effort: string; evidence: string };
type WeekPlan = { id: string; week_start: string; status: string; source_report_id: string | null; actions: PlanAction[] };
type Checkin = { id: string; day: string; kind: string; note: string; action_title: string | null;
  publication_url: string | null; minutes: number | null };
type Publication = { draft_id: string; url: string | null; publication_kind: string; text: string;
  time_source: "platform" | "user_confirmed" };
type Experiment = { id: string; hypothesis: string; metric: string; baseline: { note?: string };
  started_at: number; planned_end_at: number; status: string; result: { note?: string; early?: boolean };
  decision: string; conclusion_ready: boolean; mature_post_count: number;
  minutes_logged: number | null; publications: Publication[] };
type Reward = { observed_at: number; page_status: string; period: string | null;
  metrics: { label: string; current_display: string | null }[]; raw: { lines: string[]; text: string };
  cloud_status: string; cloud_error: string };
type FollowerObservation = { observed: number; exact_counts: number; verified_observed: number;
  bands: Record<string, number>; latest_read: { observed_at: number; discovered: number; read_count: number; stopped_reason: string; cloud_status: string; cloud_error: string } | null;
  latest_profile_total: number | null };
type Relationship = { handle: string; note: string; interaction_url: string | null;
  next_step: string; updated_at: number; followers: number | null; verified: number | null };
type State = { account: string; today: string; week_start: string;
  profile: { followers: number; observed_at: number; stale: boolean } | null;
  milestones: Milestone[]; current_milestone_id: number | null; plan: WeekPlan | null;
  checkins: Checkin[]; publications: Publication[]; experiments: Experiment[];
  previous_report: { id: string; week_start: string; report: { issues: { title: string; evidence: string }[];
    unavailable: string[]; observed: string[] }; facts: { run_count?: number; followers?: { net: number | null };
    posts?: { post: number; reply: number; metrics?: Record<string, { total: number | null }> } };
    plan: WeekPlan | null; checkins: { kind: string; count: number; minutes: number | null }[] } | null;
  browser_ready: boolean; growth_read_tasks: { kind: string; status: string; error: string; created: number }[];
  rewards: Reward[]; follower_observation: FollowerObservation; relationships: Relationship[] };

const blankAction = (): PlanAction => ({ title: "", problem: "", measure: "", effort: "", evidence: "" });
const errorText = (error: unknown) => error instanceof Error ? error.message : "操作失败，请稍后再试。";
const cloudLabel = (status: string, error?: string) => status === "synced" ? "已同步" :
  error === "http_404" ? "目标 Supabase 尚未建立成长计划数据表" :
  status === "retry" ? "暂未同步，后台会重试" : "待同步";
const taskLabel = (status: string, error: string) => status === "succeeded" ? "已完成" :
  status === "queued" ? "等待读取" : status === "running" ? "正在读取" :
  error === "browser_offline" ? "连接助手暂时离线" :
  status === "failed" ? "读取失败，可稍后重试" : "未完成";

export default function GrowthPlan({ actions }: { actions: Actions }) {
  const [state, setState] = useState<State | null>(null);
  const [items, setItems] = useState<PlanAction[]>([]);
  const [target, setTarget] = useState("");
  const [deadline, setDeadline] = useState("");
  const [checkKind, setCheckKind] = useState("other");
  const [checkNote, setCheckNote] = useState("");
  const [checkMinutes, setCheckMinutes] = useState("");
  const [checkAction, setCheckAction] = useState("");
  const [checkPublication, setCheckPublication] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [metric, setMetric] = useState("");
  const [baseline, setBaseline] = useState("");
  const [result, setResult] = useState("");
  const [decision, setDecision] = useState("");
  const [relationHandle, setRelationHandle] = useState("");
  const [relationNote, setRelationNote] = useState("");
  const [relationLink, setRelationLink] = useState("");
  const [relationNext, setRelationNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const value = await api<State>("/growth/plan");
    setState(value);
    setItems(value.plan?.actions.map(({ title, problem, measure, effort, evidence }) =>
      ({ title, problem, measure, effort, evidence })) || []);
    const current = value.milestones.find((item) => item.id === value.current_milestone_id);
    setTarget(current ? String(current.target) : "");
    setDeadline(current?.deadline || "");
    return value;
  }, []);
  useEffect(() => { void load().catch((e) => setError(errorText(e))); }, [load]);
  const run = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true); setError("");
    try { await fn(); await load(); actions.notify(success); }
    catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  };
  const current = state?.milestones.find((item) => item.id === state.current_milestone_id);
  const logged = new Set(state?.checkins.map((item) => item.publication_url).filter(Boolean));
  const availablePublications = state?.publications.filter((item) => item.url && !logged.has(item.url)) || [];
  const activeExperiment = state?.experiments.find((item) => item.status === "active");
  const updateAction = (position: number, key: keyof PlanAction, value: string) =>
    setItems((existing) => existing.map((item, index) => index === position ? { ...item, [key]: value } : item));

  return <>
    <Heading eyebrow="从目标到行动" title="成长计划"
      description="设定阶段目标，确认这周要做的事，每天记下实际行动，再用周报决定下一步。所有发布都由你自己完成。"
      action={<Button onClick={() => actions.navigate("reports")}>查看每周诊断</Button>} />
    {error && <p role="alert" className="error">{error}</p>}
    {!state ? <section className="surface"><p>正在读取成长计划…</p></section> : <>
      {!state.account && <section className="surface"><p>先在设置中连接 X 账号，才能把计划与真实账号数据关联。</p></section>}
      <section className="surface spaced">
        <div className="section-title"><h2>现在走到哪一步</h2><Badge>{current ? `${current.target.toLocaleString()} 粉目标` : "暂无目标"}</Badge></div>
        <p>{state.profile ? `最近读取 ${state.profile.followers.toLocaleString()} 粉 · ${time(state.profile.observed_at)}${state.profile.stale ? " · 数据已过期" : ""}` : "尚无可信粉丝读数；先从首页读取账号主页。"}</p>
        {current && <>
          <p>阶段起点：{current.stage_start ?? "暂无数据"} · 距离目标：{current.remaining ?? "暂无数据"} · 阶段进度：{current.progress == null ? "暂无数据" : `${Math.round(current.progress * 100)}%`}</p>
          <div className="draft-tools">
            <Field label="当前阶段目标"><input type="number" min="1" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
            <Field label="目标期限（可选）"><input type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} /></Field>
            <Button disabled={busy || !target} onClick={() => void run(() => api(`/growth/milestones/${current.id}`, "PUT", { target: Number(target), deadline: deadline || null }), "里程碑已保存")}>保存阶段目标</Button>
          </div>
        </>}
        <div className="growth-milestones">{state.milestones.map((item) => <div key={item.id} className="growth-milestone">
          <strong>{item.target.toLocaleString()}</strong><small>{item.completed_at ? `已完成 · ${time(item.completed_at)}` : item.id === current?.id ? "当前阶段" : "待开始"}</small>
          {item.deadline && <small>期限 {item.deadline}</small>}
        </div>)}</div>
        <small>完成的里程碑会保留；粉丝回落时，当前差额仍按最新读数显示。旧目标记录仍保存在原数据中。</small>
      </section>

      <section className="surface spaced">
        <div className="section-title"><h2>这周最多三件事</h2><Badge>{state.plan?.status === "confirmed" ? "已确认" : "待你确认"}</Badge></div>
        <p>周一 {state.week_start} 开始。建议来自上一份周报；没有足够证据时不会自动凑满三项。</p>
        {!state.plan && <Button disabled={busy || !state.account} onClick={() => void run(() => api("/growth/weeks/suggest", "POST", {}), "已准备本周计划")}>根据周报准备计划</Button>}
        {state.plan && <>
          {state.plan.source_report_id ? <small>依据：上一份每周诊断。你可以修改每项行动后再确认。</small> : <small>暂无可用周报，请自行补充这周要尝试的行动。</small>}
          {items.map((item, index) => <div className="growth-action" key={index}>
            <h3>行动 {index + 1}</h3>
            <Field label="具体做什么"><textarea value={item.title} onChange={(e) => updateAction(index, "title", e.target.value)} /></Field>
            <div className="growth-fields"><Field label="要解决的问题"><input value={item.problem} onChange={(e) => updateAction(index, "problem", e.target.value)} /></Field>
              <Field label="观察什么指标"><input value={item.measure} onChange={(e) => updateAction(index, "measure", e.target.value)} /></Field>
              <Field label="预计投入"><input value={item.effort} onChange={(e) => updateAction(index, "effort", e.target.value)} /></Field></div>
            {item.evidence && <small>依据：{item.evidence}</small>}
            <Button disabled={busy} onClick={() => setItems((list) => list.filter((_, i) => i !== index))}>移除</Button>
          </div>)}
          {items.length < 3 && <Button disabled={busy} onClick={() => setItems((list) => [...list, blankAction()])}>加一项行动</Button>}
          <div className="draft-tools"><Button disabled={busy} onClick={() => void run(() => api(`/growth/weeks/${state.plan!.id}`, "PUT", { actions: items, confirm: false }), "计划草稿已保存")}>保存草稿</Button>
            <Button tone="primary" disabled={busy || !items.length || items.some((item) => !item.title.trim())} onClick={() => void run(() => api(`/growth/weeks/${state.plan!.id}`, "PUT", { actions: items, confirm: true }), "本周计划已确认")}>确认本周计划</Button></div>
        </>}
      </section>

      <section className="surface spaced">
        <div className="section-title"><h2>今天做了什么</h2><Badge>{state.today}</Badge></div>
        <p>已关联的发布记录会出现在下面；其他行动可以手动记录。只把真实完成的事记进来。</p>
        {availablePublications.length > 0 && <div className="growth-publications">{availablePublications.map((item) => <p key={item.draft_id}>
          <a href={item.url || "#"} target="_blank" rel="noreferrer">{item.text.slice(0, 80) || item.url}</a>
          <small> · {item.time_source === "platform" ? "平台已观察到" : "你已确认发布，平台时间待核对"}</small>
        </p>)}</div>}
        <div className="growth-fields">
          <Field label="类型"><select value={checkKind} onChange={(e) => setCheckKind(e.target.value)}><option value="original">原创</option><option value="reply">回复</option><option value="profile">主页调整</option><option value="other">其他</option></select></Field>
          <Field label="关联本周行动"><select value={checkAction} onChange={(e) => setCheckAction(e.target.value)}><option value="">不关联</option>{state.plan?.status === "confirmed" && state.plan.actions.map((item) => <option key={item.id} value={item.id}>{item.title.slice(0, 45)}</option>)}</select></Field>
          <Field label="关联发布记录"><select value={checkPublication} onChange={(e) => setCheckPublication(e.target.value)}><option value="">不关联</option>{availablePublications.map((item) => <option key={item.draft_id} value={item.draft_id}>{item.text.slice(0, 45) || item.url}</option>)}</select></Field>
          <Field label="投入分钟（可选）"><input type="number" min="0" max="1440" value={checkMinutes} onChange={(e) => setCheckMinutes(e.target.value)} /></Field>
        </div>
        <Field label="实际做了什么"><textarea value={checkNote} onChange={(e) => setCheckNote(e.target.value)} placeholder="例如：回复了一条关于开发流程的讨论，补充了自己的项目经验" /></Field>
        <Button tone="primary" disabled={busy || (!checkNote.trim() && !checkPublication)} onClick={() => void run(async () => {
          await api("/growth/checkins", "POST", { day: state.today, kind: checkKind, note: checkNote,
            action_id: checkAction || null, publication_id: checkPublication || null,
            minutes: checkMinutes === "" ? null : Number(checkMinutes) });
          setCheckNote(""); setCheckMinutes(""); setCheckPublication("");
        }, "今日行动已记录")}>记录行动</Button>
        {state.checkins.length ? <div className="growth-log">{state.checkins.map((item) => <div key={item.id}>
          <strong>{item.day} · {item.kind === "original" ? "原创" : item.kind === "reply" ? "回复" : item.kind === "profile" ? "主页调整" : "其他"}</strong>
          <p>{item.note || "已关联发布记录"}</p><small>{item.action_title ? `对应计划：${item.action_title} · ` : ""}{item.minutes == null ? "" : `投入 ${item.minutes} 分钟`}</small>
          {item.publication_url && <a href={item.publication_url} target="_blank" rel="noreferrer">查看发布</a>}
        </div>)}</div> : <p>这周还没有行动记录。</p>}
      </section>

      <section className="surface spaced">
        <div className="section-title"><h2>复盘与两周实验</h2><Badge>一个主要变量</Badge></div>
        {state.previous_report ? <div><p>最近完成的周报：{state.previous_report.week_start}；粉丝净增：{state.previous_report.facts.followers?.net ?? "暂无数据"}。</p>
          <p>计划：{state.previous_report.plan?.actions.map((item) => item.title).join("；") || "未确认计划"}。实际记录：{state.previous_report.checkins.map((item) => `${item.kind} ${item.count} 次`).join("、") || "没有打卡"}。</p>
          <p>平台读到的内容：非回复 {state.previous_report.facts.run_count ? state.previous_report.facts.posts?.post : "暂无数据"}、回复 {state.previous_report.facts.run_count ? state.previous_report.facts.posts?.reply : "暂无数据"}；主页访问 {state.previous_report.facts.posts?.metrics?.ProfileVisits?.total ?? "暂无数据"}；帖子归因关注 {state.previous_report.facts.posts?.metrics?.Follows?.total ?? "暂无数据"}。这些指标口径不同，不拼成精确漏斗。</p>
          {state.previous_report.report.issues.map((issue) => <p key={issue.title}>优先问题：{issue.title}。依据：{issue.evidence}</p>)}
          {!!state.previous_report.report.unavailable.length && <small>缺失数据：{state.previous_report.report.unavailable.slice(0, 2).join("；")}</small>}</div>
          : <p>还没有已完成的周报。行动可以先记录，效果等数据成熟后再看。</p>}
        {activeExperiment ? <div className="growth-action">
          <h3>{activeExperiment.hypothesis}</h3><p>观察指标：{activeExperiment.metric} · 计划结束：{time(activeExperiment.planned_end_at)}</p>
          <small>基线：{activeExperiment.baseline.note || "未记录"}。实验不足两周或帖子未满 72 小时时，只记观察，不下确定性结论。</small>
          <p>已关联发布 {activeExperiment.publications.length} 条，满 72 小时 {activeExperiment.mature_post_count} 条；记录投入 {activeExperiment.minutes_logged ?? "暂无数据"} 分钟。</p>
          {activeExperiment.publications.slice(0, 5).map((post) => post.url && <p key={post.draft_id}><a href={post.url} target="_blank" rel="noreferrer">{post.text.slice(0, 70) || post.url}</a></p>)}
          <Field label="看到的结果"><textarea value={result} onChange={(e) => setResult(e.target.value)} /></Field>
          <Field label="下一步决定（可选）"><input value={decision} onChange={(e) => setDecision(e.target.value)} /></Field>
          <Button disabled={busy || !result.trim()} onClick={() => void run(() => api(`/growth/experiments/${activeExperiment.id}`, "PUT", { result, decision }), "实验结果已记录")}>结束并记录实验</Button>
        </div> : <>
          <div className="growth-fields"><Field label="只测试什么变化"><input value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} placeholder="例如：用项目中的具体失败案例做开头" /></Field>
            <Field label="观察指标"><input value={metric} onChange={(e) => setMetric(e.target.value)} placeholder="例如：72 小时后的主页访问" /></Field></div>
          <Field label="开始前的情况"><textarea value={baseline} onChange={(e) => setBaseline(e.target.value)} /></Field>
          <Button disabled={busy || !hypothesis.trim() || !metric.trim() || !state.account} onClick={() => void run(() => api("/growth/experiments", "POST", { hypothesis, metric, baseline }), "两周实验已开始")}>开始两周实验</Button>
        </>}
        {state.experiments.filter((item) => item.status !== "active").slice(0, 3).map((item) => <div key={item.id} className="growth-log"><strong>{item.hypothesis}</strong><p>{item.result.note || "暂无结果"}</p>
          <small>{item.conclusion_ready ? `你的下一步决定：${item.decision}。这仍是方向性观察。` : "记录仅供观察；样本和时间不足时没有确定性结论。"}</small></div>)}
      </section>
      <section className="surface spaced">
        <div className="section-title"><h2>原创奖励进度</h2><Badge>{state.browser_ready ? "可读取" : "需连接助手 1.4.5"}</Badge></div>
        <p>X 目前实际显示什么，就记录什么。平台未给出的周期与收益规则保持未知；这一项不影响粉丝里程碑。</p>
        <Button disabled={!state.browser_ready || busy} onClick={() => void run(() => api("/growth/rewards/read", "POST", {}), "已加入奖励进度读取队列")}>读取奖励进度</Button>
        {state.rewards[0] ? <div className="growth-action">
          <p>上次读取：{time(state.rewards[0].observed_at)} · {state.rewards[0].page_status === "check_back_later" ? "页面提示稍后再看" : "页面可见"} · 周期：{state.rewards[0].period || "页面未提供"}</p>
          {state.rewards[0].metrics.map((item) => <p key={item.label}>{item.label}：{item.current_display || "页面未给出可确认的数值"}</p>)}
          <details><summary>查看页面原文</summary><pre className="growth-raw">{state.rewards[0].raw.text}</pre></details>
          {state.rewards[1] && <small>前次读取：{time(state.rewards[1].observed_at)}。两次页面原文均保留，未把页面文案当作永久规则。</small>}
          <small>云端同步：{cloudLabel(state.rewards[0].cloud_status, state.rewards[0].cloud_error)}</small>
        </div> : <p>还没有读取记录。</p>}
        {state.growth_read_tasks.filter((item) => item.kind === "growth_rewards").slice(0, 1).map((item) => <small key={item.created}>最近读取：{taskLabel(item.status, item.error)}</small>)}
      </section>
      <section className="surface spaced">
        <div className="section-title"><h2>高粉关系观察</h2><Badge>已读样本</Badge></div>
        <p>每次读取最多核对 12 位粉丝的主页；下列人数只来自已观察样本，且每档为累计人数。粉丝列表会变化，不能把历史样本当作当前全部粉丝。</p>
        <Button disabled={!state.browser_ready || busy} onClick={() => void run(() => api("/growth/followers/read", "POST", {}), "已加入粉丝样本读取队列")}>继续读取粉丝样本</Button>
        <p>已观察 {state.follower_observation.observed} 位，其中 {state.follower_observation.exact_counts} 位能读到精确粉丝数；最近账号粉丝总数：{state.follower_observation.latest_profile_total ?? "暂无数据"}。已观察的认证账号 {state.follower_observation.verified_observed} 位，认证不等于高粉。</p>
        {!state.follower_observation.observed && <p>尚未读到粉丝样本，分层人数未知。</p>}
        <div className="growth-milestones">{[1000, 3000, 5000, 10000].map((limit) => <div className="growth-milestone" key={limit}><strong>{state.follower_observation.observed ? state.follower_observation.bands[String(limit)] : "—"}</strong><small>≥ {limit.toLocaleString()} 粉</small></div>)}</div>
        {state.follower_observation.latest_read && <small>最近读取 {time(state.follower_observation.latest_read.observed_at)}：本次页面发现 {state.follower_observation.latest_read.discovered} 位，核对 {state.follower_observation.latest_read.read_count} 位。云端：{cloudLabel(state.follower_observation.latest_read.cloud_status, state.follower_observation.latest_read.cloud_error)}。覆盖范围仍可能不完整。</small>}
        {state.growth_read_tasks.filter((item) => item.kind === "growth_followers").slice(0, 1).map((item) => <small key={item.created}>最近读取：{taskLabel(item.status, item.error)}</small>)}
        <h3>值得继续交流的人</h3>
        <div className="growth-fields"><Field label="X 用户名"><input value={relationHandle} onChange={(e) => setRelationHandle(e.target.value.replace(/^@/, ""))} placeholder="例如 lifesinger" /></Field>
          <Field label="互动帖子链接（可选）"><input value={relationLink} onChange={(e) => setRelationLink(e.target.value)} placeholder="https://x.com/.../status/..." /></Field></div>
        <Field label="为什么值得交流"><textarea value={relationNote} onChange={(e) => setRelationNote(e.target.value)} /></Field>
        <Field label="下一步（可选）"><input value={relationNext} onChange={(e) => setRelationNext(e.target.value)} /></Field>
        <Button disabled={busy || !/^[A-Za-z0-9_]{1,15}$/.test(relationHandle) || !relationNote.trim()} onClick={() => void run(async () => {
          await api(`/growth/relationships/${relationHandle}`, "PUT", { handle: relationHandle, note: relationNote,
            interaction_url: relationLink, next_step: relationNext });
          setRelationHandle(""); setRelationNote(""); setRelationLink(""); setRelationNext("");
        }, "关系备注已保存")}>保存关系备注</Button>
        <div className="growth-log">{state.relationships.map((item) => <div key={item.handle}>
          <strong>@{item.handle}</strong><small> · 粉丝数：{item.followers ?? "暂无精确读数"}{item.verified ? " · 已认证" : ""}</small>
          <p>{item.note}</p>{item.next_step && <small>下一步：{item.next_step}</small>}
          {item.interaction_url && <a href={item.interaction_url} target="_blank" rel="noreferrer">查看互动</a>}
          <Button onClick={() => { setRelationHandle(item.handle); setRelationNote(item.note);
            setRelationLink(item.interaction_url || ""); setRelationNext(item.next_step); }}>编辑</Button>
        </div>)}</div>
      </section>
    </>}
  </>;
}
