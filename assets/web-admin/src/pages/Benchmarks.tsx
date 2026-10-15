import { useCallback, useEffect, useRef, useState } from "react";
import { Eye, Microscope, Plus, RefreshCw } from "lucide-react";
import { api, safeXUrl, time } from "../api";
import type { Studio, Actions } from "../types";
import { Badge, Button, Empty, External, Field, Heading } from "../components/UI";

const NO_DATA = "暂无数据";
// X withholds a count rather than sending 0, so a missing number is written out
// in words (§4.8 "哪些数据看不到") and never rendered as a zero.
const UNSEEN = "看不到";
type Account = {
  handle: string;
  label: string;
  note: string;
  active: number;
  added_at: number;
  posts: number;
  viral: number;
  last_captured: number | null;
  coverage_gap: string;
};
type Post = {
  post_id: string;
  handle: string;
  author: string;
  text: string;
  text_source: string;
  url: string;
  posted_at: number | null;
  kind: string;
  metrics_json: Record<string, number>;
  unknown_metrics: string[];
  is_viral: boolean;
  captured_at: number;
  coverage_gap: string;
};
type Deconstruction = {
  id: string;
  post_id: string;
  handle: string;
  thesis: string;
  hook: string;
  engine: string;
  created: number;
  has_doc: boolean;
};
// §4.7.2 lists 浏览量、回复数、点赞数、收藏数 first; 转推/引用 follow because
// 爆款拆解 needs them to tell a repost from a discussion.
const METRICS: [string, string][] = [
  ["views", "浏览"],
  ["replies", "回复"],
  ["likes", "点赞"],
  ["bookmarks", "收藏"],
  ["retweets", "转推"],
  ["quotes", "引用"],
];

export default function Benchmarks({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const [accounts, setAccounts] = useState<Account[]>([]),
    [posts, setPosts] = useState<Post[]>([]),
    [selected, setSelected] = useState(""),
    [lastQuery, setLastQuery] = useState(""),
    [minFaves, setMinFaves] = useState(500),
    [form, setForm] = useState({ handle: "", label: "", note: "" }),
    [docs, setDocs] = useState<Deconstruction[]>([]),
    [openDoc, setOpenDoc] = useState<{ post_id: string; doc: string } | null>(
      null,
    ),
    [loading, setLoading] = useState(true);
  const actionsRef = useRef(actions);
  actionsRef.current = actions;
  const selectedRef = useRef("");
  const busy = data.overview.tasks.filter(
    (t) => ["queued", "running"].includes(t.status),
  );
  const running = busy.some((t) => t.kind === "benchmark");
  // A deconstruction runs against one post, so the panel marks that post rather
  // than the whole page.
  const deconstructing = new Set(
    busy.filter((t) => t.kind === "deconstruct").map((t) => t.post_id || ""),
  );

  const openAccount = useCallback(async (handle: string) => {
    const [thread, saved] = await Promise.all([
      api<{ posts: Post[] }>(`/benchmarks/posts/${handle}`),
      api<{ deconstructions: Deconstruction[] }>(
        `/benchmarks/deconstructions/${handle}`,
      ),
    ]);
    if (selectedRef.current !== handle) setOpenDoc(null);
    selectedRef.current = handle;
    setSelected(handle);
    setPosts(thread.posts);
    setDocs(saved.deconstructions);
  }, []);

  const load = useCallback(
    async (keep?: string) => {
      const list = await api<{ accounts: Account[]; min_faves: number }>(
        "/benchmarks/accounts",
      );
      setAccounts(list.accounts);
      setMinFaves(list.min_faves);
      // A refresh keeps the operator on whoever they were reading; only when
      // that account is gone (or none was chosen) does it move to the first.
      const pick =
        (keep && list.accounts.some((a) => a.handle === keep) ? keep : "") ||
        list.accounts[0]?.handle ||
        "";
      if (!pick) {
        selectedRef.current = "";
        setSelected("");
        setPosts([]);
        setOpenDoc(null);
        return;
      }
      await openAccount(pick);
    },
    [openAccount],
  );

  // A new Actions object is passed on every app render. Depending on it here
  // would restart the first load and close an open deconstruction repeatedly.
  useEffect(() => {
    void actionsRef.current.run(() => load()).finally(() => setLoading(false));
  }, [load]);

  // A read runs for minutes inside the browser, so the panel keeps looking while
  // it does instead of making the operator reload the page to see the result.
  // A deconstruction is a model call and finishes on its own, but it also has to
  // be picked up here, or the 查看 button would only appear after a manual reload.
  const waiting = running || deconstructing.size > 0;
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => {
      void load(selected).catch(() => {});
    }, 4000);
    return () => clearInterval(timer);
  }, [waiting, load, selected]);

  const current = accounts.find((a) => a.handle === selected);
  return (
    <>
      <Heading
        eyebrow="06B 对标账号"
        title="看别人靠什么火"
        description={`读取别人真正跑出来的高赞推文：固定查询语法，只读不互动，X 没给出来的数字会写成“${UNSEEN}”。`}
        action={<Badge>≥{minFaves} 赞</Badge>}
      />
      <div className="stack">
        <section className="surface">
          <div className="section-title">
            <h2>对标列表</h2>
            {running && <Badge>浏览器正在读取</Badge>}
          </div>
          {loading ? (
            <p className="muted">正在读取名单…</p>
          ) : accounts.length ? (
            <div className="weekly-list">
              {accounts.map((account) => (
                <button
                  key={account.handle}
                  className={`weekly-list-item ${account.handle === selected ? "active" : ""}`}
                  onClick={() => {
                    setLastQuery("");
                    void actions.run(() => openAccount(account.handle));
                  }}
                >
                  <span>
                    <strong>@{account.handle}</strong>
                    {account.label && <small>{account.label}</small>}
                  </span>
                  <span className="muted">
                    {/* 一条都没读到，和读到了但没有一条达标，不是同一件事。 */}
                    {account.posts
                      ? `达标 ${account.viral} / 共 ${account.posts} 条 · ${time(account.last_captured || 0)}`
                      : "尚未读取"}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="muted">还没有对标账号，先在下面加一个。</p>
          )}
          <form
            className="form-stack spaced-small"
            onSubmit={(e) => {
              e.preventDefault();
              void actions.run(async () => {
                const added = await api<{ handle: string }>(
                  "/benchmarks/accounts",
                  "POST",
                  form,
                );
                setForm({ handle: "", label: "", note: "" });
                setLastQuery("");
                await load(added.handle);
              }, "已加入对标列表");
            }}
          >
            <Field label="X 用户名" hint="不带 @，例如 lifesinger。">
              <input
                value={form.handle}
                onChange={(e) => setForm({ ...form, handle: e.target.value })}
                maxLength={15}
                placeholder="lifesinger"
                required
              />
            </Field>
            <Field label="备注名" hint="只给自己看，不会出现在读取里。">
              <input
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                maxLength={80}
                placeholder="例如：玉伯"
              />
            </Field>
            <Button type="submit" disabled={!form.handle.trim()}>
              <Plus size={16} />
              加入对标
            </Button>
          </form>
        </section>

        <section className="surface">
          <div className="section-title">
            <h2>{selected ? `@${selected} 读到的推文` : "读到的推文"}</h2>
            {posts.length ? (
              <span className="muted">
                共 {posts.length} 条 · 达标 {posts.filter((p) => p.is_viral).length} 条
              </span>
            ) : null}
          </div>
          {!selected ? (
            <Empty
              title="还没选账号"
              description="左侧选一个对标账号，再加一次读取，这里就会出现真实读回来的推文。"
            />
          ) : (
            <>
              {current?.coverage_gap && (
                <p className="notice warning">上次没读全：{current.coverage_gap}</p>
              )}
              <div className="actions spaced-small">
                <Button
                  tone="primary"
                  busy={running}
                  onClick={() =>
                    void actions.run(async () => {
                      const started = await api<{ query: string }>(
                        `/benchmarks/read/${selected}`,
                        "POST",
                      );
                      setLastQuery(started.query);
                      await load();
                    }, "已让浏览器开始读取")
                  }
                >
                  <RefreshCw size={16} />
                  读取达标推文
                </Button>
                <span className="muted">
                  读取会自己开一个 X 搜索页，只读、不发布、不互动。
                </span>
              </div>
              {running && (
                <p className="notice">
                  正在读：浏览器取回结果要几分钟，读完这里会自动出现，不用刷新页面。
                </p>
              )}
              {lastQuery && (
                <Field label="最近一次查询" hint="由工作室按 §4.9.1 生成，不经过手输。">
                  <code className="bench-query">{lastQuery}</code>
                </Field>
              )}
              {!posts.length && !running && (
                <p className="muted">
                  <Eye size={14} /> 这个账号还没有读到推文，{NO_DATA}。
                </p>
              )}
              <div className="bench-posts">
                {posts.map((post) => {
                  const url = safeXUrl(post.url);
                  const missing = new Set(post.unknown_metrics);
                  const saved = docs.find((d) => d.post_id === post.post_id);
                  return (
                    <article key={post.post_id} className="bench-post">
                      <header>
                        <span>
                          <strong>@{post.author || selected}</strong>
                          <small>{time(post.posted_at || 0)}</small>
                        </span>
                        <span className="bench-badges">
                          {post.is_viral ? (
                            <Badge tone="green">达标</Badge>
                          ) : (
                            <Badge>低于门槛</Badge>
                          )}
                          {post.kind === "quote" && <Badge>引用推文</Badge>}
                          {post.text_source === "full_text" && <Badge>正文可能被截断</Badge>}
                        </span>
                      </header>
                      <p>{post.text}</p>
                      <dl className="bench-metrics">
                        {METRICS.map(([key, name]) => (
                          <div key={key}>
                            <dt>{name}</dt>
                            <dd>
                              {missing.has(key)
                                ? UNSEEN
                                : typeof post.metrics_json[key] === "number"
                                  ? post.metrics_json[key].toLocaleString("zh-CN")
                                  : UNSEEN}
                            </dd>
                          </div>
                        ))}
                      </dl>
                      <footer>
                        <small className="muted">
                          这个数字读于 {time(post.captured_at)}
                        </small>
                        {url ? <External url={url}>在 X 上查看</External> : null}
                      </footer>
                      {/* 拆解读的是已经存下来的那条推文，不需要再开一次 X 页面。
                          所以这里和“读取”是两件事：读取要浏览器，拆解只要模型。 */}
                      <div className="actions spaced-small">
                        <Button
                          busy={deconstructing.has(post.post_id)}
                          onClick={() =>
                            void actions.run(async () => {
                              await api(
                                `/benchmarks/deconstruct/${post.post_id}`,
                                "POST",
                              );
                              await load(selected);
                            }, "已让模型开始拆解这条")
                          }
                        >
                          <Microscope size={16} />
                          {saved?.post_id === post.post_id ? "重新拆解" : "拆解这条"}
                        </Button>
                        {saved?.post_id === post.post_id && (
                          <Button
                            onClick={() => {
                              const id = post.post_id;
                              void actions.run(async () => {
                                if (openDoc?.post_id === id) {
                                  setOpenDoc(null);
                                  return;
                                }
                                const one = await api<{
                                  post_id: string;
                                  doc: string;
                                }>(`/benchmarks/deconstruction/${id}`);
                                setOpenDoc(one);
                              });
                            }}
                          >
                            <Eye size={16} />
                            {openDoc?.post_id === post.post_id
                              ? "收起拆解"
                              : "查看拆解"}
                          </Button>
                        )}
                        <span className="muted">
                          拆解只写在这里，不会发到 X。
                        </span>
                      </div>
                      {openDoc?.post_id === post.post_id && (
                        <pre className="bench-doc">{openDoc.doc}</pre>
                      )}
                    </article>
                  );
                })}
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}
