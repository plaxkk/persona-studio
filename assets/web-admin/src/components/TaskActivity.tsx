import { LoaderCircle, AlertCircle } from "lucide-react";
import { api } from "../api";
import type { Actions, Task } from "../types";
export function TaskActivity({
  tasks,
  actions,
}: {
  tasks: Task[];
  actions: Actions;
}) {
  const active = tasks.filter((t) => ["queued", "running"].includes(t.status));
  const problems = tasks
    .filter((t) => t.status === "failed" || t.result.conflict)
    .slice(0, 3);
  const names: Record<string, string> = {
    sync: "读取新互动",
    post: "准备原创草稿",
    reply: "准备回复草稿",
    chat: "试聊",
    probe: "测试写作引擎",
  };
  return (
    <>
      {active.map((t) => (
        <div className="task-progress" key={t.id}>
          <LoaderCircle size={16} className="spin" />
          <span>
            {names[t.kind] || "内容任务"} ·{" "}
            {t.status === "queued" ? "等待处理" : "正在处理"}
          </span>
          <button
            className="text-button"
            onClick={() =>
              void actions.run(
                () => api("/tasks/" + t.id + "/cancel", "POST", {}),
                "已请求取消",
              )
            }
          >
            取消
          </button>
        </div>
      ))}
      {problems.length > 0 && (
        <details className="task-problems">
          <summary>
            <AlertCircle size={14} />
            最近有 {problems.length} 项任务需要留意
          </summary>
          {problems.map((t) => (
            <div key={t.id}>
              <strong>{names[t.kind]}</strong>
              <p>
                {t.result.conflict
                  ? "你已修改草稿，新生成内容没有覆盖原文。以下版本可供参考。"
                  : t.message || "任务未完成，请检查连接后重试。"}
              </p>
              {t.result.conflict && (
                <textarea
                  aria-label="未覆盖的生成版本"
                  readOnly
                  value={t.result.text || ""}
                />
              )}
            </div>
          ))}
        </details>
      )}
    </>
  );
}
