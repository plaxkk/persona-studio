import {
  ArrowRight,
  ArrowUpRight,
  Pause,
  Play,
  RefreshCw,
  PenLine,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api, label, time } from "../api";
import type { Studio, Actions } from "../types";
import { Heading, Button, Badge, Step, Empty } from "../components/UI";
type Growth = { goal: { target_followers: number } | null; profile: { followers: number | null; observed_at: number; bio: string | null; pinned_tweet_id: string | null } | null; remaining: number | null };
type AnalyticsContent = {
  browser_ready: boolean;
  history_ready: boolean;
  runs: { id: string; window_from: string; window_to: string; observed_at: number;
    received_count: number; parsed_count: number; gap_count: number;
    at_request_limit: number; cloud_status: string }[];
  tasks: { id: string; status: string; created: number; error: string }[];
};
type AnalyticsOverview = {
  browser_ready: boolean;
  runs: { id: string; window_from: string; window_to: string; observed_at: number;
    point_count: number; gap_count: number; cloud_status: string;
    scalars: { followers: number | null; verified_followers: number | null;
      active_followers: number | null; active_followers_approximate: boolean | null } }[];
  tasks: { id: string; status: string; error: string }[];
};
export default function Home({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const o = data.overview,
    s = o.settings;
  const [growth, setGrowth] = useState<Growth | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsContent | null>(null);
  const [analyticsOverview, setAnalyticsOverview] = useState<AnalyticsOverview | null>(null);
  const [historyFrom, setHistoryFrom] = useState("");
  const [historyTo, setHistoryTo] = useState("");
  const [target, setTarget] = useState(1000);
  useEffect(() => {
    api<Growth>("/growth").then((value) => { setGrowth(value); setTarget(value.goal?.target_followers || 1000); }).catch(() => setGrowth(null));
    api<AnalyticsContent>("/analytics/content").then(setAnalytics).catch(() => setAnalytics(null));
    api<AnalyticsOverview>("/analytics/overview").then(setAnalyticsOverview).catch(() => setAnalyticsOverview(null));
  }, [s.x_username, o.events[0]?.id]);
  const engine = o.engines.find((e) => e.id === s.engine);
  const complete =
    o.persona.name !== "未命名的角色" &&
    engine?.status === "ready" &&
    Boolean(data.connections.x.verified_at);
  return (
    <>
      <Heading
        eyebrow="你的角色，你来掌舵"
        title="让表达，有自己的样子。"
        description="在这里整理想法、发现值得回应的声音，再亲自把它们带到 X。"
        action={
          <Button tone="primary" onClick={() => actions.navigate("compose")}>
            <PenLine size={17} />
            写一条推文
          </Button>
        }
      />
      <section className="status-banner">
        <div className="status-icon">
          {s.paused ? <Pause size={21} /> : <Play size={21} />}
        </div>
        <div>
          <strong>
            {s.paused ? "同步与自动生成已暂停" : !o.worker_alive ? "后台服务离线" : "后台服务运行中"}
          </strong>
          <p>
            {s.paused
              ? "同步与自动生成已暂停，仍可修改草稿和试聊。"
              : "产品只读取与写作，所有 X 操作由你完成。"}
          </p>
        </div>
        <Button
          onClick={() =>
            void actions.run(
              () => api("/pause", "POST", { paused: !s.paused }),
              s.paused ? "已恢复同步与生成" : "已暂停",
            )
          }
        >
          {s.paused ? "开始运行" : "暂停运行"}
        </Button>
      </section>
      {!o.worker_alive && (
        <div className="notice warning">
          后台任务服务尚未就绪。已保存的内容可继续编辑，生成与同步暂时无法执行。
        </div>
      )}
      {o.sync?.stale && <div className="notice warning">部分互动来源尚未成功读取，或已超过两个同步周期未更新。请查看下方读取结果。</div>}
      <div className="metrics">
        <button onClick={() => actions.navigate("inbox")}>
          <span>新互动</span>
          <strong>{o.counts.new.toString().padStart(2, "0")}</strong>
          <small>
            值得留意的声音 <ArrowUpRight size={14} />
          </small>
        </button>
        <button onClick={() => actions.navigate("history")}>
          <span>待处理草稿</span>
          <strong>{o.counts.drafts.toString().padStart(2, "0")}</strong>
          <small>
            等你加上最后一笔 <ArrowUpRight size={14} />
          </small>
        </button>
        <button onClick={() => actions.navigate("history")}>
          <span>确认完成</span>
          <strong>{o.counts.confirmed.toString().padStart(2, "0")}</strong>
          <small>由你确认，未经平台验证</small>
        </button>
      </div>
      <section className="surface spaced">
        <div className="section-title"><h2>涨粉目标</h2><Badge>{growth?.profile?.followers == null ? "粉丝数待读取" : `${growth.profile.followers} 位粉丝`}</Badge></div>
        <p>最近一次账号读取：{growth?.profile ? time(growth.profile.observed_at) : "暂无数据"}。目标剩余：{growth?.remaining ?? "暂无数据"}。</p>
        <div className="draft-tools">
          <label>目标粉丝数 <input type="number" min="1" max="100000000" value={target} onChange={(e) => setTarget(Number(e.target.value))} /></label>
          <Button onClick={() => void actions.run(async () => { const value = await api<Growth>("/growth/goal", "PUT", { target_followers: target }); setGrowth(value); return value; }, "涨粉目标已保存")}>保存目标</Button>
          <Button onClick={() => void actions.run(() => api("/growth/read", "POST", {}), "已加入账号读取队列")}>读取粉丝数与主页</Button>
        </div>
        {growth?.profile && <small>主页介绍与置顶记录在本机每日快照中；无法读到精确粉丝数时显示“暂无数据”。</small>}
      </section>
      <section className="surface spaced">
        <div className="section-title"><h2>Subscription 帖子数据</h2><Badge>{analytics?.browser_ready ? "可读取" : "等连接助手更新"}</Badge></div>
        <p>从 X 的 Subscription → Analytics 读取逐帖可见指标。这里显示实际读取的查询范围和缺口，不把一次查询当作完整历史。</p>
        <div className="draft-tools">
          <Button disabled={!analytics?.browser_ready} onClick={() => void actions.run(() => api("/analytics/content/read", "POST", { days: 28 }), "已加入最近 28 天读取队列")}>读取最近 28 天</Button>
          <Button disabled={!analytics?.browser_ready} onClick={() => void actions.run(() => api("/analytics/content/read", "POST", { days: 90 }), "已加入近 3 个月读取队列")}>读取近 3 个月</Button>
        </div>
        <div className="draft-tools">
          <label>历史起始日期 <input type="date" value={historyFrom} onChange={(event) => setHistoryFrom(event.target.value)} /></label>
          <label>历史结束日期 <input type="date" value={historyTo} onChange={(event) => setHistoryTo(event.target.value)} /></label>
          <Button disabled={!analytics?.history_ready || !historyFrom || !historyTo} onClick={() => void actions.run(() => api("/analytics/content/read", "POST", { from_date: historyFrom, to_date: historyTo }), "已加入历史窗口读取队列")}>读取所选历史</Button>
          <Button disabled={!analytics?.history_ready || !historyFrom || !historyTo} onClick={() => void actions.run(() => api("/analytics/content/backfill", "POST", { from_date: historyFrom, to_date: historyTo }), "已安排历史分批补采")}>分批补采所选范围</Button>
          {!analytics?.history_ready && <small>历史窗口读取需连接助手 1.3.12；单次最多 31 天。</small>}
        </div>
        <small>单次读取最多 31 天；分批补采最多 365 天，每段不超过 28 天。已成功的相同窗口会跳过，达到查询上限的窗口仍需进一步拆分。</small>
        {analytics?.tasks.length ? <small>最近任务：{analytics.tasks.filter((task) => task.status === "queued").length} 个排队、{analytics.tasks.filter((task) => task.status === "running").length} 个读取中、{analytics.tasks.filter((task) => task.status === "succeeded").length} 个完成、{analytics.tasks.filter((task) => task.status === "failed").length} 个失败（最近 {analytics.tasks.length} 项）。</small> : null}
        {analytics?.tasks[0] && <p>最近任务：{label(analytics.tasks[0].status)}{analytics.tasks[0].error ? ` · ${label(analytics.tasks[0].error)}` : ""}。</p>}
        {analytics?.runs[0] ? <p>最近实际读取：{time(analytics.runs[0].observed_at)}，查询窗口 {analytics.runs[0].window_from} 至 {analytics.runs[0].window_to}；返回 {analytics.runs[0].received_count} 项，可解析 {analytics.runs[0].parsed_count} 项，缺口 {analytics.runs[0].gap_count} 项{analytics.runs[0].at_request_limit ? "；已达到单次查询上限，需进一步拆分" : ""}。云端状态：{label(analytics.runs[0].cloud_status)}。</p> : <p>暂无读取记录。</p>}
      </section>
      <section className="surface spaced">
        <div className="section-title"><h2>Subscription 账号趋势</h2><Badge>{analyticsOverview?.browser_ready ? "可读取" : "等连接助手更新"}</Badge></div>
        <p>读取 Overview 最近 4 周和上一期的原始趋势。小时回填与每日趋势可能重叠；卡片总量核对前，这里不把它们相加。</p>
        <div className="draft-tools">
          <Button disabled={!analyticsOverview?.browser_ready} onClick={() => void actions.run(() => api("/analytics/overview/read", "POST", {}), "已加入 Overview 读取队列")}>读取最近 4 周趋势</Button>
          {!analyticsOverview?.browser_ready && <small>需要连接助手 1.3.13。</small>}
        </div>
        {analyticsOverview?.tasks[0] && <p>最近任务：{label(analyticsOverview.tasks[0].status)}{analyticsOverview.tasks[0].error ? ` · ${label(analyticsOverview.tasks[0].error)}` : ""}。</p>}
        {analyticsOverview?.runs[0] ? <p>最近读取：{time(analyticsOverview.runs[0].observed_at)}，查询窗口 {analyticsOverview.runs[0].window_from} 至 {analyticsOverview.runs[0].window_to}；保留 {analyticsOverview.runs[0].point_count} 个分项，缺口 {analyticsOverview.runs[0].gap_count} 个。当前粉丝数：{analyticsOverview.runs[0].scalars.followers ?? "暂无数据"}；已认证粉丝：{analyticsOverview.runs[0].scalars.verified_followers ?? "暂无数据"}；活跃粉丝：{analyticsOverview.runs[0].scalars.active_followers == null ? "暂无数据" : `${analyticsOverview.runs[0].scalars.active_followers}${analyticsOverview.runs[0].scalars.active_followers_approximate ? "（约）" : ""}`}。云端状态：{label(analyticsOverview.runs[0].cloud_status)}。</p> : <p>暂无读取记录。</p>}
      </section>
      <div className="home-grid">
        <section className="surface">
          <div className="section-title">
            <h2>{complete ? "角色名片" : "让角色迈出第一步"}</h2>
            <Badge>{complete ? "已配置" : "开始设置"}</Badge>
          </div>
          <div className="persona-card">
            <div className="avatar large">{o.persona.name.slice(0, 1)}</div>
            <div>
              <h3>{o.persona.name}</h3>
              <p>{o.persona.identity}</p>
            </div>
          </div>
          <Step
            done={o.persona.name !== "未命名的角色"}
            title="赋予它名字与性格"
            description="定义独特的声音，导入表达素材。"
            onClick={() => actions.navigate("persona")}
          />
          <Step
            done={engine?.status === "ready"}
            title="选择写作引擎"
            description="Codex、OpenClaw 或 Hermes，可随时切换。"
            onClick={() => actions.navigate("settings")}
          />
          <Step
            done={data.connections.x.status === "ready"}
            title="连接 X，只读取互动"
            description="使用你手动提供的登录凭据。"
            onClick={() => actions.navigate("settings")}
          />
        </section>
        <section className="surface">
          <div className="section-title">
            <h2>最近发生的事</h2>
            <button
              className="text-button"
              onClick={() => actions.navigate("history")}
            >
              全部记录 <ArrowRight size={15} />
            </button>
          </div>
          {o.events.length ? (
            <div className="activity-list">
              {o.events.slice(0, 5).map((e) => (
                <div key={e.id}>
                  <span className="activity-line" />
                  <div>
                    <p>{e.label}</p>
                    <small>{time(e.created)}</small>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title="故事从这里开始"
              description="连接账号、准备草稿后，这里会记录你的工作进展。"
            />
          )}
        </section>
      </div>
      <section className="sync-strip">
        <div>
          <RefreshCw size={17} />
          <strong>X 互动同步</strong>
          <Badge>{label(s.x_status)}</Badge>
        </div>
        <span>最近有来源更新：{time(s.last_sync)}</span>
        {o.sync && <><span>全部来源成功：{time(o.sync.last_complete)}</span><span>最近尝试：{o.sync.latest ? `${time(o.sync.latest.updated)} · ${label(o.sync.latest.status)}${o.sync.latest.error ? ` · ${label(o.sync.latest.error)}` : ""}` : "尚未尝试"}</span></>}
        <span>
          {s.paused
            ? "恢复后再同步"
            : `下次检查：${time(Math.max(Date.now() / 1000, s.last_sync_requested + s.sync_interval))}`}
        </span>
        <Button
          onClick={() =>
            void actions.run(() => api("/sync", "POST", {}), "已加入同步队列")
          }
        >
          现在同步
        </Button>
      </section>
      <p className="bottom-note">
        一个虚构人格，一种独特表达。真实的发布决定，始终在你手中。
      </p>
    </>
  );
}
