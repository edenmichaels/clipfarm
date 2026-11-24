// Live Watcher (v0.8.7): finds funny moments while the streamers you pick are live. Chat + viewer clips + a
// 3-minute buffer with laughter detection decide when to look closer; the AI judge scores the moment; viewer clips
// and chat 10 minutes later confirm it. Only moments that pass (7/10 by default) become clips in My Clips.
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Page } from "../App";

const mb = (b: number) => (b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)} GB` : `${(b / 1024 ** 2).toFixed(0)} MB`);

export default function LiveWatcher({ go }: { go: (p: Page, t?: string) => void }) {
  const [st, setSt] = useState<any>(null);
  const [cands, setCands] = useState<any[]>([]);
  const [picking, setPicking] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => api.get("/live/status").then(setSt).catch((e) => setMsg(e.message));
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  useEffect(() => { if (picking) api.get("/live/candidates").then(setCands).catch((e) => setMsg(e.message)); }, [picking]);

  const save = async (body: any) => { try { setSt(await api.put("/live/settings", body)); } catch (e: any) { setMsg(e.message); } };
  const chosen: number[] = st?.streamers?.map((s: any) => s.id) ?? [];
  const toggle = (id: number) => save({ streamers: chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id] });

  return (
    <div className="stack">
      <header className="row between">
        <div className="stack tight" style={{ gap: 4 }}>
          <h1>Live Watcher</h1>
          <span className="muted">Watches the streamers you pick while they're live: chat, viewer clips and laughter. Only moments that score {st?.threshold ?? 7}/10 or more become clips.</span>
        </div>
        <span className="row" style={{ gap: 8 }}>
          <button className="btn-ghost" onClick={() => go("new")}>Scan a past stream</button>
          <button className="btn-ghost" onClick={() => setPicking(!picking)}>{picking ? "Done" : "Pick streamers"}</button>
          {st && <button className={st.paused ? "btn-primary" : ""} onClick={() => save({ paused: !st.paused })}>{st.paused ? "▶ Resume" : "⏸ Pause"}</button>}
        </span>
      </header>
      {msg && <span className="muted">{msg}</span>}
      {st?.llm_warning && <div className="banner" role="alert"><span>{st.llm_warning}</span><button className="btn-ghost" onClick={() => go("health")}>Fix</button></div>}
      {st?.error && <span className="warn-text small-text">{st.error}</span>}

      {st && (
        <div className="acct-cards">
          <section className="card stack tight" style={{ gap: 2 }}><span className="muted small-text">CPU (ClipFarm)</span><b>{st.cpu_percent ?? "–"}%</b></section>
          <section className="card stack tight" style={{ gap: 2 }}><span className="muted small-text">Disk (buffers + waiting moments)</span><b>{mb(st.disk_bytes || 0)}</b></section>
          <section className="card stack tight" style={{ gap: 2 }}><span className="muted small-text">Keep moments scoring at least</span>
            <span className="row" style={{ gap: 6 }}><input type="range" min={5} max={9} step={0.5} value={st.threshold} onChange={(e) => save({ threshold: Number(e.target.value) })} /><b>{st.threshold}/10</b></span></section>
          <section className="card stack tight" style={{ gap: 2 }}><span className="muted small-text">Close looks per streamer per hour (max)</span><b>{st.per_hour}</b></section>
        </div>)}

      {picking && (
        <section className="card stack tight">
          <h2>Pick streamers</h2>
          <span className="muted small-text">Only Twitch streamers you have permission or a campaign for can be watched.</span>
          {cands.map((c) => (
            <label key={c.id} className="row" style={{ gap: 8, opacity: c.allowed ? 1 : 0.5 }} title={c.why || ""}>
              <input type="checkbox" checked={chosen.includes(c.id)} disabled={!c.allowed} onChange={() => toggle(c.id)} />
              {c.display_name} <span className="muted small-text">{c.allowed ? `twitch.tv/${c.login}` : c.why}</span>
            </label>))}
          {cands.length === 0 && <span className="muted">No Twitch streamers yet. Add them in a project first.</span>}
        </section>)}

      <section className="card stack tight">
        <h2 className="accent">Streamers</h2>
        {(st?.streamers ?? []).length === 0 ? <span className="muted">No streamers picked yet. Press "Pick streamers".</span> :
          st.streamers.map((s: any) => (
            <div key={s.id} className="row between">
              <span><span className={s.live && !st.paused ? "pill ok" : "pill"}>{st.paused ? "Paused" : s.live ? "● Live" : "Offline"}</span> <b>{s.display_name}</b> {s.title && <span className="muted small-text">· {s.title}</span>}</span>
              <span className="muted small-text">{s.triggers_hour} close look{s.triggers_hour === 1 ? "" : "s"} this hour · {s.kept} clip{s.kept === 1 ? "" : "s"} kept{s.live ? ` · buffer ${s.buffer_sec}s` : ""}{s.buffer_error ? ` · ${s.buffer_error}` : ""}</span>
            </div>))}
      </section>

      <section className="card stack tight">
        <h2 className="accent">Recent moments</h2>
        {(st?.recent ?? []).length === 0 ? <span className="muted">Nothing judged yet.</span> : st.recent.map((m: any) => (
          <span key={m.id} className="small-text">
            <span className={m.status === "kept" ? "ok-text" : m.status === "pending" ? "" : "muted"}>{m.status === "kept" ? "✓ kept" : m.status === "pending" ? "… waiting 10 min" : "✗ dropped"}</span>
            {" "}<b>{m.display_name}</b> · {m.trigger.replace("_", " ")} · judge {m.judge_score ?? "–"}{m.final_score != null ? ` → ${m.final_score}` : ""} · <span className="muted">{m.reason}</span>
          </span>))}
      </section>
    </div>
  );
}
