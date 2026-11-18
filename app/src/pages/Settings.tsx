// Settings with tabs: General · Connections · Posting · Rules (mockup layout).
import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api } from "../api";
import { applyAccent } from "../App";
import Connections from "../components/Connections";
import Disclaimer from "../components/Disclaimer";

const TABS = ["General", "Connections", "Posting", "Rules"];
const ACCENTS = ["#ffd400", "#3ccf7a", "#4da3ff", "#ff5a7a"];

export default function Settings() {
  const [tab, setTab] = useState("General");
  const [s, setS] = useState<any>(null);
  const [sys, setSys] = useState<any>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.get("/settings").then(setS).catch((e) => setMsg(e.message));
    api.get("/system").then(setSys).catch(() => {});
  }, []);
  if (!s) return <p className="muted">{msg || "Loading…"}</p>;

  const set = (k: string, v: any) => { setMsg(""); setS({ ...s, [k]: v }); };
  const setIn = (k: string, sub: string, v: any) => set(k, { ...s[k], [sub]: v });
  const save = () => api.put("/settings", s).then(() => setMsg("Saved")).catch((e) => setMsg(e.message));
  const pickFolder = async () => { const d = await open({ directory: true }); if (typeof d === "string") set("media_dir", d); };
  // Plain function (not a component) so inputs keep focus while typing.
  const num = (k: string, sub?: string, step = 1, w = 70) => (
    <input type="number" step={step} style={{ width: w }} value={sub ? s[k][sub] : s[k]}
      onChange={(e) => (sub ? setIn(k, sub, +e.target.value) : set(k, +e.target.value))} />
  );

  return (
    <div className="stack">
      <header className="row between">
        <h1>Settings</h1>
        <div className="row">
          {msg && <span className={msg === "Saved" ? "ok-text" : "bad-text"}>{msg}</span>}
          {tab !== "Connections" && <button className="btn-primary" onClick={save}>Save settings</button>}
        </div>
      </header>
      <div className="row" style={{ gap: 8 }}>
        {TABS.map((t) => <button key={t} className={tab === t ? "tab on" : "tab"} onClick={() => setTab(t)}>{t}</button>)}
      </div>

      {tab === "General" && (
        <div className="cards2">
          <section className="card stack">
            <h2 className="accent">Storage</h2>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input aria-label="Media folder" className="grow" readOnly value={s.media_dir || ""} />
              <button onClick={pickFolder}>Change…</button>
            </div>
            <label className="line">Storage limit <span>{num("storage_cap_gb", undefined, 1, 70)} GB</span></label>
            <button className="check" style={{ background: "none", border: "none", padding: 0 }}
              onClick={() => set("delete_sources_after_processing", !s.delete_sources_after_processing)}>
              <span className={s.delete_sources_after_processing ? "tick on" : "tick"} aria-hidden="true">{s.delete_sources_after_processing ? "✓" : ""}</span>
              Delete source videos after processing</button>
          </section>

          <section className="card stack">
            <h2 className="accent">Rendering</h2>
            <label className="line">Encoder
              <select value={s.render.encoder} onChange={(e) => setIn("render", "encoder", e.target.value)}>
                <option value="auto">Auto (best available)</option>
                {sys?.encoders?.filter((e: any) => e.available).map((e: any) => <option key={e.codec} value={e.codec}>{e.label}</option>)}
              </select></label>
            <label className="line">Quality
              <select value={s.render.quality} onChange={(e) => setIn("render", "quality", e.target.value)}>
                <option value="high">High</option><option value="medium">Medium</option><option value="fast">Fast / smaller</option></select></label>
          </section>

          <section className="card stack">
            <h2 className="accent">Default clip settings</h2>
            <label className="line">Clip length (seconds) <span>{num("clip", "min_sec", 1, 60)} – {num("clip", "max_sec", 1, 60)}</span></label>
            <label className="line">Highlights per stream hour {num("clip", "top_n_per_hour", 1, 60)}</label>
            <label className="line">Lead-in / tail padding (s) <span>{num("clip", "pad_before", 0.5, 60)} / {num("clip", "pad_after", 0.5, 60)}</span></label>
            <label className="field">How picky (higher = fewer, better clips): {Math.round(s.clip.score_threshold * 100)}
              <input type="range" min={30} max={90} value={Math.round(s.clip.score_threshold * 100)} onChange={(e) => setIn("clip", "score_threshold", +e.target.value / 100)} /></label>
            <label className="line">Caption theme
              <select value={s.captions.style} onChange={(e) => setIn("captions", "style", e.target.value)}>
                <option value="bold_pop">Bold pop</option><option value="clean">Clean</option><option value="neon">Neon</option></select></label>
            <button className="check" style={{ background: "none", border: "none", padding: 0 }} onClick={() => setIn("transcription", "enabled", !s.transcription.enabled)}>
              <span className={s.transcription.enabled ? "tick on" : "tick"} aria-hidden="true">{s.transcription.enabled ? "✓" : ""}</span>Transcribe speech for captions</button>
            <label className="line">Speech model
              <select value={s.transcription.model} onChange={(e) => setIn("transcription", "model", e.target.value)}>
                <option value="base">Base (fastest)</option><option value="small">Small (balanced)</option><option value="medium">Medium (best, needs a GPU)</option></select></label>
          </section>

          <section className="card stack">
            <h2 className="accent">AI model (hooks and titles)</h2>
            <label className="line">Provider
              <select value={s.llm.provider} onChange={(e) => setIn("llm", "provider", e.target.value)}>
                <option value="ollama">Local (Ollama, free)</option><option value="cloud">Cloud API</option></select></label>
            {s.llm.provider === "ollama"
              ? <label className="line">Model <input style={{ width: 180 }} value={s.llm.model} onChange={(e) => setIn("llm", "model", e.target.value)} /></label>
              : <>
                  <label className="line">API base URL <input style={{ width: 240 }} value={s.llm.cloud_base_url} onChange={(e) => setIn("llm", "cloud_base_url", e.target.value)} /></label>
                  <label className="line">Model <input style={{ width: 180 }} value={s.llm.cloud_model} onChange={(e) => setIn("llm", "cloud_model", e.target.value)} /></label>
                  <span className="muted">Add the API key under Connections.</span>
                </>}
            <span className="muted">If no model is running, ClipFarm uses simple template hooks.</span>
          </section>

          <section className="card stack">
            <h2 className="accent">Appearance</h2>
            <div className="line">Accent colour
              <div className="row" style={{ gap: 8 }}>{ACCENTS.map((c) => (
                <button key={c} aria-label={`Accent ${c}`} onClick={() => { set("accent", c); applyAccent(c); }}
                  style={{ width: 30, height: 30, padding: 0, borderRadius: "50%", background: c, border: s.accent === c ? "3px solid #fff" : "1px solid #2b303b" }} />))}</div>
            </div>
          </section>
        </div>
      )}

      {tab === "Connections" && <Connections />}

      {tab === "Posting" && (
        <section className="card stack" style={{ maxWidth: 560 }}>
          <h2 className="accent">Posting schedule</h2>
          <label className="line">Posts per day per account {num("posting", "posts_per_day_per_account", 1, 60)}</label>
          <label className="line">Posting hours <span>{num("posting", "window_start_hour", 1, 55)} to {num("posting", "window_end_hour", 1, 55)}</span></label>
          <label className="line">Minimum minutes between posts {num("posting", "min_gap_minutes", 1, 60)}</label>
          <span className="muted">Auto-posting is coming in the next version.</span>
        </section>
      )}

      {tab === "Rules" && <Disclaimer />}
    </div>
  );
}
