import { useState } from "react";
import { History, RotateCcw } from "lucide-react";
import { api, time } from "../api";
import type { Actions, Persona } from "../types";
import { comparePersona, rollbackBody, sameBody } from "../personaDiff";
import { Button } from "./UI";

type Stored = { version: number; body: Persona; created: number };

/** Version history with field-level diffs and append-only rollback.
 *
 * Rollback goes through the ordinary `PUT /persona`: the restored body carries
 * the current version number, the server writes the next one, and the versions
 * already on screen stay untouched. That is why this panel needs no route of
 * its own — and therefore no connection-assistant update.
 */
export default function PersonaHistory({
  current,
  actions,
  onLoad,
  onRollback,
}: {
  current: Persona;
  actions: Actions;
  onLoad: (body: Persona) => void;
  onRollback: (body: Persona) => Promise<void>;
}) {
  const [versions, setVersions] = useState<Stored[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [compared, setCompared] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const open = () =>
    void actions.run(async () => {
      setVersions(await api<Stored[]>("/persona/versions"));
      setLoaded(true);
    });

  return (
    <details onToggle={(e) => e.currentTarget.open && !loaded && open()}>
      <summary>查看历史版本、差异与回退</summary>
      <p className="muted">
        每次保存都会留下一个新版本，历史不会被覆盖。回退是把旧版本的内容写成一个新版本。
      </p>
      {!versions.length && <p className="muted">正在读取历史版本…</p>}
      <ul className="persona-versions">
        {versions.map((item) => {
          const diff = comparePersona(item.body, current);
          const identical = sameBody(item.body, current);
          return (
            <li key={item.version} className="persona-version">
              <div className="persona-version-head">
                <strong>v{item.version}</strong>
                <small>{time(item.created)}</small>
                <span className="muted">
                  {item.version === current.version
                    ? "当前已保存的版本"
                    : identical
                      ? "内容与当前版本一致"
                      : `相对当前版本改了 ${diff.changed.length} 个字段`}
                </span>
              </div>
              {item.version !== current.version && (
                <div className="actions">
                  <Button
                    disabled={busy}
                    onClick={() =>
                      setCompared(compared === item.version ? null : item.version)
                    }
                  >
                    {compared === item.version ? "收起差异" : "查看差异"}
                  </Button>
                  <Button disabled={busy} onClick={() => onLoad(item.body)}>
                    <History size={14} />
                    载入表单
                  </Button>
                  <Button
                    tone="primary"
                    disabled={busy || identical}
                    onClick={() =>
                      setConfirming(confirming === item.version ? null : item.version)
                    }
                  >
                    <RotateCcw size={14} />
                    回退到这一版
                  </Button>
                </div>
              )}
              {compared === item.version && (
                <div className="persona-diff">
                  <p className="muted">
                    左边是 v{item.version} 的内容，右边是当前 v{current.version} 的内容。
                  </p>
                  {!diff.changed.length && <p>两个版本的内容完全一致。</p>}
                  {diff.changed.map((change) => (
                    <div key={change.field} className="persona-diff-field">
                      <strong className="persona-diff-label">{change.label}</strong>
                      <div className="persona-diff-pair">
                        <div>
                          <small>v{item.version}</small>
                          <p>{change.before}</p>
                        </div>
                        <div>
                          <small>v{current.version}</small>
                          <p>{change.after}</p>
                        </div>
                      </div>
                    </div>
                  ))}
                  {!!diff.unchanged.length && (
                    <p className="muted">
                      其余 {diff.unchanged.length} 个字段没有变化，未列出。
                    </p>
                  )}
                </div>
              )}
              {confirming === item.version && (
                <div className="persona-confirm" role="status">
                  <p>
                    回退到 v{item.version}：v{item.version} 的内容会保存成新版本 v
                    {current.version + 1}，已有的 v1–v{current.version} 全部保留。
                    {identical ? "" : "下方表单里未保存的修改会被这份内容替换。"}
                  </p>
                  <div className="actions">
                    <Button disabled={busy} onClick={() => setConfirming(null)}>
                      取消
                    </Button>
                    <Button
                      tone="primary"
                      busy={busy}
                      onClick={() =>
                        void actions.run(async () => {
                          setBusy(true);
                          try {
                            await onRollback(rollbackBody(item.body, current));
                            setConfirming(null);
                            setCompared(null);
                            setVersions(await api<Stored[]>("/persona/versions"));
                          } finally {
                            setBusy(false);
                          }
                        }, `已回退到 v${item.version}，并记录为新版本`)
                      }
                    >
                      确认回退
                    </Button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
