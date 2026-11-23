// Trending Styles: pick a whole-video look, tweak every part, save it, set defaults; plus your own
// style references (screenshots + notes) and "Make a style from this".
import { useEffect, useRef, useState } from "react";
import { api } from "../api";

type Style = any;

const LAYOUT_LABEL: Record<string, string> = { split: "Facecam on top + gameplay", full_reaction: "Streamer full screen",
  gameplay: "Gameplay only", auto: "Keep the AI reframe" };
const POS_LABEL: Record<string, string> = { seam: "On the seam", upper: "Upper third", centre: "Centre", lower: "Lower third" };
const BOX_LABEL: Record<string, string> = { white_box: "White box", black_box: "Black box", outline: "Outline only", none: "No title" };

function ago(iso?: string | null) {
  if (!iso) return "never";
  const d = (Date.now() - new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime()) / 86400000;
  return d < 1 ? "today" : d < 2 ? "yesterday" : `${Math.floor(d)} days ago`;
}

// A small schematic of the style (not a render): where facecam, captions and title go.
export function StyleThumb({ s, small }: { s: Style; small?: boolean }) {
  const w = small ? 54 : 72, h = w * 16 / 9;
  const split = s.layout.mode === "split" || (s.layout.mode === "auto" && s.key !== "clean");
  const camH = split ? h * s.layout.cam_ratio : 0;
  const capY = { seam: split ? camH + h * 0.04 : h * 0.64, upper: split ? camH + h * 0.14 : h * 0.3, centre: h * 0.5, lower: h * 0.66 }[s.captions.position as string] ?? h * 0.6;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden className="style-thumb">
      <rect width={w} height={h} rx={4} fill={s.layout.mode === "full_reaction" ? "#5a4a44" : "#3d4450"} />
      {split && <rect width={w} height={camH} rx={4} fill="#2a3242" />}
      {split && s.layout.seam_line && <rect y={camH - 1} width={w} height={2} fill="#000" />}
      {s.title.box !== "none" && <rect x={w * 0.14} y={h * 0.09} width={w * 0.72} height={h * 0.06} rx={1}
        fill={s.title.box === "white_box" ? "#fff" : s.title.box === "black_box" ? "#000" : "transparent"}
        stroke={s.title.box === "outline" ? "#fff" : "none"} strokeWidth={1} />}
      <rect x={w * 0.25} y={capY - h * 0.025} width={w * 0.2} height={h * 0.05} rx={1} fill="#fff" />
      <rect x={w * 0.5} y={capY - h * 0.025} width={w * 0.24} height={h * 0.05} rx={1}
        fill={s.captions.highlight ? "#" + s.captions.highlight_color : "#fff"} />
    </svg>
  );
}

function StyleEditor({ s, onChange }: { s: Style; onChange: (s: Style) => void }) {
  const set = (grp: string, k: string, v: any) => onChange({ ...s, [grp]: { ...s[grp], [k]: v } });
  return (
    <div className="stack tight style-editor">
      <h3>Layout</h3>
      <label className="field">Layout <select value={s.layout.mode} onChange={(e) => set("layout", "mode", e.target.value)}>
        {Object.entries(LAYOUT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label className="field">Facecam height: {Math.round(s.layout.cam_ratio * 100)}% of the video
        <input type="range" min={0.35} max={0.45} step={0.01} value={s.layout.cam_ratio}
          onChange={(e) => set("layout", "cam_ratio", +e.target.value)} /></label>
      <label className="row"><input type="checkbox" checked={s.layout.seam_line} onChange={(e) => set("layout", "seam_line", e.target.checked)} /> Thin line on the seam</label>
      <h3>Captions</h3>
      <label className="field">Position <select value={s.captions.position} onChange={(e) => set("captions", "position", e.target.value)}>
        {Object.entries(POS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label className="field">Words on screen <select value={s.captions.words} onChange={(e) => set("captions", "words", +e.target.value)}>
        {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
      <label className="row"><input type="checkbox" checked={s.captions.highlight} onChange={(e) => set("captions", "highlight", e.target.checked)} /> Highlight the spoken word</label>
      {s.captions.highlight && <label className="field">Highlight colour <input type="color" value={"#" + s.captions.highlight_color}
        onChange={(e) => set("captions", "highlight_color", e.target.value.slice(1).toUpperCase())} /></label>}
      <label className="row"><input type="checkbox" checked={s.captions.uppercase} onChange={(e) => set("captions", "uppercase", e.target.checked)} /> UPPERCASE</label>
      <h3>Title</h3>
      <label className="field">Look <select value={s.title.box} onChange={(e) => set("title", "box", e.target.value)}>
        {Object.entries(BOX_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label className="row"><input type="checkbox" checked={s.title.whole} onChange={(e) => set("title", "whole", e.target.checked)} /> Show for the whole clip</label>
      {!s.title.whole && <label className="field">Show for {s.title.seconds}s
        <input type="range" min={1} max={8} step={0.5} value={s.title.seconds} onChange={(e) => set("title", "seconds", +e.target.value)} /></label>}
      <label className="field">Where <select value={s.title.position} onChange={(e) => set("title", "position", e.target.value)}>
        <option value="top">Top</option><option value="seam">On the seam</option></select></label>
      <h3>Pacing &amp; effects</h3>
      <label className="field">Zoom strength {Math.round(s.pacing.zoom * 100)}%
        <input type="range" min={0} max={1} step={0.05} value={s.pacing.zoom} onChange={(e) => set("pacing", "zoom", +e.target.value)} /></label>
      <label className="row"><input type="checkbox" checked={s.pacing.cut_silence} onChange={(e) => set("pacing", "cut_silence", e.target.checked)} /> Cut pauses longer than {s.pacing.max_gap}s (never the payoff)</label>
      {(["flash", "shake", "sfx", "emoji"] as const).map((k) => (
        <label key={k} className="row"><input type="checkbox" checked={s.effects[k]} onChange={(e) => set("effects", k, e.target.checked)} />
          {{ flash: "Flash on the payoff", shake: "Shake on the payoff", sfx: "Sound effects", emoji: "Reaction emojis" }[k]}</label>))}
      <label className="row"><input type="checkbox" checked={s.audio.music} onChange={(e) => set("audio", "music", e.target.checked)} /> Music (licensed tracks only)</label>
      {s.audio.music && <label className="field">Music level {s.audio.music_db > 0 ? "+" : ""}{s.audio.music_db} dB
        <input type="range" min={-12} max={6} step={1} value={s.audio.music_db} onChange={(e) => set("audio", "music_db", +e.target.value)} /></label>}
    </div>
  );
}

/** Pick / tweak / save styles. With `cid`, "Apply" restyles that clip (a new undoable version). */
export function StylesPanel({ cid, current, onApplied, compact }: { cid?: number; current?: string; onApplied?: (v: any) => void; compact?: boolean }) {
  const [d, setD] = useState<any>(null);
  const [sel, setSel] = useState<Style | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.get("/styles").then(setD).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  if (!d) return <p className="muted">{msg || "Loading styles…"}</p>;
  const apply = (body: any, label: string) => {
    if (!cid) return;
    setBusy(true);
    api.post(`/edit/${cid}/style`, body).then((v) => { onApplied?.(v); setMsg(`${label} applied (undo with Ctrl+Z).`); })
      .catch((e) => setMsg(e.message)).finally(() => setBusy(false));
  };
  const save = () => {
    if (!sel) return;
    const name = prompt("Save this style as", sel.builtin ? `${sel.name} (mine)` : sel.name);
    if (!name) return;
    api.post("/styles", { name, style: sel, id: sel.builtin ? undefined : sel.id }).then((s) => { setSel(s); load(); setMsg(`Saved "${s.name}".`); })
      .catch((e) => setMsg(e.message));
  };
  return (
    <div className="stack tight styles-panel">
      <div className="row between"><b>Trending Styles</b>
        <span className="muted small-text" title="When you last added a style reference or saved a style">Style last updated: {ago(d.last_updated)}</span></div>
      <div className={compact ? "style-grid compact" : "style-grid"}>
        {d.styles.map((s: Style) => (
          <button key={s.key} className={"style-card" + (sel?.key === s.key ? " on" : "") + (current === s.key ? " current" : "")}
            onClick={() => setSel(JSON.parse(JSON.stringify(s)))} title={s.description}>
            <StyleThumb s={s} small={compact} />
            <span className="style-name">{s.name}{current === s.key && <span className="muted"> · in use</span>}</span>
            {!compact && <span className="muted small-text">{s.description}</span>}
          </button>))}
      </div>
      {sel && (
        <div className="card stack tight">
          <div className="row between"><b>{sel.name}</b>
            <span className="row" style={{ gap: 6 }}>
              {cid && <button className="btn" disabled={busy} onClick={() => apply({ style: sel }, sel.name)}>Apply to this clip</button>}
              <button className="btn-quiet" onClick={save}>Save as my style</button>
              {!sel.builtin && sel.id && <button className="btn-quiet" onClick={() => api.del(`/styles/${sel.id}`).then(() => { setSel(null); load(); })}>Delete</button>}
            </span></div>
          <DefaultPicker styleKey={sel.key} defaults={d.defaults} streamers={d.streamers || []} onDone={load} />
          <StyleEditor s={sel} onChange={setSel} />
        </div>)}
      {msg && <span className="muted small-text" role="status">{msg}</span>}
    </div>
  );
}

function DefaultPicker({ styleKey, defaults, streamers, onDone }: { styleKey: string; defaults: Record<string, string>; streamers: any[]; onDone: () => void }) {
  const [scope, setScope] = useState("global");
  const [opts, setOpts] = useState<{ v: string; l: string }[]>([{ v: "global", l: "Everything (global default)" }]);
  useEffect(() => {
    Promise.all([api.get("/accounts").catch(() => []), api.get("/programs").catch(() => [])])
      .then(([acc, prog]: any[]) => setOpts([{ v: "global", l: "Everything (global default)" },
        ...streamers.map((x: any) => ({ v: `streamer:${x.id}`, l: `Streamer: ${x.name}` })),
        ...((acc.accounts || acc || []) as any[]).map((x: any) => ({ v: `account:${x.id}`, l: `Account: ${x.handle || x.platform}` })),
        ...((prog.programs || prog || []) as any[]).map((x: any) => ({ v: `program:${x.id}`, l: `Campaign: ${x.name}` }))]));
  }, [streamers.length]);
  const usedBy = Object.entries(defaults).filter(([, k]) => k === styleKey).map(([s]) => opts.find((o) => o.v === s)?.l || s);
  return (
    <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
      <span className="small-text">Make default for</span>
      <select value={scope} onChange={(e) => setScope(e.target.value)}>{opts.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}</select>
      <button className="btn-quiet" onClick={() => api.put("/styles/defaults", { scope, key: styleKey }).then(onDone)}>Set</button>
      {usedBy.length > 0 && <span className="muted small-text">Default for: {usedBy.join(", ")}</span>}
    </div>
  );
}

/** Your own screenshots of clips that work, with notes and tags. Nothing is scraped. */
export function StyleReferences({ onStyle }: { onStyle?: () => void }) {
  const [d, setD] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [made, setMade] = useState<any>(null);
  const file = useRef<HTMLInputElement>(null);
  const load = () => api.get("/style-refs").then(setD).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  const add = (f: File) => {
    const rd = new FileReader();
    rd.onload = () => {
      const data = String(rd.result).split(",")[1];
      const notes = prompt("Notes for this reference (what you like about it)", "") ?? "";
      const tags = (prompt("Tags, comma separated (e.g. split, fail, rage)", "") ?? "").split(",").map((t) => t.trim()).filter(Boolean);
      api.post("/style-refs", { data, notes, tags, source_note: f.name }).then(() => { load(); setMsg("Reference added."); })
        .catch((e) => setMsg(e.message));
    };
    rd.readAsDataURL(f);
  };
  const onPaste = (e: any) => {
    const it = Array.from(e.clipboardData?.items || []).find((i: any) => i.type.startsWith("image/")) as any;
    const f: File | null = it?.getAsFile() ?? null;
    if (f) add(f);
  };
  if (!d) return <p className="muted">{msg || "Loading…"}</p>;
  return (
    <section className="card stack tight" onPaste={onPaste} tabIndex={0}>
      <div className="row between"><h2>Style references</h2>
        <button className="btn" onClick={() => file.current?.click()}>Add screenshot</button></div>
      <span className="muted small-text">Your own screenshots of clips that are doing well, with notes. Paste (Ctrl+V), drop or pick a file.
        ClipFarm never downloads or scrapes them. Tags you add here also teach the moment finder what you like.</span>
      <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => e.target.files?.[0] && add(e.target.files[0])} />
      <div className="ref-grid" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) add(f); }}>
        {d.refs.map((r: any) => (
          <figure key={r.id} className="ref-card">
            <img src={api.styleRefUrl(r.id)} alt={r.notes || "Style reference"} loading="lazy" />
            <figcaption className="stack tight">
              <span className="small-text">{r.notes || <span className="muted">No notes</span>}</span>
              <span className="muted small-text">{(r.tags || []).map((t: string) => `#${t}`).join(" ")} · added {(r.created_at || "").slice(0, 10)}</span>
              <span className="row" style={{ gap: 6 }}>
                <button className="btn-quiet" onClick={() => api.post(`/style-refs/${r.id}/make-style`, { save: true }).then((m) => { setMade(m); load(); onStyle?.(); })
                  .catch((e) => setMsg(e.message))}>Make a style from this</button>
                <button className="btn-quiet" onClick={() => api.del(`/style-refs/${r.id}`).then(load)}>Remove</button>
              </span>
            </figcaption>
          </figure>))}
        {!d.refs.length && <p className="muted">No references yet. Add a screenshot of a clip whose look you want to copy.</p>}
      </div>
      {made && <div className="card stack tight"><b>Made “{made.saved?.name}” ({made.method})</b>
        <span className="small-text">Found: {made.found.layout === "split" ? `split, facecam ${Math.round((made.found.seam_y || 0) * 100)}%` : made.found.layout};
          captions {POS_LABEL[made.found.caption_position] || "not found"}, {made.found.words || "?"} word(s){made.found.highlight_color ? `, highlight #${made.found.highlight_color}` : ", no highlight"};
          title {BOX_LABEL[made.found.title_box] || "not found"}. Tweak it in Styles.</span></div>}
      {msg && <span className="muted small-text" role="status">{msg}</span>}
    </section>
  );
}
