import { useCallback, useEffect, useState } from "react";
import { CalendarDays } from "lucide-react";
import type { Studio, Actions } from "../types";
import { api, time } from "../api";
import { Badge, Button, Empty, Field, Heading } from "../components/UI";

const NO_DATA = "暂无数据";
type Quality = "ok" | "low_confidence" | "insufficient_data";
const qualityLabel: Record<Quality, [string, string]> = {
  ok: ["数据齐全", "green"],
  low_confidence: ["数据偏少，结论需谨慎", ""],
  insufficient_data: ["数据不足，只能看缺口", ""],
};
type Issue = {
  code: string;
  title: string;
  evidence: string;
  benchmark: string;
  hypothesis: string;
  alternative: string;
  action: string;
};
// The 14-day ledger (05-2). It is empty until an experiment is registered, and
// an empty ledger is reported as 暂无数据 rather than an implied zero.
type Experiment = {
  id: string;
  hypothesis: string;
  metric: string;
  status: string;
  result: Record<string, unknown>;
  decision: string;
};
type Report = {
  week_start: string;
  week_end: string;
  timezone: string;
  data_quality: Quality;
  method: string;
  observed: string[];
  observed_note: string;
  issues: Issue[];
  priority_note: string;
  last_week_result: { text: string; items: Experiment[] };
  actions: string[];
  actions_note: string;
  unavailable: string[];
};
type Row = {
  id: string;
  week_start: string;
  week_end: string;
  generated_at: number;
  data_quality: Quality;
};
type Listing = { account: string; latest_week_start: string; reports: Row[] };
type Detail = Row & {
  report: Report;
  facts: {
    followers: { net: number | null; start: unknown; end: unknown; reason: string };
    posts: {
      total: number;
      post: number;
      reply: number;
      unclassified: number;
      metrics: Record<string, { total: number | null; known: number; counted: number }>;
    };
  };
};
const message = (error: unknown) =>
  error instanceof Error ? error.message : "读取失败，请重试。";
// §4.8 asks for slots that often have no answer yet. A slot without a value says
// so; it is never left blank and never filled with a guess.
const shown = (value: string) => value || NO_DATA;
const total = (detail: Detail, name: string) => {
  const value = detail.facts.posts.metrics[name]?.total;
  return typeof value === "number" ? String(value) : NO_DATA;
};

export default function WeeklyReport({ actions }: { data: Studio; actions: Actions }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [week, setWeek] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingWeek, setPendingWeek] = useState("");
  const [error, setError] = useState("");
  const refresh = useCallback(() => api<Listing>("/reports/weekly"), []);
  const open = useCallback(async (id: string) => {
    setDetail(await api<Detail>("/reports/weekly/" + id));
  }, []);
  const load = useCallback(async () => {
    const data = await refresh();
    setListing(data);
    setWeek((current) => current || data.latest_week_start);
    setError("");
    if (data.reports.length) await open(data.reports[0].id);
    return data;
  }, [refresh, open]);
  useEffect(() => {
    void load().catch((problem) => setError(message(problem)));
  }, [load]);
  // Generating is a queued job, so watch for the stored report instead of
  // pretending it is ready the moment the request returns.
  useEffect(() => {
    if (!pendingWeek) return;
    let tries = 0;
    const timer = window.setInterval(() => {
      tries += 1;
      void refresh()
        .then(async (data) => {
          setListing(data);
          const row = data.reports.find((item) => item.week_start === pendingWeek);
          if (row) {
            await open(row.id);
            setPendingWeek("");
            window.clearInterval(timer);
          } else if (tries >= 15) {
            setPendingWeek("");
            setError("报告还在队列里。生成任务完成后点“刷新”就能看到。");
            window.clearInterval(timer);
          }
        })
        .catch((problem) => {
          setPendingWeek("");
          setError(message(problem));
          window.clearInterval(timer);
        });
    }, 4000);
    return () => window.clearInterval(timer);
  }, [pendingWeek, refresh, open]);
  const generate = () =>
    actions.run(async () => {
      await api("/reports/weekly/generate", "POST", { week_start: week });
      setPendingWeek(week);
      setError("");
    }, "已加入周报生成队列");
  const report = detail?.report;
  const quality = report ? qualityLabel[report.data_quality] : null;
  const past = listing?.reports || [];
  return (
    <>
      <Heading
        eyebrow="账号诊断"
        title="这一周到底发生了什么"
        description="只写看到的：有数据支撑的事、最值得先解决的一个问题、上周尝试的结果、下周不超过三个动作，以及哪些数据还看不到。"
        action={
          <Button tone="primary" busy={busy} disabled={!week} onClick={() => {
            setBusy(true);
            void generate().then(() => setBusy(false));
          }}>
            生成本周报告
          </Button>
        }
      />
      <section className="surface">
        <div className="weekly-controls">
          <Field label="要生成本周报告的目标周" hint="必须是一个周一；结束得越早，越接近周界当天的读数。">
            <input
              type="date"
              value={week}
              onChange={(event) => setWeek(event.target.value)}
            />
          </Field>
          <div className="actions">
            <Button
              onClick={() =>
                void load().catch((problem) => setError(message(problem)))
              }
            >
              刷新
            </Button>
            {pendingWeek ? <span className="muted">正在等 {pendingWeek} 的报告生成…</span> : null}
          </div>
        </div>
        {error ? <p className="weekly-error">{error}</p> : null}
        {listing && !listing.account ? (
          <p className="muted">先在本机设置里填上 X 用户名，周报才有账号可诊断。</p>
        ) : null}
      </section>
      {past.length ? (
        <section className="surface spaced">
          <div className="section-title">
            <h2>历史周报</h2>
            <span className="muted">共 {past.length} 份</span>
          </div>
          <div className="weekly-list">
            {past.map((row) => (
              <button
                key={row.id}
                className={"weekly-list-item" + (detail?.id === row.id ? " active" : "")}
                onClick={() =>
                  void open(row.id).catch((problem) => setError(message(problem)))
                }
              >
                <span>
                  <CalendarDays size={14} />
                  {row.week_start} → {row.week_end}
                </span>
                <span className="badge">
                  {qualityLabel[row.data_quality as Quality]?.[0] || row.data_quality}
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {!report || !detail ? (
        <section className="surface spaced">
          <Empty
            title="还没有这一周的诊断"
            description="选一个已结束的周一并点“生成本周报告”。周一会自动生成上一周的一份，生成后这里会留下记录。"
          />
        </section>
      ) : (
        <>
          <section className="surface spaced">
            <div className="section-title">
              <h2>
                {report.week_start} → {report.week_end}
              </h2>
              {quality ? <Badge tone={quality[1]}>{quality[0]}</Badge> : null}
            </div>
            <dl className="weekly-numbers">
              <div>
                <dt>本周推文</dt>
                <dd>{detail.facts.posts.total}</dd>
                <small>
                  非回复 {detail.facts.posts.post} · 回复 {detail.facts.posts.reply}
                  {detail.facts.posts.unclassified
                    ? ` · 未分类 ${detail.facts.posts.unclassified}`
                    : ""}
                </small>
              </div>
              <div>
                <dt>展示合计</dt>
                <dd>{total(detail, "Impressions")}</dd>
                <small>主页访问 {total(detail, "ProfileVisits")}</small>
              </div>
              <div>
                <dt>粉丝净增</dt>
                <dd>{detail.facts.followers.net ?? NO_DATA}</dd>
                <small>{detail.facts.followers.net === null ? "见下方「看不到」" : "本周末 − 本周初"}</small>
              </div>
            </dl>
            <h3 className="weekly-heading">看到的三件事</h3>
            {report.observed.length ? (
              <ol className="weekly-observed">
                {report.observed.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ol>
            ) : (
              <p className="muted">{NO_DATA}</p>
            )}
            {report.observed_note ? <p className="muted">{report.observed_note}</p> : null}
          </section>
          <section className="surface spaced">
            <h2 className="weekly-heading">最值得先解决的一个问题</h2>
            {report.issues.length ? (
              report.issues.map((item) => (
                <article key={item.code} className="weekly-issue">
                  <h3>{item.title}</h3>
                  <dl className="weekly-slots">
                    <div>
                      <dt>问题出在哪</dt>
                      <dd>{shown(item.evidence)}</dd>
                    </div>
                    <div>
                      <dt>同类账号做了什么</dt>
                      <dd>{shown(item.benchmark)}</dd>
                    </div>
                    <div>
                      <dt>猜测的原因</dt>
                      <dd>{shown(item.hypothesis)}</dd>
                    </div>
                    <div>
                      <dt>其他解释</dt>
                      <dd>{shown(item.alternative)}</dd>
                    </div>
                    <div>
                      <dt>下周改哪一件</dt>
                      <dd>{shown(item.action)}</dd>
                    </div>
                  </dl>
                </article>
              ))
            ) : (
              <p className="muted">{report.priority_note || NO_DATA}</p>
            )}
          </section>
          <section className="surface spaced">
            <h2 className="weekly-heading">上周尝试的结果</h2>
            {report.last_week_result.text ? (
              <p className="muted">{report.last_week_result.text}</p>
            ) : (
              <div className="weekly-list">
                {report.last_week_result.items.map((item) => (
                  <div key={item.id} className="weekly-experiment">
                    <strong>{item.hypothesis}</strong>
                    <small>
                      {item.metric || NO_DATA} · {item.status || NO_DATA}
                      {item.decision ? " · " + item.decision : ""}
                    </small>
                    <span className="muted">
                      {Object.keys(item.result).length
                        ? JSON.stringify(item.result)
                        : "结果：" + NO_DATA}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
          <section className="surface spaced">
            <h2 className="weekly-heading">下周的动作</h2>
            {report.actions.length ? (
              <ol className="weekly-observed">
                {report.actions.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ol>
            ) : (
              <p className="muted">{report.actions_note || NO_DATA}</p>
            )}
            {report.actions.length > 1 ? (
              <p className="muted">动作不超过三个：只有问题被数据支撑时才会写进这里。</p>
            ) : null}
          </section>
          <section className="surface spaced">
            <h2 className="weekly-heading">哪些数据看不到</h2>
            <ul className="weekly-unavailable">
              {report.unavailable.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </section>
          <section className="surface spaced">
            <h2 className="weekly-heading">口径与方法</h2>
            <p className="muted">{report.method}</p>
            <p className="muted">
              时区 {report.timezone} · 生成于 {time(detail.generated_at)}
            </p>
          </section>
        </>
      )}
    </>
  );
}
