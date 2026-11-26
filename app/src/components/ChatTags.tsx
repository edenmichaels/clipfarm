// Per-streamer chat tags (v0.8.9): the words/emotes that count as "clip it", laughing and shock in this
// streamer's chat, how big a spike has to be, and the learned chat delay.
import { useEffect, useState } from "react";
import { api } from "../api";

const GROUPS: [string, string][] = [["clip", "Clip requests (count most)"], ["laugh", "Laughing"], ["shock", "Shock / hype"]];

export default function ChatTags({ sid }: { sid: number }) {
  const [c, setC] = useState<any>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get(`/live/streamers/${sid}/chat`).then(setC).catch((e) => setMsg(e.message)); }, [sid]);
  if (!c) return <span className="muted small-text">{msg || "Loading…"}</span>;
  const put = async (body: any) => { try { setC(await api.put(`/live/streamers/${sid}/chat`, body)); setMsg("Saved"); } catch (e: any) { setMsg(e.message); } };
  return (
    <div className="stack tight">
      {GROUPS.map(([g, label]) => (
        <label key={g} className="stack tight" style={{ gap: 2 }}>
          <span className="small-text">{label}</span>
          <input defaultValue={c.tags[g].join(", ")} onBlur={(e) => put({ tags: { ...c.tags, [g]: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) } })} />
        </label>))}
      <span className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        <label className="line">Spike = baseline × <input type="number" step={0.5} min={1.5} max={10} style={{ width: 64 }} defaultValue={c.trigger.multiplier}
          onBlur={(e) => put({ trigger: { ...c.trigger, multiplier: +e.target.value } })} /></label>
        <label className="line">and at least <input type="number" min={2} max={100} style={{ width: 64 }} defaultValue={c.trigger.min_unique}
          onBlur={(e) => put({ trigger: { ...c.trigger, min_unique: +e.target.value } })} /> different people</label>
        <label className="line">Chat delay <input type="number" min={5} max={45} style={{ width: 64 }} defaultValue={c.delay.delay}
          onBlur={(e) => put({ delay: +e.target.value })} /> s <span className="muted small-text">{c.delay.manual ? "(set by you)" : c.delay.samples ? `(learned from ${c.delay.samples} viewer clip${c.delay.samples === 1 ? "" : "s"})` : "(default; learns from viewer clips)"}</span></label>
      </span>
      <span className="row" style={{ gap: 8 }}>
        <button className="btn-ghost" onClick={() => put({ tags: c.defaults })}>Reset tags</button>
        {msg && <span className="muted small-text">{msg}</span>}
      </span>
    </div>
  );
}

// Settings page: pick a streamer, edit its tags.
export function ChatTagSettings() {
  const [rows, setRows] = useState<any[]>([]);
  const [sid, setSid] = useState<number | null>(null);
  useEffect(() => { api.get("/live/streamers").then((r) => { setRows(r.rows); if (r.rows[0]) setSid(r.rows[0].id); }).catch(() => {}); }, []);
  return (
    <section className="card stack">
      <h2 className="accent">Chat tags (Live Watcher)</h2>
      <span className="muted">What counts as a reaction in each streamer's chat. Add their channel emotes and inside jokes. Each person counts once.</span>
      {rows.length === 0 ? <span className="muted">No Twitch streamers yet.</span> : <>
        <select value={sid ?? ""} onChange={(e) => setSid(+e.target.value)}>{rows.map((r) => <option key={r.id} value={r.id}>{r.display_name}</option>)}</select>
        {sid != null && <ChatTags key={sid} sid={sid} />}</>}
    </section>
  );
}
