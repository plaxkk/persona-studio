import { useState } from "react";
import type { Studio, Actions } from "../types";
import AccountsObserve from "./AccountsObserve";
import AccountsReview from "./AccountsReview";

// 06C 的两半本来就是一件事的上下两步：机器判定「这个号像不像真人」，人再复核
// 「机器判得准不准」。做成两个菜单入口只是当初的实现选择，方案没要求——所以
// 现在收成一个入口、两个标签。
const TABS = [
  ["observe", "看一个号"],
  ["review", "复核室"],
] as const;

type Tab = (typeof TABS)[number][0];

export default function Accounts({
  data,
  actions,
}: {
  data: Studio;
  actions: Actions;
}) {
  const [tab, setTab] = useState<Tab>("observe");
  // 两个「切到某个标签」的入口都走这里：切过去之前先把它挂上。只写 setTab 的话，
  // 点「复核室」会切到一个还没挂载的地方——标签行也长在各自那一半里，于是连标签
  // 行一起没了。
  const [seen, setSeen] = useState<Record<Tab, boolean>>({
    observe: true,
    review: false,
  });
  const [focus, setFocus] = useState<{ handle: string }>();

  const go = (id: Tab) => {
    setSeen((was) => (was[id] ? was : { ...was, [id]: true }));
    setTab(id);
  };
  const openReview = (handle: string) => {
    setFocus({ handle });
    go("review");
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

  // 挂上之后只藏不卸：这本账的价值就在人写了一半的复核意见上、看一个号那一半的
  // 价值在「正在读哪个号」上，来回切一次标签就丢掉是不能接受的。但藏起来的那一
  // 半也不该继续读——桌面端每 4 秒刷一次，两半都挂着就是两份流量，而且「按需加
  // 载」这句话也就不成立了。所以挂载和读数据是两件事：`seen` 管挂载，`active`
  // 管读。
  return (
    <>
      {seen.observe && (
        <div hidden={tab !== "observe"}>
          <AccountsObserve
            data={data}
            actions={actions}
            tabs={tabs}
            active={tab === "observe"}
            onOpenReview={openReview}
          />
        </div>
      )}
      {seen.review && (
        <div hidden={tab !== "review"}>
          <AccountsReview
            actions={actions}
            tabs={tabs}
            active={tab === "review"}
            focus={focus}
          />
        </div>
      )}
    </>
  );
}
