import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Sparkles, EyeOff } from "lucide-react";
import { api, label, time, safeXUrl } from "../api";
import type { Studio, Actions, Post, InteractionPage, InteractionSkill } from "../types";
import { Heading, Empty, Button, Badge, External } from "../components/UI";
import { DraftEditor } from "../components/DraftEditor";

const topics: Record<string, string> = { developer: "开发与产品", travel: "旅行需求", other: "其他" };
const scoreLabels: Record<string, string> = { topic: "话题相关性 · 30", contribution: "能补充的信息 · 30", discussion: "讨论空间 · 20", learning: "互相学习 · 10", timing: "及时性 · 10" };
const emptyPage: InteractionPage = { items: [], total: 0, offset: 0, limit: 25, topics: { developer: 0, travel: 0, other: 0 } };

function Skills({ actions }: { actions: Actions }) {
  const [open, setOpen] = useState(false);
  const [skills, setSkills] = useState<InteractionSkill[]>([]);
  const [editing, setEditing] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [level, setLevel] = useState<InteractionSkill["level"]>("practiced");
  const [evidence, setEvidence] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const reload = useCallback(() => api<InteractionSkill[]>("/interactions/skills").then(setSkills).catch((error) => actions.notify(error.message, true)), [actions.notify]);
  useEffect(() => { if (open) void reload(); }, [open, reload]);
  const clear = () => { setEditing(null); setName(""); setLevel("practiced"); setEvidence(""); setConfirmed(false); };
  const save = async () => {
    const path = editing === null ? "/interactions/skills" : `/interactions/skills/${editing}`;
    const result = await actions.run(() => api(path, editing === null ? "POST" : "PUT", { name, level, evidence, confirmed }), "能力清单已保存");
    if (result) { clear(); await reload(); }
  };
  return <details className="surface inbox-skills" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>我的能力与学习方向</summary><p>只有你确认过、并写明项目出处的“已实践”能力，才会用于判断能否补充经验。</p>
    {skills.map((skill) => <div className="skill-row" key={skill.id}><div><strong>{skill.name}</strong> · {({ practiced: "已实践", learning: "正在学", want: "想学", uncertain: "待确认" } as Record<string, string>)[skill.level]}{skill.confirmed ? " · 已确认" : ""}<p>{skill.evidence || "尚无项目出处"}</p></div><div className="actions"><Button onClick={() => { setEditing(skill.id); setName(skill.name); setLevel(skill.level); setEvidence(skill.evidence); setConfirmed(Boolean(skill.confirmed)); }}>修改</Button><Button onClick={() => void actions.run(() => api(`/interactions/skills/${skill.id}`, "DELETE"), "已移除").then(reload)}>移除</Button></div></div>)}
    <div className="skill-form"><input aria-label="能力或学习方向" placeholder="例如：FastAPI 数据采集" value={name} onChange={(event) => setName(event.target.value)} /><select aria-label="能力状态" value={level} onChange={(event) => setLevel(event.target.value as InteractionSkill["level"])}><option value="practiced">已实践</option><option value="learning">正在学</option><option value="want">想学</option><option value="uncertain">待确认</option></select><input aria-label="项目出处" placeholder="项目或材料出处（已实践时必填）" value={evidence} onChange={(event) => setEvidence(event.target.value)} /><label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> 我确认这条信息准确</label><Button tone="primary" onClick={() => void save()} disabled={!name.trim() || (level === "practiced" && confirmed && !evidence.trim())}>{editing === null ? "添加" : "保存修改"}</Button>{editing !== null && <Button onClick={clear}>取消</Button>}</div>
  </details>;
}

function PostCard({ post, data, actions, reload }: { post: Post; data: Studio; actions: Actions; reload: () => Promise<void> }) {
  const [note, setNote] = useState(post.context.manual_context_note || "");
  const [contribution, setContribution] = useState(post.review?.contribution || "");
  const [evidence, setEvidence] = useState(post.review?.evidence || "");
  const [decision, setDecision] = useState(post.review?.decision || "");
  const [reason, setReason] = useState(post.review?.decision_reason || "");
  const review = post.review;
  const target = safeXUrl(post.url);
  const act = async (run: () => Promise<unknown>, message: string) => { const result = await actions.run(run, message); if (result) await reload(); };
  const save = () => void act(() => api(`/interactions/${post.id}/review`, "PUT", { context_note: note, contribution, evidence, decision, decision_reason: reason }), "审核记录已保存");
  return <section className="surface post-card">
    <div className="post-top"><div className="avatar">{post.author[0].toUpperCase()}</div><div><strong>@{post.author}</strong><p>发布：{post.context.published_at ? time(post.context.published_at) : "时间未知"} · 采集：{time(post.created)}</p></div><Badge>{topics[review?.topic || "other"]}</Badge><Badge>{label(post.status)}</Badge></div>
    <p className="post-body">{post.text}</p>
    {post.context.parent && <blockquote className="source-preview"><small>会话上文 {post.context.parent.author ? `@${post.context.parent.author}` : ""}</small><p>{post.context.parent.unavailable ? "尚未读取到上文" : post.context.parent.text}</p>{post.context.parent.url && safeXUrl(post.context.parent.url) && <External url={safeXUrl(post.context.parent.url)!}>查看上文</External>}</blockquote>}
    {post.context.quote?.text && <blockquote className="source-preview"><small>引用的内容</small><p>{post.context.quote.text}</p></blockquote>}
    {post.context.media?.length ? <div className="inbox-media"><strong>媒体线索</strong>{post.context.media.map((item, index) => <p key={index}>{item.kind === "video" ? "视频" : "图片"}：{item.description || "缺少可读取的描述，需要在 X 查看"}</p>)}</div> : null}
    <div className={review?.context_status === "ready" ? "inbox-context ready" : "inbox-context pending"}><strong>{review?.context_status === "ready" ? "上下文已核对" : "待补上下文"}</strong> · {review?.context_issue || "尚未核对"}</div>
    {review && <div className="inbox-score"><div className="inbox-score-head"><strong>互动判断</strong><span>{review.score_total === null ? "信息不足，暂不计算总分" : `${review.score_total} / 100 · ${review.score_total >= 70 ? "优先准备" : review.score_total >= 50 ? "备选" : "建议跳过"}`}</span></div>{Object.entries(review.score).map(([key, item]) => <p key={key}><strong>{scoreLabels[key] || key}：{item.value}</strong> · {item.reason}</p>)}<p>场景：{review.scenario || ({ direct: "直接互动", question: "开放提问", discussion: "讨论" } as Record<string, string>)[review.scene]}</p></div>}
    {post.reason && <div className="suggestion"><Sparkles size={15} />{post.reason}</div>}
    <details className="inbox-review"><summary>补充上下文与审核记录</summary><p>如浏览器未读全，先去 X 查看，再用自己的话写下缺失的上下文；至少 20 字，会标为人工核对。</p><textarea aria-label="人工核对的上下文" placeholder="在 X 核对到的上文、引用或媒体内容" value={note} onChange={(event) => setNote(event.target.value)} /><div className="inbox-review-grid"><input aria-label="准备补充什么" placeholder="这条回复准备补充什么" value={contribution} onChange={(event) => setContribution(event.target.value)} /><input aria-label="经验出处" placeholder="真实经验或项目出处" value={evidence} onChange={(event) => setEvidence(event.target.value)} /><select aria-label="审核决定" value={decision} onChange={(event) => setDecision(event.target.value)}><option value="">尚未决定</option><option value="adopted">采用</option><option value="edited">修改后采用</option><option value="discarded">放弃</option></select><input aria-label="修改或放弃原因" placeholder="修改或放弃原因" value={reason} onChange={(event) => setReason(event.target.value)} /></div><Button onClick={save}>保存审核记录</Button></details>
    {post.draft ? <DraftEditor draft={post.draft} username={data.overview.settings.x_username} actions={actions} compact /> : <div className="actions"><Button tone="primary" disabled={review?.context_status !== "ready" || ["skipped", "ignored", "handled"].includes(post.status)} onClick={() => void act(() => api("/generate", "POST", { kind: "reply", post_id: post.id, text: "" }), "已加入回复生成队列")}><Sparkles size={15} />准备回复</Button><Button onClick={() => void act(() => api(`/interactions/${post.id}/context`, "POST", {}), "已加入原帖详情读取队列")}>重读原帖详情</Button>{target && <External url={target}>去 X 查看</External>}<Button onClick={() => void act(() => api(`/interactions/${post.id}/ignore`, "POST", {}), "已忽略")}><EyeOff size={15} />忽略</Button></div>}
  </section>;
}

export default function Inbox({ data, actions }: { data: Studio; actions: Actions }) {
  const [view, setView] = useState("recommended");
  const [topic, setTopic] = useState("all");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<InteractionPage>(emptyPage);
  const [loading, setLoading] = useState(false);
  const requestSequence = useRef(0);
  const reload = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setLoading(true);
    try { const params = new URLSearchParams({ view, topic, q: query.trim(), offset: String(offset), limit: "25" }); const result = await api<InteractionPage>(`/interactions/page?${params}`); if (sequence === requestSequence.current) setPage(result); }
    catch (error) { if (sequence === requestSequence.current) actions.notify((error as Error).message, true); }
    finally { if (sequence === requestSequence.current) setLoading(false); }
  }, [view, topic, query, offset, actions.notify]);
  useEffect(() => { void reload(); }, [reload, data.overview.settings.last_sync]);
  useEffect(() => { const timer = window.setInterval(() => { if (document.visibilityState === "visible") void reload(); }, 20000); return () => window.clearInterval(timer); }, [reload]);
  const changeView = (next: string) => { requestSequence.current++; setPage(emptyPage); setView(next); setOffset(0); };
  const changeTopic = (next: string) => { requestSequence.current++; setPage(emptyPage); setTopic(next); setOffset(0); };
  return <><Heading title="今日互动清单" description="先读全原帖与上下文，再决定是否补充自己的真实经验。推荐分数是审核规则，不代表 X 算法评分。" action={<Button onClick={() => void actions.run(() => api("/sync", "POST", {}), "已加入同步队列")}><RefreshCw size={16} />同步互动</Button>} />
    <div className="tabs" role="tablist">{[["recommended", "今日推荐"], ["direct", "直接问我的"], ["pending", "待补上下文"], ["new", "待处理"], ["all", "全部记录"], ["ignored", "已忽略"], ["skipped", "已跳过"]].map(([value, title]) => <button role="tab" aria-selected={view === value} key={value} className={view === value ? "active" : ""} onClick={() => changeView(value)}>{title}</button>)}</div>
    <div className="inbox-filters"><div className="filter-tabs" role="tablist">{[["all", "全部主题"], ["developer", "开发与产品"], ["travel", "旅行需求"], ["other", "其他"]].map(([value, title]) => <button role="tab" aria-selected={topic === value} key={value} className={topic === value ? "active" : ""} onClick={() => changeTopic(value)}>{title}{value === "all" ? "" : ` ${page.topics[value as keyof typeof page.topics]}`}</button>)}</div><input aria-label="搜索互动" placeholder="搜索正文或作者" value={query} onChange={(event) => { requestSequence.current++; setPage(emptyPage); setQuery(event.target.value); setOffset(0); }} /></div>
    <p className="inbox-count">{view === "recommended" ? "最多选取 30 条近期候选；可以少于上限。" : ""} 当前筛选 {page.total} 条 · 每页 25 条 · {loading ? "读取中…" : "可翻页查看全部历史"}</p><Skills actions={actions} />
    {data.overview.sources.length > 0 && <details className="source-status"><summary>读取范围与来源状态 · 不保证覆盖全部互动</summary><div>{data.overview.sources.map((source) => <p key={source.name}>{({ own: "自己的帖子", mentions: "提及", replies: "回复", quotes: "引用", notifications: "通知", timeline: "关注时间线" } as Record<string, string>)[source.name] || source.name}：{label(source.status)} · 最近成功 {time(source.last_success)} · 本次新增 {source.count} 条 · 最近尝试 {time(source.last_attempt)}</p>)}</div></details>}
    {!page.items.length ? <section className="surface"><Empty title={loading ? "正在读取互动" : "当前筛选没有内容"} description={data.connections.x.status === "ready" ? "可以换个筛选条件，或同步 X 后再查看。" : "先连接 X 账号；产品不会替你发布或回复。"} /></section> : <div className="inbox-list">{page.items.map((post) => <PostCard key={post.id} post={post} data={data} actions={actions} reload={reload} />)}</div>}
    {page.total > 25 && <div className="inbox-pagination"><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>上一页</Button><span>{Math.floor(offset / 25) + 1} / {Math.ceil(page.total / 25)}</span><Button disabled={offset + 25 >= page.total} onClick={() => setOffset(offset + 25)}>下一页</Button></div>}
  </>;
}
