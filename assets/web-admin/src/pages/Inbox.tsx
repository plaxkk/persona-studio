import { useState } from "react";
import { RefreshCw, Sparkles, ArrowUpRight, EyeOff } from "lucide-react";
import { api, label, time, safeXUrl } from "../api";
import type { Studio, Actions } from "../types";
import { Heading, Empty, Button, Badge, External } from "../components/UI";
import { DraftEditor } from "../components/DraftEditor";
const kinds: Record<string, string> = {
  reply: "回复了你",
  mention: "提到了你",
  quote: "引用了你",
  timeline: "关注时间线",
};
export default function Inbox({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const [filter, setFilter] = useState("new");
  const posts = data.posts.filter(
    (p) => filter === "all" || p.status === filter,
  );
  return (
    <>
      <Heading
        title="听见值得回应的声音"
        description="先读懂上下文，再决定回应。生成的文案会留在这里，等待你的判断。"
        action={
          <Button
            onClick={() =>
              void actions.run(() => api("/sync", "POST", {}), "已加入同步队列")
            }
          >
            <RefreshCw size={16} />
            同步互动
          </Button>
        }
      />
      <div className="tabs" role="tablist">
        {[
          ["new", "待处理"],
          ["all", "全部互动"],
          ["ignored", "已忽略"],
          ["skipped", "已跳过"],
        ].map(([value, title]) => (
          <button
            role="tab"
            aria-selected={filter === value}
            key={value}
            className={filter === value ? "active" : ""}
            onClick={() => setFilter(value)}
          >
            {title}
          </button>
        ))}
      </div>
      {data.overview.sources.length > 0 && (
        <details className="source-status">
          <summary>读取范围与来源状态 · 不保证覆盖全部互动</summary>
          <div>
            {data.overview.sources.map((s) => (
              <p key={s.name}>
                {(
                  {
                    own: "自己的帖子",
                    mentions: "提及",
                    replies: "回复",
                    quotes: "引用",
                    notifications: "通知",
                    timeline: "关注时间线",
                  } as Record<string, string>
                )[s.name] || s.name}
                ：{label(s.status)} · 最近成功 {time(s.last_success)} ·{" "}
                {s.count} 条
              </p>
            ))}
          </div>
        </details>
      )}
      {!posts.length ? (
        <section className="surface">
          <Empty
            title={
              data.connections.x.status === "ready"
                ? "暂时没有新的声音"
                : "先连接你的 X 账号"
            }
            description={
              data.connections.x.status === "ready"
                ? "同步成功后，新互动会自动出现在这里。没有新内容和读取失败会分别显示。"
                : "产品会读取互动并准备建议，不会替你发帖或回复。"
            }
            action={
              <Button onClick={() => actions.navigate("settings")}>
                查看连接设置 <ArrowUpRight size={15} />
              </Button>
            }
          />
        </section>
      ) : (
        <div className="inbox-list">
          {posts.map((post) => {
            const draft = data.drafts.find(
              (d) => d.post_id === post.id && d.status === "draft",
            );
            const target = safeXUrl(post.url);
            return (
              <section className="surface post-card" key={post.id}>
                <div className="post-top">
                  <div className="avatar">{post.author[0].toUpperCase()}</div>
                  <div>
                    <strong>@{post.author}</strong>
                    <p>
                      {kinds[post.kind] || "互动"} · {time(post.created)}
                    </p>
                  </div>
                  <Badge>{label(post.status)}</Badge>
                </div>
                <p className="post-body">{post.text}</p>
                {post.context.parent && (
                  <blockquote className="source-preview">
                    <small>
                      会话上文{" "}
                      {post.context.parent.author
                        ? "@" + post.context.parent.author
                        : ""}
                    </small>
                    <p>
                      {post.context.parent.unavailable
                        ? "尚未读取到上文，请在 X 查看完整会话。"
                        : post.context.parent.text}
                    </p>
                    {post.context.parent.url &&
                      safeXUrl(post.context.parent.url) && (
                        <External url={safeXUrl(post.context.parent.url)!}>
                          查看上文
                        </External>
                      )}
                  </blockquote>
                )}
                {post.context.quote?.text && (
                  <blockquote className="source-preview">
                    <small>引用的内容</small>
                    <p>{post.context.quote.text}</p>
                  </blockquote>
                )}
                {post.reason && (
                  <div className="suggestion">
                    <Sparkles size={15} />
                    {post.reason}
                    {post.context.suggestion && (
                      <Badge>
                        {
                          (
                            {
                              reply: "建议回复",
                              like: "可以点赞",
                              repost: "可以转帖",
                              quote: "可以引用",
                              follow: "可以关注",
                              skip: "建议跳过",
                            } as Record<string, string>
                          )[post.context.suggestion]
                        }
                      </Badge>
                    )}
                  </div>
                )}
                {draft ? (
                  <DraftEditor
                    draft={draft}
                    username={data.overview.settings.x_username}
                    actions={actions}
                    compact
                  />
                ) : (
                  <div className="actions">
                    <Button
                      tone="primary"
                      disabled={["skipped", "ignored", "handled"].includes(
                        post.status,
                      )}
                      onClick={() =>
                        void actions.run(
                          () =>
                            api("/generate", "POST", {
                              kind: "reply",
                              post_id: post.id,
                              text: "",
                            }),
                          "已加入回复生成队列",
                        )
                      }
                    >
                      <Sparkles size={15} />
                      准备回复
                    </Button>
                    {target && <External url={target}>去 X 查看</External>}
                    <Button
                      onClick={() =>
                        void actions.run(
                          () =>
                            api(
                              "/interactions/" + post.id + "/ignore",
                              "POST",
                              {},
                            ),
                          "已忽略",
                        )
                      }
                    >
                      <EyeOff size={15} />
                      忽略
                    </Button>
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}
