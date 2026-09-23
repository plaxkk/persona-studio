import { useState } from "react";
import type { Studio, Actions } from "../types";
import { Heading, Empty } from "../components/UI";
import { DraftEditor } from "../components/DraftEditor";
import { time } from "../api";
export default function History({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const [filter, setFilter] = useState("draft");
  const drafts = data.drafts.filter(
    (d) => filter === "all" || d.status === filter,
  );
  return (
    <>
      <Heading
        title="每一次表达，都留下来"
        description="打开 X、用户确认和平台验证是不同的事。这里如实记录你的操作。"
      />
      <div className="tabs">
        {[
          ["draft", "待处理"],
          ["confirmed", "用户确认完成"],
          ["archived", "已归档"],
          ["all", "全部内容"],
        ].map(([value, title]) => (
          <button
            key={value}
            className={filter === value ? "active" : ""}
            onClick={() => setFilter(value)}
          >
            {title}
          </button>
        ))}
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
            title="这里还没有内容"
            description="创建草稿、处理互动后，内容会在这里持续积累。"
          />
        </section>
      )}
      <section className="surface spaced">
        <h2>最近活动</h2>
        <div className="activity-list">
          {data.overview.events.map((e) => (
            <div key={e.id}>
              <span className="activity-line" />
              <div>
                <p>{e.label}</p>
                <small>{time(e.created)}</small>
              </div>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
