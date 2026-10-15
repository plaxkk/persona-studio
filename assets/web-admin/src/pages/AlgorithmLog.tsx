import { useState } from "react";
import type { Actions } from "../types";
import AlgorithmLogChanges from "./AlgorithmLogChanges";
import AlgorithmLogSignals from "./AlgorithmLogSignals";
import AlgorithmLogGuide from "./AlgorithmLogGuide";
import type { UpstreamRead } from "./AlgorithmLogSignals";

const TABS = [
  ["overview", "流量与涨粉"],
  ["weights", "行为权重"],
  ["cadence", "发帖频次"],
  ["changes", "更新记录"],
  ["signals", "源码明细"],
] as const;

type Tab = (typeof TABS)[number][0];
type GuideTab = "overview" | "weights" | "cadence";
const isGuideTab = (id: Tab): id is GuideTab =>
  id === "overview" || id === "weights" || id === "cadence";

export default function AlgorithmLog({ actions }: { actions: Actions }) {
  const [tab, setTab] = useState<Tab>("overview");
  const [read, setRead] = useState<UpstreamRead | null>(null);
  // Keep the original data views mounted after first opening so unsaved judgments survive tab switches.
  const [seen, setSeen] = useState<{ signals: boolean; changes: boolean }>({
    signals: false,
    changes: false,
  });

  const go = (id: Tab) => {
    if (id === "signals" || id === "changes") {
      setSeen((was) => (was[id] ? was : { ...was, [id]: true }));
    }
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

  return (
    <>
      {isGuideTab(tab) && (
        <AlgorithmLogGuide view={tab} tabs={tabs} onNavigate={go} />
      )}
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
