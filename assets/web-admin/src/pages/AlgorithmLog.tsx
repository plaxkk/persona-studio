import { useState } from "react";
import type { Actions } from "../types";
import AlgorithmLogChanges from "./AlgorithmLogChanges";
import AlgorithmLogSignals from "./AlgorithmLogSignals";
import type { UpstreamRead } from "./AlgorithmLogSignals";

// 06D 这一页回答两个不同的问题，所以做成两个标签而不是两页：
// 「当前逻辑」= 现在是什么（清单，构建期固化，不发请求）；「改动记录」= 变了什么
// （原来的那一页，一个字没改）。用户要的是前者，所以**默认落在「当前逻辑」**。
const TABS = [
  ["signals", "当前逻辑"],
  ["changes", "改动记录"],
] as const;

type Tab = (typeof TABS)[number][0];

export default function AlgorithmLog({ actions }: { actions: Actions }) {
  const [tab, setTab] = useState<Tab>("signals");
  // 「当前逻辑」那一半要用上游状态去比「清单是不是落后了」，而它自己不读上游。
  // 所以状态存在这里，由「改动记录」那一半读到之后递上来。没读过就是 null，
  // 页面照实说「还没比过」，不假装比过。
  const [read, setRead] = useState<UpstreamRead | null>(null);
  // 两个「切到某个标签」的入口都走这里：切过去之前先把它挂上。只写 setTab 的话，
  // 点「改动记录」会切到一个还没挂载的地方——标签行也长在各自那一半里，于是连
  // 标签行一起没了。（06C 的写法，照抄。）
  const [seen, setSeen] = useState<Record<Tab, boolean>>({
    signals: true,
    changes: false,
  });

  const go = (id: Tab) => {
    setSeen((was) => (was[id] ? was : { ...was, [id]: true }));
    setTab(id);
  };

  const tabs = (
    <div className="tabs" role="tablist">
      {TABS.map(([id, label]) => (
        <button
          type="button"
          role="tab"
          aria-selected={tab === id}
          key={id}
          className={tab === id ? "active" : ""}
          onClick={() => go(id)}
        >
          {label}
        </button>
      ))}
    </div>
  );

  // 挂上之后只藏不卸：人写了一半的判断不能因为切了个标签就丢掉。但藏起来的那
  // 一半不该继续读——所以 `seen` 管挂载，`active` 管读。「当前逻辑」那一半
  // 压根不读，`active` 对它只意味着「标签行要不要显示」。
  return (
    <>
      {seen.signals && (
        <div hidden={tab !== "signals"}>
          <AlgorithmLogSignals
            tabs={tabs}
            active={tab === "signals"}
            read={read}
          />
        </div>
      )}
      {seen.changes && (
        <div hidden={tab !== "changes"}>
          <AlgorithmLogChanges
            actions={actions}
            tabs={tabs}
            active={tab === "changes"}
            onLedger={setRead}
          />
        </div>
      )}
    </>
  );
}
