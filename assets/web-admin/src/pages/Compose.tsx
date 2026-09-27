import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, MessageCircle, Save, Sparkles } from "lucide-react";
import { api, time } from "../api";
import { registerSave, flushDrafts } from "../pending";
import type { Studio, Actions, Message } from "../types";
import { Heading, Field, Button, Empty } from "../components/UI";

type Creation = { id: string; idea: string; candidate: string; stage: string; version: number; draft_id: string | null; final_text?: string; updated: number; messages: Message[]; task: {id: string; status: string; error: string; text: string} | null };
const labels: Record<string, string> = {idea: "灵感暂存", writing: "正在打磨", final: "已定稿"};

export default function Compose({data, actions}: {data: Studio; actions: Actions}) {
  const [items, setItems] = useState<Creation[]>([]);
  const [selected, setSelected] = useState(() => sessionStorage.getItem("creation-selected") || "");
  const [idea, setIdea] = useState(() => sessionStorage.getItem("creation-capture") || "");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [filter, setFilter] = useState("all");
  async function refresh() { setItems(await api<Creation[]>("/creations")); setLoaded(true); }
  useEffect(() => { void refresh().catch(e => actions.notify(e.message, true)); }, []);
  function select(id: string) { setSelected(id); sessionStorage.setItem("creation-selected", id); }
  async function capture(open: boolean) {
    setBusy(true);
    try {
      const record = await api<{id: string}>("/creations", "POST", {idea});
      setIdea(""); sessionStorage.removeItem("creation-capture");
      await refresh();
      if (open) select(record.id); else actions.notify("灵感已暂存，随时可以继续创作");
    } catch(e) { actions.notify((e as Error).message, true); } finally { setBusy(false); }
  }
  if (selected) return <Workspace key={selected} id={selected} data={data} actions={actions} back={async () => { await flushDrafts(); select(""); await refresh(); }} />;
  const visible = items.filter(i => filter === "all" || i.stage === filter);
  const linked = new Set(items.map(i => i.draft_id));
  const legacy = data.drafts.filter(d => d.kind === "post" && d.status === "draft" && !linked.has(d.id));
  return <>
    <Heading title="从一点灵感，到一句想说的话。" description="先记下来，和 AI 找角度，再一起打磨。灵感、对话和定稿都留在同一份记录里。" />
    <section className="surface idea-box">
      <h2>先记下一点什么</h2>
      <Field label="灵感、观察或工作片段"><textarea rows={4} value={idea} maxLength={12000} onChange={e => {setIdea(e.target.value); sessionStorage.setItem("creation-capture", e.target.value);}} placeholder="今天发生了什么？哪个判断让你想多聊两句？可以先写得零散一点…" /></Field>
      <div className="actions"><Button busy={busy} disabled={!idea.trim()} onClick={() => void capture(false)}><Save size={16}/>仅暂存灵感</Button><Button tone="primary" busy={busy} disabled={!idea.trim()} onClick={() => void capture(true)}><MessageCircle size={16}/>保存并开始创作</Button></div>
      <p className="muted">暂存不调用 AI。进入创作后，可以先脑暴，也可以直接写候选稿。</p>
    </section>
    <div className="section-title spaced"><h2>我的创作</h2><span className="muted">{items.length} 份记录</span></div>
    <div className="tabs">{[["all","全部"],["idea","灵感暂存"],["writing","正在打磨"],["final","已定稿"]].map(([v,t]) => <button key={v} className={filter===v?"active":""} onClick={()=>setFilter(v)}>{t}</button>)}</div>
    <div className="creation-list">{visible.map(i => <button key={i.id} className="surface creation-card" onClick={()=>select(i.id)}><span className="eyebrow">{labels[i.stage]}</span><strong>{i.idea}</strong><small>{time(i.updated)} · {i.stage === "final" ? "查看定稿" : "继续创作"} →</small></button>)}</div>
    {loaded && !visible.length && <section className="surface"><Empty title="把第一条灵感放在这里" description="不用一开始就写成推文。记下事实、疑问或半句话，之后再一起展开。" /></section>}
    {!!legacy.length && <section className="surface spaced"><p>还有 {legacy.length} 份此前保存的原创草稿。</p><Button onClick={()=>actions.navigate("history")}>到内容库继续处理</Button></section>}
  </>;
}

function Workspace({id, data, actions, back}: {id:string; data:Studio; actions:Actions; back:()=>Promise<void>}) {
  const [record, setRecord] = useState<Creation | null>(null);
  const [idea, setIdea] = useState("");
  const [candidate, setCandidate] = useState("");
  const [input, setInput] = useState(() => sessionStorage.getItem("creation-input:"+id)||"");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const current = useRef({idea:"", candidate:"", record:null as Creation|null});
  current.current = {idea, candidate, record};
  const saving = useRef<Promise<Creation> | null>(null);
  const messagesEnd = useRef<HTMLDivElement | null>(null);
  useEffect(()=>{const el=messagesEnd.current;if(el)el.scrollTop=el.scrollHeight;},[record?.messages.length,record?.task?.status]);
  function accept(r:Creation) {
    const c = current.current;
    if (!c.record || (c.idea === c.record.idea && c.candidate === c.record.candidate)) {
      setIdea(r.idea); setCandidate(r.candidate);
      current.current = {idea:r.idea, candidate:r.candidate, record:r};
    }
    setRecord(r);
  }
  async function reload() { const r=await api<Creation>("/creations/"+id); accept(r); return r; }
  useEffect(()=>{void reload().catch(e=>setError(e.message));},[id]);
  const running = !!record?.task && ["queued","running"].includes(record.task.status);
  useEffect(()=>{
    if (!running) return;
    let disposed=false, timer:ReturnType<typeof setTimeout>;
    async function poll() { try {const r=await api<Creation>("/creations/"+id); if(!disposed) accept(r);} catch(e) {if(!disposed)setError((e as Error).message);} finally {if(!disposed)timer=setTimeout(poll,2000);} }
    timer=setTimeout(poll,1500); return ()=>{disposed=true;clearTimeout(timer);};
  },[running,id]);
  async function save():Promise<Creation> {
    if(saving.current) {await saving.current;return save();}
    const c=current.current;
    if(!c.record) throw new Error("创作记录尚未加载");
    if(c.record.draft_id || (c.idea===c.record.idea && c.candidate===c.record.candidate)) return c.record;
    saving.current=api<Creation>("/creations/"+id,"PUT",{idea:c.idea,candidate:c.candidate,version:c.record.version});
    try {const r=await saving.current; if(current.current.idea===c.idea && current.current.candidate===c.candidate) {setIdea(r.idea);setCandidate(r.candidate);current.current={idea:r.idea,candidate:r.candidate,record:r};} else {current.current.record=r;} setRecord(r); return r;} finally {saving.current=null;}
  }
  const saveRef=useRef(save);saveRef.current=save;
  useEffect(()=>registerSave(()=>saveRef.current()),[id]);
  useEffect(()=>{const guard=(e:BeforeUnloadEvent)=>{const c=current.current;if(c.record && (c.idea!==c.record.idea||c.candidate!==c.record.candidate)){e.preventDefault();e.returnValue="";}};window.addEventListener("beforeunload",guard);return()=>window.removeEventListener("beforeunload",guard);},[]);
  useEffect(()=>{
    if (!record || record.draft_id || busy || running || (idea===record.idea && candidate===record.candidate)) return;
    const timer=setTimeout(()=>{void saveRef.current().catch(e=>setError(e.message));},1100);
    return ()=>clearTimeout(timer);
  },[idea,candidate,record?.version,busy,running]);
  async function perform(fn:()=>Promise<unknown>) {setBusy(true);setError("");try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function send(mode:"brainstorm"|"draft", text:string) {
    await perform(async()=>{
      const r=await save();
      await api("/creations/"+id+"/turn","POST",{version:r.version,text,mode});
      setInput("");sessionStorage.removeItem("creation-input:"+id);
      await reload(); await actions.refresh();
    });
  }
  if(!record) return <section className="surface"><p>{error||"正在打开创作记录…"}</p><Button onClick={()=>void back()}>返回灵感库</Button></section>;
  const final = !!record.draft_id;
  const disabled=busy||running||final;
  const dirty=idea!==record.idea||candidate!==record.candidate;
  return <>
    <Button onClick={()=>void perform(back)} disabled={busy}><ArrowLeft size={16}/>返回灵感库</Button>
    <Heading title={final?"这次表达，已经定稿。":"把这个想法，再聊深一点。"} description={final?"原始灵感和对话保留在这里。到内容库编辑定稿、管理发布。":"找角度、补证据、改语气。候选稿随时可改，只有你确认后才成为定稿。"}/>
    <ol className="creation-steps" aria-label="创作进度">{["记录灵感","脑暴与打磨","确认定稿"].map((t,i)=><li key={t} aria-current={(final?2:record.stage==="idea"?0:1)===i?"step":undefined}>{i+1} · {t}</li>)}</ol>
    {error&&<div role="alert" className="surface">{error}</div>}
    <div className="creation-grid">
      <section className="surface creation-conversation">
        <details open={record.messages.length===0}><summary>原始灵感</summary><Field label="这次想表达的事"><textarea rows={4} value={idea} maxLength={12000} disabled={disabled} onChange={e=>setIdea(e.target.value)}/></Field></details>
        <h2>一起找角度</h2>
        {!record.messages.length && <p className="muted">可以先让 AI 提出三个角度，或告诉它你希望谁读到、想表达什么。这里的讨论不会自动变成发布稿。</p>}
        <div ref={messagesEnd} className="creation-messages" aria-live="polite">{record.messages.map(m=><div className={`creation-message ${m.role}`} key={m.id}><small>{m.role==="user"?"你":"AI 创作搭档"}</small><p>{m.text}</p></div>)}
        {running && <div className="creation-message user"><small>本轮要求</small><p>{record.task?.text}</p><span className="muted">AI 正在回应，可离开后继续…</span><Button onClick={()=>void perform(async()=>{await api("/tasks/"+record.task!.id+"/cancel","POST",{});await reload();})}>停止本轮</Button></div>}
        </div>
        {record.task && ["failed","cancelled"].includes(record.task.status) && <div className="creation-retry" role="status"><p>本轮{record.task.status==="failed"?"未完成":"已停止"}，原有内容已保留。可检查引擎设置后重试。</p><Button disabled={disabled} onClick={()=>{setInput(record.task!.text);sessionStorage.setItem("creation-input:"+id,record.task!.text);}}>恢复本轮要求</Button></div>}
        {!final && <><Field label="继续讨论或提出修改意见"><textarea rows={3} value={input} disabled={busy||running} maxLength={12000} onChange={e=>{setInput(e.target.value);sessionStorage.setItem("creation-input:"+id,e.target.value);}} placeholder="比如：第二个角度更像我，补一点自嘲，但不要编造结果。"/></Field>
        <div className="actions"><Button disabled={disabled||!idea.trim()} onClick={()=>void send("brainstorm",input.trim()||"请从这条灵感提出三个有区别的表达角度，指出需要补充的事实，并问我一个最关键的问题。") }><MessageCircle size={16}/>{record.messages.length?"继续脑暴":"帮我找三个角度"}</Button><Button tone="primary" disabled={disabled||!idea.trim()} onClick={()=>void send("draft",input.trim()||"根据我们已有的讨论和当前候选稿，整理一版推文正文。") }><Sparkles size={16}/>{candidate?"按讨论修改候选稿":"生成候选稿"}</Button></div></>}
      </section>
      <aside className="surface creation-preview"><h2>{final?"最终稿":"候选稿"}</h2>
        {final ? <><p className="creation-final-text">{record.final_text ?? candidate}</p><Button tone="primary" onClick={()=>{sessionStorage.setItem("content-selected",record.draft_id!);actions.navigate("history");}}>到内容库查看定稿</Button></> : <><p className="muted">生成后在这里预览。也可以直接写，或把修改意见发给 AI。</p><Field label="推文正文"><textarea rows={12} value={candidate} maxLength={12000} disabled={disabled} onChange={e=>{setCandidate(e.target.value);setConfirming(false);}} placeholder="不必急着写完。先聊清楚，再让想法落成文字。"/></Field><small>{candidate.length} 字符 · {dirty?"有修改待保存":"已保存"}</small><div className="actions"><Button disabled={disabled||!dirty||!idea.trim()} onClick={()=>void perform(async()=>{await save();actions.notify("创作进度已保存");})}><Save size={16}/>保存进度</Button><Button tone="primary" disabled={disabled||!candidate.trim()||!idea.trim()} onClick={()=>setConfirming(true)}><Check size={16}/>确认定稿</Button></div>
        {confirming&&<div className="creation-confirm"><p>将当前正文保存为最终草稿，保留灵感和全部对话。不会自动发布到 X。</p><div className="actions"><Button disabled={busy} onClick={()=>setConfirming(false)}>再改改</Button><Button tone="primary" busy={busy} onClick={()=>void perform(async()=>{const r=await save();await api("/creations/"+id+"/finalize","POST",{version:r.version});await reload();await actions.refresh();setConfirming(false);})}>保存为最终稿</Button></div></div>}</>}
      </aside>
    </div>
  </>;
}
