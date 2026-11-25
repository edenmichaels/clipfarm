// Live Watcher (v0.8.9): chat-first. Reads the chat of the streamers you pick (and the ones auto-pilot picks) while
// they're live. When lots of different people type "clip it", KEKW or "NO WAY" at once (vs that channel's normal),
// or a viewer makes a Twitch clip, the moment is cut from a 3-minute buffer, transcribed, and the local AI judges it
// from the transcript + chat. Only moments scoring 7/10+ become clips. No video is analysed.
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Page } from "../App";
import ChatTags from "../components/ChatTags";
import { LiveDot } from "../components/LiveDot";

const SORTS: [string, string][] = [["live", "Live first"], ["viewers", "Viewers"], ["projected", "Projected A$"],
  ["campaign_type", "Campaign type"], ["chat", "Chat activity now"], ["kept_today", "Clips kept today"], ["hit_rate", "Hit rate"], ["name", "Name"]];
const since = (iso?: string) => { if (!iso) return ""; const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000)); return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`; };
const mb = (b: number) => (b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)} GB` : `${(b / 1024 ** 2).toFixed(0)} MB`);

export default function LiveWatcher({ go }: { go: (p: Page, t?: string) => void }) {
  const [st, setSt] = useState<any>(null);
  const [rows, setRows] = useState<any>(null);
  const [tagsFor, setTagsFor] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get("/live/status").then(setSt).catch((e) => setMsg(e.message));
  const loadRows = (q: Record<string, any> = {}) => {
    const qs = new URLSearchParams(Object.entries(q).map(([k, v]) => [k, String(v)])).toString();
    return api.get(`/live/streamers${qs ? `?${qs}` : ""}`).then(setRows).catch((e) => setMsg(e.message));
  };
  useEffect(() => { load(); loadRows(); const t = setInterval(() => { load(); loadRows(); }, 15000); return () => clearInterval(t); }, []);

  const save = async (body: any) => { try { setSt(await api.put("/live/settings", body)); loadRows(); } catch (e: any) { setMsg(e.message); } };
  const chosen: number[] = (rows?.rows ?? []).filter((r: any) => r.picked).map((r: any) => r.id);
  const toggle = (id: number) => save({ streamers: chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id] });
  const view = rows?.view ?? {};

  return (
    <div className="stack">
      <header className="row between">
        <div className="stack tight" style={{ gap: 4 }}>
          <h1>Live Watcher</h1>
          <span className="muted">Reads the chat of the streamers you tick while they're live. When lots of people type "clip it", KEKW or "NO WAY" at once, or a viewer clips it, the AI judges the moment. Only {st?.threshold ?? 7}/10 or more become clips.</span>
        </div>
        <span className="row" style={{ gap: 8 }}>
          <button className="btn-ghost" onClick={() => go("new")}>Scan a past stream</button>
          {st && <button className={st.paused ? "btn-primary" : ""} onClick={() => save({ paused: !st.paused })}>{st.paused ? "▶ Resume" : "⏸ Pause"}</button>}
        </span>
      </header>
      {msg && <span className="muted">{msg}</span>}
      {st?.llm_warning && <div className="banner" role="alert"><span>{st.llm_warning}</span><button className="btn-ghost" onClick={() => go("health")}>Fix</button></div>}
      {st?.error && <span className="warn-text small-text">{st.error}</span>}

      {st && (
        <div className="acct-cards">
          <section className="card stack tight" style={{ gap: 2 }}><span className="muted small-text">CPU (ClipFarm)</span><b>{st.cpu_percent ?? "–"}%</b></section>
          <section className="card stack tight" style={{ gap: 2 }}><span className="muted small-text">Disk (buffers)</span><b>{mb(st.disk_bytes || 0)}</b></section>
          <section className="card stack tight" style={{ gap: 2 }}><span className="muted small-text">Keep moments scoring at least</span>
            <span className="row" style={{ gap: 6 }}><input type="range" min={5} max={9} step={0.5} value={st.threshold} onChange={(e) => save({ threshold: Number(e.target.value) })} /><b>{st.threshold}/10</b></span></section>
          <section className="card stack tight" style={{ gap: 2 }}><span className="muted small-text">Triggers judged per streamer per hour (max)</span><b>{st.per_hour}</b></section>
        </div>)}

      {st?.autopilot?.enabled && <span className="small-text">Auto-pilot is on: it picks the best live campaign streamers itself{st.autopilot.note ? ` · ${st.autopilot.note}` : ""}.</span>}
      <section className="card stack tight">
        <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
          <h2 className="accent">Streamers</h2>
          <span className="row small-text" style={{ gap: 10, flexWrap: "wrap" }}>
            <label className="line">Sort <select value={view.sort ?? "live"} onChange={(e) => loadRows({ sort: e.target.value })}>{SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="line"><input type="checkbox" checked={!!view.live_only} onChange={(e) => loadRows({ live_only: e.target.checked })} /> Live only</label>
            <label className="line"><input type="checkbox" checked={!!view.joined_only} onChange={(e) => loadRows({ joined_only: e.target.checked })} /> Joined campaigns</label>
            <label className="line"><input type="checkbox" checked={!!view.watching} onChange={(e) => loadRows({ watching: e.target.checked })} /> Being watched</label>
            <label className="line">Game <select value={view.game ?? ""} onChange={(e) => loadRows({ game: e.target.value })}><option value="">Any</option>{(rows?.games ?? []).map((g: string) => <option key={g} value={g}>{g}</option>)}</select></label>
          </span>
        </div>
        {!rows ? <span className="muted">Loading…</span> : rows.rows.length === 0 ? <span className="muted">No Twitch streamers match. Add them in a project first, or clear the filters.</span> :
          rows.rows.map((s: any) => (
            <div key={s.id} className="stack tight" style={{ gap: 2, opacity: s.allowed ? 1 : 0.5 }}>
              <div className="row between" style={{ gap: 8 }}>
                <label className="row" style={{ gap: 8 }} title={s.why || ""}>
                  <input type="checkbox" checked={s.picked} disabled={!s.allowed} onChange={() => toggle(s.id)} />
                  <LiveDot st={s.status} /> <b>{s.display_name}</b>
                  {s.watching && <span className="pill ok">{st?.paused ? "Paused" : s.auto ? "Auto-pilot" : "Watching"}</span>}
                </label>
                <span className="muted small-text" style={{ textAlign: "right" }}>
                  {s.live ? `${(s.viewers ?? 0).toLocaleString()} viewers${s.status?.started_at ? ` · live ${since(s.status.started_at)}` : ""}${s.game ? ` · ${s.game}` : ""}` : "offline"}
                  {s.campaign ? ` · ${s.campaign_type === "pay_per_view" && s.rate ? `$${s.rate}/1K` : (s.campaign_type || "").replace("_", " ")}` : ""}
                  {s.projected_aud != null ? ` · A$${s.projected_aud}/day` : ""}
                </span>
              </div>
              <span className="muted small-text" style={{ paddingLeft: 26 }}>
                {s.allowed ? <>
                  {s.chat_per_min != null ? `${s.chat_per_min} msgs/min · ` : ""}{s.kept_today} kept today · {s.hit_rate != null ? `${s.hit_rate}% hit rate (${s.kept}/${s.triggers})` : "no triggers yet"} · chat delay {s.delay}s
                  {s.live && s.watching ? ` · buffer ${s.buffer_sec}s` : ""}
                  {" · "}<a href="#" onClick={(e) => { e.preventDefault(); setTagsFor(tagsFor === s.id ? null : s.id); }}>{tagsFor === s.id ? "close tags" : "chat tags"}</a></> : s.why}
              </span>
              {tagsFor === s.id && <div style={{ paddingLeft: 26 }}><ChatTags sid={s.id} /></div>}
            </div>))}
      </section>

      <section className="card stack tight">
        <h2 className="accent">Recent moments</h2>
        {(st?.recent ?? []).length === 0 ? <span className="muted">Nothing judged yet.</span> : st.recent.map((m: any) => (
          <span key={m.id} className="small-text">
            <span className={m.status === "kept" ? "ok-text" : m.status === "pending" ? "" : "muted"}>{m.status === "kept" ? "✓ kept" : m.status === "pending" ? "… judging" : "✗ dropped"}</span>
            {" "}<b>{m.display_name}</b> · {m.trigger.replace("_", " ")} · judge {m.judge_score ?? "–"}{m.final_score != null ? ` → ${m.final_score}` : ""} · <span className="muted">{m.reason}</span>
          </span>))}
      </section>
    </div>
  );
}
