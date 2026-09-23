import {
  ArrowRight,
  ArrowUpRight,
  Pause,
  Play,
  RefreshCw,
  PenLine,
} from "lucide-react";
import { api, label, time } from "../api";
import type { Studio, Actions } from "../types";
import { Heading, Button, Badge, Step, Empty } from "../components/UI";
export default function Home({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const o = data.overview,
    s = o.settings;
  const engine = o.engines.find((e) => e.id === s.engine);
  const complete =
    o.persona.name !== "未命名的角色" &&
    engine?.status === "ready" &&
    data.connections.x.status === "ready";
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
            {s.paused ? "角色正在休息" : "正在收集互动与准备草稿"}
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
            description="OpenClaw 或 Hermes，可随时切换。"
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
        <span>最近成功：{time(s.last_sync)}</span>
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
