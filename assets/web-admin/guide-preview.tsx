import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import AlgorithmLogGuide from "./src/pages/AlgorithmLogGuide";
import "./src/styles.css";

type View = "overview" | "weights" | "cadence";
function Preview() {
  const requested = new URLSearchParams(window.location.search).get("view");
  const [view, setView] = useState<View>(requested === "weights" || requested === "cadence" ? requested : "overview");
  const tabs = (
    <div className="tabs" role="tablist">
      {([ ["overview", "流量与涨粉"], ["weights", "行为权重"], ["cadence", "发帖频次"] ] as const).map(([id, label]) => (
        <button key={id} role="tab" type="button" className={view === id ? "active" : ""} onClick={() => setView(id)}>{label}</button>
      ))}
    </div>
  );
  return <div className="page"><AlgorithmLogGuide view={view} tabs={tabs} onNavigate={setView} /></div>;
}
createRoot(document.getElementById("root")!).render(<Preview />);
