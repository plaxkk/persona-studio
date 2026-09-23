import { useState } from "react";
import { Sparkles, Plus, PenLine } from "lucide-react";
import { api } from "../api";
import type { Studio, Actions, Draft } from "../types";
import { Heading, Field, Button, Empty } from "../components/UI";
import { DraftEditor } from "../components/DraftEditor";
export default function Compose({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const [idea, setIdea] = useState("");
  const drafts = data.drafts.filter(
    (d) => d.kind === "post" && d.status === "draft",
  );
  return (
    <>
      <Heading
        title="把想法，写成它的声音。"
        description="从一个话题开始，或者让角色自由发挥。文案由你定稿，发布也由你完成。"
      />
      <section className="surface idea-box">
        <div className="idea-heading">
          <PenLine size={20} />
          <h2>今天想聊什么？</h2>
        </div>
        <Field label="给角色一个方向">
          <textarea
            rows={3}
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            placeholder="一个小观察、想表达的观点，或刚刚想到的问题…"
          />
        </Field>
        <div className="actions">
          <Button
            tone="primary"
            onClick={() =>
              void actions.run(
                () => api("/generate", "POST", { kind: "post", text: idea }),
                "正在准备新的表达",
              )
            }
          >
            <Sparkles size={16} />
            按人设写一条
          </Button>
          <Button
            onClick={() =>
              void actions.run(
                () =>
                  api<Draft>("/drafts", "POST", { text: idea, kind: "post" }),
                "已保存到草稿",
              )
            }
          >
            <Plus size={16} />
            自己写，保存草稿
          </Button>
        </div>
        <p className="muted">
          暂停时仍可手动保存草稿；恢复运行后可以请求 AI 生成。
        </p>
      </section>
      <div className="section-title spaced">
        <h2>等待定稿</h2>
        <span className="muted">{drafts.length} 条原创草稿</span>
      </div>
      {drafts.length ? (
        <div className="draft-list">
          {drafts.map((d) => (
            <DraftEditor
              key={d.id}
              draft={d}
              username={data.overview.settings.x_username}
              actions={actions}
            />
          ))}
        </div>
      ) : (
        <section className="surface">
          <Empty
            title="第一条推文，还等着你的灵感"
            description="草稿会保存在本机。你可以修改、换个写法，或稍后再继续。"
          />
        </section>
      )}
    </>
  );
}
