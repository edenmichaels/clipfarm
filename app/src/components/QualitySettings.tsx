// Settings › Captions, Highlights and Music (v0.4).
import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api } from "../api";

const MOODS = ["funny", "hype", "tense", "epic", "chill", "sad"];
const SIGNAL_LABEL: Record<string, string> = {
  audio_events: "Sound events (laughs, explosions…)", chat: "Chat reactions", llm: "AI transcript rating",
  visual: "On-screen action", community: "Community clips",
};

export function CaptionSettings() {
  const [presets, setPresets] = useState<any[]>([]);
  const [cfg, setCfg] = useState<any>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.get("/captions/presets").then((r) => setPresets(r.presets)).catch(() => {});
    api.get("/settings").then((s) => setCfg(s.captions || {})).catch((e) => setMsg(e.message));
  }, []);
  if (!cfg) return <p className="muted">{msg || "Loading…"}</p>;
  const save = (patch: any) => {
    const next = { ...cfg, ...patch };
    setCfg(next);
    api.put("/settings", { captions: next }).then(() => setMsg("Saved")).catch((e) => setMsg(e.message));
  };
  const style = cfg.style || "bold_pop";
  return (
    <section className="card stack" style={{ maxWidth: 720 }}>
      <h2>Captions</h2>
      <span className="muted">The standard streamer-clip look: Montserrat, UPPERCASE, white with a thick black outline, 1–3 words at a time, the spoken word in colour. One fixed place at the bottom for every clip and layout, clear of the TikTok / Reels / Shorts buttons, and no animation.</span>
      <div className="field">Default style
        <div className="cards2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
          {presets.map((p) => (
            <button key={p.key} className={style === p.key ? "preset on" : "preset"} onClick={() => save({ style: p.key })}>
              <span style={{ fontFamily: `"CF ${p.font}", Impact, sans-serif`, color: p.text, WebkitTextStroke: `2px ${p.outline}`,
                fontSize: 22, fontWeight: 900 }}>{p.uppercase ? "NO " : "No "}<span style={{ color: p.highlight }}>{p.uppercase ? "WAY" : "way"}</span></span>
              <small>{p.label} · {p.font}</small>
            </button>))}
        </div>
      </div>
      <label className="line">Capital letters
        <select value={cfg.uppercase === true ? "on" : cfg.uppercase === false ? "off" : "preset"}
          onChange={(e) => save({ uppercase: e.target.value === "on" ? true : e.target.value === "off" ? false : null })}>
          <option value="preset">As the style does</option><option value="on">Always</option><option value="off">Never</option></select></label>
      <label className="line">Swear words
        <select value={cfg.profanity || "keep"} onChange={(e) => save({ profanity: e.target.value })}>
          <option value="keep">Keep</option><option value="mask">Mask (f***)</option><option value="bleep">Mask + bleep the audio</option></select></label>
      <label className="line">Filler words (um, uh)
        <select value={cfg.fillers || "remove"} onChange={(e) => save({ fillers: e.target.value })}>
          <option value="remove">Leave out of captions</option><option value="keep">Keep</option></select></label>
      <label className="line">Emoji on keywords (😂 🔥 💀)
        <select value={cfg.emoji ? "on" : "off"} onChange={(e) => save({ emoji: e.target.value === "on" })}>
          <option value="off">Off</option><option value="on">On</option></select></label>
      <label className="line">Your fonts folder (optional)
        <input placeholder="e.g. C:\\Fonts\\ClipFarm" defaultValue={cfg.fonts_dir || ""} onBlur={(e) => save({ fonts_dir: e.target.value.trim() || null })} /></label>
      <span className="muted small-text">Montserrat is bundled (SIL Open Font License). To use Komika Axis, The Bold Font, Bebas Neue or others, put font files you have a licence for in this folder; they then show up in the caption font list. ClipFarm never bundles a font without a confirmed licence.</span>
      {msg && <span className={msg === "Saved" ? "ok-text" : "bad-text"}>{msg}</span>}
      <Vocabulary />
    </section>
  );
}

function Vocabulary() {
  const [v, setV] = useState<any>(null);
  const [terms, setTerms] = useState("");
  const [fixes, setFixes] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.get("/vocabulary").then((r) => {
      setV(r); setTerms(r.terms.join("\n")); setFixes(r.fixes.map((f: any) => `${f.from} -> ${f.to}`).join("\n"));
    }).catch((e) => setMsg(e.message));
  }, []);
  if (!v) return <p className="muted">{msg || "Loading…"}</p>;
  const save = () => api.put("/vocabulary", {
    terms: terms.split("\n").map((x) => x.trim()).filter(Boolean),
    fixes: fixes.split("\n").map((x) => x.split("->")).filter((p) => p.length === 2).map(([a, b]) => ({ from: a.trim(), to: b.trim() })),
  }).then((r) => { setV(r); setMsg(`Saved ${r.terms.length} terms and ${r.fixes.length} fixes. Captions use them from the next preview or export.`); })
    .catch((e) => setMsg(e.message));
  return (
    <div className="stack tight">
      <h3>Vocabulary</h3>
      <span className="muted small-text">Names, game terms and slang spelled your way. Whisper gets them as a hint, and captions are corrected with them (close misspellings too). It started with your streamers' names and common GTA 6 terms.</span>
      <div className="grid2">
        <label className="field">Terms (one per line)<textarea rows={8} value={terms} onChange={(e) => setTerms(e.target.value)} /></label>
        <label className="field">Fixes ("wrong -&gt; right", one per line)<textarea rows={8} value={fixes} placeholder={"vice sitty -> Vice City\nkreek craft -> KreekCraft"} onChange={(e) => setFixes(e.target.value)} /></label>
      </div>
      <div className="row" style={{ gap: 8 }}><button onClick={save}>Save vocabulary</button>{msg && <span className="muted small-text">{msg}</span>}</div>
    </div>
  );
}

export function HighlightSettings() {
  const [w, setW] = useState<any>(null);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState("");
  const load = () => api.get("/highlights/weights").then((r) => { setW(r); setDraft(r.global); }).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  if (!w) return <p className="muted">{msg || "Loading…"}</p>;
  const put = (body: any) => api.put("/highlights/weights", body).then((r) => { setW(r); setDraft(r.global); setMsg("Saved"); })
    .catch((e) => setMsg(e.message));
  const total = Object.values(draft).reduce((a, b) => a + b, 0) || 1;
  return (
    <div className="stack" style={{ maxWidth: 760 }}>
      <section className="card stack">
        <h2>What makes a highlight</h2>
        <label className="line">Moment focus
          <span className="row" style={{ gap: 6 }}>{[["balanced", "Balanced"], ["funny", "Funny first"], ["action", "Action first"]].map(([k, l]) => (
            <button key={k} className={w.focus === k ? "pill-btn on" : "pill-btn"} onClick={() => put({ focus: k })}>{l}</button>))}</span>
        </label>
        <span className="muted small-text">Menus, loading screens, maps, BRB screens, calm chatting and music on its own are scored down. Loudness alone never makes a highlight.</span>
        <div className="weights">{Object.keys(w.default).map((k) => (
          <FragmentRow key={k} label={SIGNAL_LABEL[k] || k} value={draft[k] ?? 0} pct={(draft[k] ?? 0) / total}
            onChange={(v) => setDraft({ ...draft, [k]: v })} />))}
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button onClick={() => put({ weights: draft })}>Save weights</button>
          <button className="btn-quiet" onClick={() => api.post("/highlights/weights/reset", {}).then(load)}>Reset to default</button>
          {msg && <span className="muted">{msg}</span>}
        </div>
      </section>
      <section className="card stack tight">
        <h2>Learned from your ratings</h2>
        <span className="muted">{w.ratings} clips rated. After about {w.min_ratings} for a streamer or game, ClipFarm reweights the signals for them from your 👍/👎 and real view counts.</span>
        {Object.keys(w.learned).length === 0 ? <span className="muted small-text">Nothing learned yet.</span> :
          Object.entries(w.learned).map(([scope, v]: any) => (
            <div key={scope} className="row between">
              <span><b>{w.names[scope]}</b> <span className="muted small-text">({v.ratings} ratings)</span>: {Object.entries(v.weights).map(([k, x]: any) => `${(SIGNAL_LABEL[k] || k).split(" (")[0]} ${Math.round(x * 100)}%`).join(" · ")}</span>
              <button className="btn-quiet" onClick={() => api.post("/highlights/weights/reset", { scope }).then(load)}>Reset to default</button>
            </div>))}
      </section>
      <TrendTopics />
    </div>
  );
}

/** Topics you're watching (game updates, memes, events). Clips that mention them score higher for relevance. */
function TrendTopics() {
  const [topics, setTopics] = useState<any[]>([]);
  const [text, setText] = useState("");
  const load = () => api.get("/trends").then(setTopics).catch(() => {});
  useEffect(() => { load(); }, []);
  return (
    <section className="card stack tight">
      <h2>Trending topics</h2>
      <span className="muted small-text">Things people are talking about right now (a new map, a meme, an event). Clips that mention them get a higher virality score. One per line.</span>
      <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder={"bank heist\nnew Vice City map"} />
      <div><button onClick={() => api.post("/trends", { text }).then(() => { setText(""); load(); })} disabled={!text.trim()}>Add</button></div>
      <div className="row wrap" style={{ gap: 6 }}>{topics.map((t) => (
        <span key={t.id} className="pill">{t.topic} <button className="btn-quiet" aria-label={`Remove ${t.topic}`} onClick={() => api.del(`/trends/${t.id}`).then(load)}>✕</button></span>))}</div>
    </section>
  );
}

function FragmentRow({ label, value, pct, onChange }: { label: string; value: number; pct: number; onChange: (v: number) => void }) {
  return <>
    <span>{label}</span>
    <input type="range" min={0} max={0.6} step={0.01} value={value} onChange={(e) => onChange(+e.target.value)} aria-label={label} />
    <span className="muted">{Math.round(pct * 100)}%</span>
  </>;
}

export function MusicSettings() {
  const [data, setData] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");
  const [trend, setTrend] = useState<any[]>([]);
  const [paste, setPaste] = useState("");
  const load = () => Promise.all([api.get("/music/tracks").then(setData), api.get("/trending").then(setTrend)]).catch((e) => setMsg(e.message));
  useEffect(() => { setBusy("Preparing the music library…"); load().finally(() => setBusy("")); }, []);
  if (!data) return <p className="muted">{busy || msg || "Loading…"}</p>;
  const s = data.settings || {};
  const setS = (patch: any) => api.put("/music/settings", patch).then(load).catch((e) => setMsg(e.message));
  const scan = async () => {
    const folder = await open({ directory: true });
    if (typeof folder !== "string") return;
    setBusy("Tagging your tracks…");
    try { const r = await api.post("/music/scan", { folder }); setMsg(`Added ${r.added} track(s).${r.failed.length ? ` ${r.failed.length} couldn't be read.` : ""}`); await load(); }
    catch (e: any) { setMsg(e.message); }
    setBusy("");
  };
  const edit = (id: number, patch: any) => api.patch(`/music/tracks/${id}`, patch).then(load);
  return (
    <div className="stack" style={{ maxWidth: 900 }}>
      <section className="card stack">
        <h2>Music</h2>
        <span className="muted">Only music ClipFarm is allowed to use goes in your videos, each track with its licence recorded. Picked to match the moment (funny → playful, action → hype, fails → ironic), dropped on the payoff, and turned down while anyone talks. Campaigns that say "original audio only" never get music.</span>
        <label className="line">Add music to clips
          <select value={s.enabled === false ? "off" : "on"} onChange={(e) => setS({ enabled: e.target.value === "on" })}>
            <option value="on">Yes</option><option value="off">No, original audio only</option></select></label>
        <label className="line">Turn music down under speech by
          <span className="row" style={{ gap: 8 }}><input type="range" min={12} max={18} step={1} value={s.duck_db ?? 15}
            onChange={(e) => setS({ duck_db: +e.target.value })} aria-label="Ducking" /><span className="muted">{s.duck_db ?? 15} dB</span></span></label>
        <div className="row between">
          <span className="muted small-text">Your own folder: {s.folder || "not set"}</span>
          <button onClick={scan} disabled={!!busy}>{busy || "Add my music folder"}</button>
        </div>
        <span className="warn-text small-text">⚠ {data.local_warning}</span>
        {msg && <span className="muted">{msg}</span>}
      </section>
      <section className="card stack tight">
        <h3>Library ({data.tracks.length})</h3>
        {data.tracks.map((t: any) => (
          <div key={t.id} className="track-row">
            <span><b>{t.title}</b> <span className="muted small-text">{t.source === "bundled" ? "ClipFarm Originals" : t.source} · {t.licence}</span></span>
            <span className="row" style={{ gap: 4 }}>{MOODS.map((m) => (
              <button key={m} className={t.moods.includes(m) ? "chip on" : "chip"}
                onClick={() => edit(t.id, { moods: t.moods.includes(m) ? t.moods.filter((x: string) => x !== m) : [...t.moods, m] })}>{m}</button>))}</span>
            <span className="muted small-text">{t.bpm ? `${Math.round(t.bpm)} BPM` : ""}</span>
            <button className="btn-quiet" onClick={() => edit(t.id, { enabled: t.enabled ? 0 : 1 })}>{t.enabled ? "Disable" : "Enable"}</button>
          </div>))}
      </section>
      <section className="card stack tight">
        <h3>Trending sounds</h3>
        <span className="muted small-text">Names only. ClipFarm never puts a trending song in your video. It suggests one in the Editor (Music & Audio) and Post & Submit, and you add it inside TikTok/Instagram when you post (use "Export for trending sound"). Paste one per line: <code>Name - Artist | funny, hype | tiktok</code></span>
        <textarea rows={3} value={paste} onChange={(e) => setPaste(e.target.value)} placeholder="Sound name - Artist | funny | tiktok" />
        <div><button disabled={!paste.trim()} onClick={() => api.post("/trending/import", { text: paste }).then((r) => { setMsg(`${r.added} added, ${r.updated} updated`); setPaste(""); load(); }).catch((e) => setMsg(e.message))}>Add to list</button></div>
        {trend.filter((x) => x.active).map((x) => (
          <div key={x.id} className="row between"><span>♪ {x.name}{x.artist ? ` – ${x.artist}` : ""} <span className="muted small-text">{x.platform} · {x.moods.join(", ") || "any mood"}</span></span>
            <button className="btn-quiet" onClick={() => api.del(`/trending/${x.id}`).then(load)}>Remove</button></div>))}
      </section>
    </div>
  );
}
