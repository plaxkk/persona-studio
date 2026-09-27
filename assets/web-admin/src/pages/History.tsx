import { useEffect, useState } from "react";
import type { Studio, Actions } from "../types";
import { Heading, Empty, Button } from "../components/UI";
import { DraftEditor } from "../components/DraftEditor";
import { time } from "../api";
export default function History({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const [selected] = useState(() => sessionStorage.getItem("content-selected"));
  const [filter, setFilter] = useState(selected ? "all" : "draft");
  useEffect(() => {
    if (selected) document.getElementById("content-" + selected)?.scrollIntoView({block: "center"});
    sessionStorage.removeItem("content-selected");
  }, [selected]);
  const drafts = data.drafts.filter(
    (d) => filter === "all" || d.status === filter,
  );
  return (
    <>
      <Heading
        title="每一次表达，都留下来"
        description="这里保留定稿、回复草稿与发布操作。尚未成稿的想法和脑暴，请到写推文继续。"
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
            <div key={d.id} id={"content-" + d.id} className={selected === d.id ? "content-selected" : undefined}>
            {d.creation_id && <Button onClick={() => { sessionStorage.setItem("creation-selected", d.creation_id!); actions.navigate("compose"); }}>回看灵感与创作对话</Button>}
            <DraftEditor
              draft={d}
              username={data.overview.settings.x_username}
              actions={actions}
            />
            </div>
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
