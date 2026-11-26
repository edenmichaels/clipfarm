// Editor building blocks: live 9:16 preview with caption overlay + safe zones, the multi-track timeline,
// and the right-hand panels (Transcript, Captions, Layout, Hook & Title, Music & Audio, Effects, Campaign rules).
import { useEffect, useRef, useState } from "react";
import { api, waitForJob } from "../api";

export type View = any;
export type Edl = any;

// ---- time mapping (source <-> output), same rules as the engine's timeline.py ----------------------------
// Segments come from the engine: {item, a, b, speed, hold, out_a, out_b}. Item 0 is the main clip; holds are
// freeze frames; speed != 1 is a speed ramp; items 1.. are appended clips.
export type Seg = { item: number; a: number; b: number; speed: number; hold: number; out_a: number; out_b: number };
export function keptOf(v: View): Seg[] {
  if (v?.segments) return v.segments as Seg[];
  let acc = 0;      // older engine: plain kept ranges
  return ((v?.kept || []) as [number, number][]).map(([a, b]) => { const s = { item: 0, a, b, speed: 1, hold: 0, out_a: acc, out_b: acc + b - a }; acc += b - a; return s; });
}
export function toOut(segs: Seg[], src: number, item = 0): number | null {
  for (const s of segs) {
    if (s.item === item && !s.hold && s.a <= src && src <= s.b) return s.out_a + (src - s.a) / s.speed;
  }
  return null;
}
export function toSrc(segs: Seg[], out: number): number {
  let last = 0;
  for (const s of segs) {
    if (s.item !== 0) continue;
    if (out <= s.out_b) return s.hold ? s.a : s.a + Math.max(0, out - s.out_a) * s.speed;
    last = s.b;
  }
  return last;
}
/** The segment under an output time. */
export function segAt(segs: Seg[], out: number): Seg | null {
  return segs.find((s) => out >= s.out_a && out < s.out_b) || segs[segs.length - 1] || null;
}
export const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
// Reaction emojis we bundle images for (Noto Emoji, Apache 2.0).
const REACTION_EMOJI = ["😂", "💥", "😱", "🏆", "💀", "❤️", "🔥", "😭", "🤯", "💰", "🚨", "👀"];
const hex = (c?: string) => (!c ? "#fff" : c.startsWith("#") ? c : `#${c}`);

// ---- fonts ---------------------------------------------------------------------------------------------
let fontsDone = false;
export function useCaptionFonts() {
  const [fonts, setFonts] = useState<any[]>([]);
  useEffect(() => {
    api.get("/caption-templates").then((r) => {
      setFonts(r.fonts || []);
      if (fontsDone) return;
      fontsDone = true;
      const el = document.createElement("style");
      el.textContent = (r.fonts || []).map((f: any) => `@font-face{font-family:"CF ${f.family}";src:url("${api.fontUrl(f.file)}")}`).join("");
      document.head.appendChild(el);
    }).catch(() => {});
  }, []);
  return fonts;
}

// ---- safe zones (1080x1920 pixels where each app's buttons and text sit) ------------------------------
export const SAFE: Record<string, { label: string; top: number; bottom: number; right: number; left: number }> = {
  tiktok: { label: "TikTok", top: 210, bottom: 1420, left: 60, right: 940 },
  reels: { label: "Reels", top: 220, bottom: 1480, left: 60, right: 950 },
  shorts: { label: "Shorts", top: 180, bottom: 1500, left: 60, right: 960 },
};

export function Preview({ v, videoRef, t, setT, zone, building, overlay }: {
  v: View; videoRef: { current: HTMLVideoElement | null }; t: number; setT: (x: number) => void;
  zone: string | null; building: boolean; overlay?: (scale: number) => any;
}) {
  // Everything you see here is drawn by the same renderer as the export (FFmpeg + libass):
  // - the playable preview is a low-res render of the current version;
  // - while that's being rebuilt after an edit, a still of the current frame is shown instead.
  // Guides (safe zones) are drawn on top in the page only and are never part of a render.
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(300);
  const [still, setStill] = useState(0);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const s = w / 1080;
  const live = !!v?.proxy;
  // stills: refresh at most every 250 ms while scrubbing
  useEffect(() => { if (live) return; const id = setTimeout(() => setStill(Math.round(t * 4) / 4), 250); return () => clearTimeout(id); }, [t, live]);
  const z = zone ? SAFE[zone] : null;
  return (
    <div className="ed-preview" ref={wrap}>
      {live ? (
        <video ref={videoRef} src={api.mediaUrl(v.proxy)} playsInline onTimeUpdate={(e) => setT((e.target as HTMLVideoElement).currentTime)}
          onClick={(e) => { const el = e.target as HTMLVideoElement; el.paused ? el.play() : el.pause(); }} />
      ) : (
        <img className="ed-still" alt="Preview frame" src={api.frameUrl(v.clip_id, still, v.head)} />
      )}
      {z && (
        <div className="ed-guide" aria-hidden title="Guide only: never part of the video">
          <div className="ed-guide-box" style={{ top: z.top * s, left: z.left * s, width: (z.right - z.left) * s, height: (z.bottom - z.top) * s }} />
          <span>{z.label} safe area (guide only)</span>
        </div>
      )}
      {overlay?.(s)}
      {!live && <span className="ed-badge">{building ? "Updating preview…" : "Still frame"}</span>}
      <span className="ed-time">{fmt(t)} / {fmt(v?.duration || 0)}</span>
    </div>
  );
}

// ---- panels --------------------------------------------------------------------------------------------
export function TranscriptPanel({ v, seek, op, t }: { v: View; seek: (x: number) => void; op: (b: any) => void; t: number }) {
  const [fix, setFix] = useState<{ i: number; text: string } | null>(null);
  const [pick, setPick] = useState<number[]>([]);
  const words = v.words_out || [];
  const fillers = new Set<number>(v.fillers || []);
  return (
    <div className="stack tight">
      <div className="row wrap" style={{ gap: 6 }}>
        <button onClick={() => op({ op: "fillers" })} disabled={!fillers.size}>Remove filler words ({fillers.size})</button>
        <button onClick={() => op({ op: "silences" })} disabled={!(v.silences || []).length}>Remove silences ({(v.silences || []).length})</button>
        {pick.length > 0 && <button className="btn-danger" onClick={() => { op({ op: "delete", indices: pick }); setPick([]); }}>Cut {pick.length} word(s)</button>}
      </div>
      <span className="muted tiny">Click a word to jump to it · Shift-click to select words to cut · double-click to fix a word.
        Use the handles at the ends of the timeline to change where the clip starts and ends.</span>
      <div className="ed-words">
        {words.map((w: any) => {
          const now = w.out != null && t >= w.out && t < w.out + (w.end - w.start);
          const cls = ["w", w.deleted ? "del" : "", fillers.has(w.i) ? "filler" : "", now ? "now" : "", pick.includes(w.i) ? "pick" : ""].join(" ");
          if (fix && fix.i === w.i) {
            return <input key={w.i} autoFocus className="w-fix" value={fix.text} onChange={(e) => setFix({ ...fix, text: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") { op({ op: "fix", index: w.i, text: fix.text }); setFix(null); } if (e.key === "Escape") setFix(null); }}
              onBlur={() => setFix(null)} />;
          }
          return (
            <span key={w.i} className={cls} title={w.deleted ? "Cut: click to restore" : ""}
              onClick={(e) => {
                if (w.deleted) return op({ op: "restore", indices: [w.i] });
                if (e.shiftKey) return setPick(pick.includes(w.i) ? pick.filter((x) => x !== w.i) : [...pick, w.i]);
                if (w.out != null) seek(w.out);
              }}
              onDoubleClick={() => setFix({ i: w.i, text: w.word })}>{w.word} </span>
          );
        })}
      </div>
    </div>
  );
}

export function CaptionsPanel({ v, onEdl, templates, fonts, reload }: { v: View; onEdl: (fn: (e: Edl) => Edl, l: string) => void; templates: any; fonts: any[]; reload: () => void }) {
  const c = v.edl.captions;
  const key = typeof c.template === "string" ? c.template : c.template?.key || "custom";
  const found = (templates?.templates || []).find((x: any) => x.key === key);
  const cur = found?.template || (typeof c.template === "object" ? c.template : null);
  const [draft, setDraft] = useState<any>(null);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState("");
  const tmpl = draft || cur || {};
  const set = (k: string, val: any) => setDraft({ ...tmpl, [k]: val });
  const apply = () => onEdl((e) => { e.captions.template = { ...tmpl, base: tmpl.base || key }; return e; }, "Edit caption style");
  const saveMine = async () => {
    try {
      const r = await api.post("/caption-templates", { name: name || "My style", template: { ...tmpl, base: tmpl.base || key } });
      setMsg(`Saved "${r.name || name}"`); reload();
      onEdl((e) => { e.captions.template = r.key; return e; }, "Use saved template");
    } catch (e: any) { setMsg(e.message); }
  };
  const langs = ["", "es", "pt", "fr", "de", "it", "ja", "ko", "hi", "ar", "id", "tr"];
  return (
    <div className="stack tight">
      <div className="row wrap" style={{ gap: 6 }}>
        {(templates?.templates || []).map((x: any) => (
          <button key={x.key} className={x.key === key ? "pill-btn on" : "pill-btn"} style={{ fontFamily: `"CF ${x.template?.family}"` }}
            onClick={() => { setDraft(null); onEdl((e) => { e.captions.template = x.key; return e; }, `Caption style: ${x.name}`); }}>{x.name}</button>
        ))}
      </div>
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={c.enabled !== false} onChange={(e) => onEdl((x) => { x.captions.enabled = e.target.checked; return x; }, e.target.checked ? "Captions on" : "Captions off")} /> Show captions</label>
      <span className="muted tiny">Captions sit in one fixed place at the bottom for the whole clip and never move or animate. The spoken word changes colour only.</span>
      <label className="field">Fine-tune the height for this clip ({Math.round(c.nudge || 0)} px)
        <input type="range" min={-120} max={60} step={5} value={Math.max(-120, Math.min(60, c.nudge || 0))} onChange={(e) => onEdl((x) => { x.captions.nudge = +e.target.value; return x; }, "Caption height")} /></label>
      <label className="field">Words per caption
        <select value={c.words_per_chunk || tmpl.max_words || 3} onChange={(e) => onEdl((x) => { x.captions.words_per_chunk = +e.target.value; return x; }, "Words per caption")}>
          {[1, 2, 3].map((n) => <option key={n} value={n}>{n === 3 ? "1–3 (standard)" : `Up to ${n}`}</option>)}
          {[4, 5, 6].map((n) => <option key={n} value={n}>Up to {n} (1–2 lines)</option>)}
        </select></label>
      <details>
        <summary>Edit this style</summary>
        <div className="grid2">
          <label className="field">Font<select value={tmpl.family || ""} onChange={(e) => set("family", e.target.value)}>{fonts.map((f) => <option key={f.family}>{f.family}</option>)}</select></label>
          <label className="field">Size<input type="number" min={104} max={140} value={tmpl.em || 124} onChange={(e) => set("em", +e.target.value)} /></label>
          <label className="field">Text<input type="color" value={hex(tmpl.text)} onChange={(e) => set("text", e.target.value)} /></label>
          <label className="field">Highlight<input type="color" value={hex(tmpl.highlight)} onChange={(e) => set("highlight", e.target.value)} /></label>
          <label className="field">Outline<input type="color" value={hex(tmpl.outline)} onChange={(e) => set("outline", e.target.value)} /></label>
          <label className="field">Outline width<input type="number" min={6} max={12} value={tmpl.outline_px ?? 9} onChange={(e) => set("outline_px", +e.target.value)} /></label>
          <label className="field">Shadow<input type="number" min={0} max={8} value={tmpl.shadow_px ?? 4} onChange={(e) => set("shadow_px", +e.target.value)} /></label>
          <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={tmpl.highlight_on !== false} onChange={(e) => set("highlight_on", e.target.checked)} /> Highlight the spoken word</label>
          <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={!!tmpl.uppercase} onChange={(e) => set("uppercase", e.target.checked)} /> UPPERCASE</label>
          <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={!!tmpl.emoji} onChange={(e) => set("emoji", e.target.checked)} /> Emoji on keywords</label>
        </div>
        <div className="row wrap" style={{ gap: 6, marginTop: 8 }}>
          <button className="btn-primary" disabled={!draft} onClick={apply}>Apply to this clip</button>
          <input placeholder="Template name" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 140 }} />
          <button onClick={saveMine}>Save as my template</button>
        </div>
        {msg && <span className="muted tiny">{msg}</span>}
      </details>
      <label className="field">Caption language
        <select value={c.translate_to || ""} onChange={(e) => api.post(`/edit/${v.clip_id}/translate`, { language: e.target.value || null }).then(reload).catch((er) => setMsg(er.message))}>
          {langs.map((l) => <option key={l} value={l}>{l ? l.toUpperCase() : `Original${c.language ? ` (${c.language})` : ""}`}</option>)}
        </select></label>
    </div>
  );
}

export const LAYOUTS: [string, string][] = [["corner", "Gameplay + facecam in a corner"], ["focus", "Focus on the streamer"],
  ["split", "Split: facecam top, game bottom"], ["crop", "Gameplay crop (fixed, on the action)"],
  ["speaker", "Speaker crop (fixed)"], ["blur_fill", "Full frame, blurred background"]];

export function LayoutPanel({ v, onEdl, post, t }: { v: View; onEdl: (fn: (e: Edl) => Edl, l: string) => void; post: (path: string, b?: any) => void; t: number }) {
  const lay = v.edl.layout;
  const kept = keptOf(v);
  const fc = lay.facecam;
  return (
    <div className="stack tight">
      <div className="row wrap" style={{ gap: 6 }}>
        <button onClick={() => post(`/edit/${v.clip_id}/reframe`)}>Run AI reframe again</button>
        <button onClick={() => onEdl((e) => {
          const src = +toSrc(kept, t).toFixed(3);
          const prev = [...e.layout.keyframes].reverse().find((k: any) => k.t <= src) || e.layout.keyframes[0] || { layout: "blur_fill", path: [[0, 0.5]] };
          e.layout.keyframes = [...e.layout.keyframes.filter((k: any) => Math.abs(k.t - src) > 0.05), { ...prev, t: src, auto: false }].sort((a: any, b: any) => a.t - b.t);
          return e;
        }, "Add layout keyframe")}>+ Keyframe at {fmt(t)}</button>
      </div>
      <span className="muted tiny">The clip switches layout at each keyframe (◆ on the timeline; drag to move).</span>
      {(lay.keyframes || []).map((k: any, i: number) => (
        <div key={i} className="row" style={{ gap: 6 }}>
          <span className="muted" style={{ width: 44 }}>{fmt(toOut(kept, k.t) ?? 0)}</span>
          <select value={k.layout} onChange={(e) => onEdl((x) => { x.layout.keyframes[i].layout = e.target.value; x.layout.keyframes[i].auto = false; return x; }, `Layout: ${e.target.value}`)}>
            {LAYOUTS.map(([id, l]) => <option key={id} value={id}>{l}</option>)}
          </select>
          {k.scene && <span className="pill">{k.scene}</span>}
          {i > 0 && <button className="btn-danger" onClick={() => onEdl((x) => { x.layout.keyframes.splice(i, 1); return x; }, "Remove keyframe")}>✕</button>}
        </div>
      ))}
      <span className="muted tiny">Every shot is one fixed crop: nothing pans or slides. A layout change is an instant cut.</span>
      {(lay.keyframes || []).some((k: any) => k.layout === "corner" || k.layout === "focus") && (
        <div className="stack tight">
          <b className="small-text">Facecam corner</b>
          <div className="row wrap" style={{ gap: 4 }}>
            {[["tl", "↖ Top left"], ["tr", "↗ Top right"], ["bl", "↙ Bottom left"], ["br", "↘ Bottom right"]].map(([c, l]) => (
              <button key={c} className={(lay.pip?.corner || "tr") === c ? "pill-btn on small" : "pill-btn small"}
                onClick={() => post(`/edit/${v.clip_id}/pip`, { corner: c, w: lay.pip?.w ?? 0.34 })}>{l}</button>))}
          </div>
          <label className="field">Size {Math.round((lay.pip?.w ?? 0.34) * 100)}% of the width
            <input type="range" min={0.25} max={0.45} step={0.01} defaultValue={lay.pip?.w ?? 0.34}
              onMouseUp={(e: any) => post(`/edit/${v.clip_id}/pip`, { corner: lay.pip?.corner || "tr", w: +e.target.value })} /></label>
          <button className="btn-quiet" onClick={() => post(`/edit/${v.clip_id}/pip`, { corner: lay.pip?.corner || "tr", w: lay.pip?.w ?? 0.34, remember: true })}>Save as my default for this streamer</button>
          {lay.pip?.source === "auto" && <span className="muted tiny">Corner picked automatically: the one that hides the least of the game's HUD and action.</span>}
        </div>)}
      {fc && <span className="muted tiny">Facecam: {lay.facecam_source === "saved" ? "your box" : lay.facecam_source === "guess" ? "best guess (draw it below to be sure)" : "detected"}.</span>}
    </div>
  );
}

export function HookPanel({ v, onEdl, post }: { v: View; onEdl: (fn: (e: Edl) => Edl, l: string) => void; post: (path: string, b?: any) => void }) {
  const h = v.edl.hook;
  const [text, setText] = useState(h.text || "");
  useEffect(() => setText(h.text || ""), [h.text]);
  const texts = v.edl.texts || {};
  const plats = ["tiktok", "youtube_shorts", "instagram_reels", "x"];
  const [plat, setPlat] = useState("tiktok");
  const tx = texts[plat] || { title: "", description: "", hashtags: [] };
  const setTx = (k: string, val: any) => onEdl((e) => { e.texts = { ...(e.texts || {}), [plat]: { ...tx, [k]: val } }; return e; }, `Edit ${plat} ${k}`);
  return (
    <div className="stack tight">
      <div><button className="btn-primary" onClick={() => post(`/edit/${v.clip_id}/hooks`)}>✨ Write hooks, titles and hashtags</button></div>
      <label className="field">Hook (top of the screen, first {h.sec || 2.2}s)
        <input value={text} onChange={(e) => setText(e.target.value)} onBlur={() => text !== (h.text || "") && onEdl((e) => { e.hook.text = text || null; return e; }, "Edit hook")} /></label>
      {(h.options || []).length > 0 && (
        <div className="stack tight">{h.options.map((o: string, i: number) => (
          <button key={i} className={o === h.text ? "pill-btn on" : "pill-btn"} style={{ textAlign: "left" }} onClick={() => onEdl((e) => { e.hook.text = o; return e; }, "Pick hook")}>{o}</button>
        ))}</div>
      )}
      <div className="row wrap" style={{ gap: 6 }}>{plats.map((p) => <button key={p} className={p === plat ? "tab on" : "tab"} onClick={() => setPlat(p)}>{{ tiktok: "TikTok", youtube_shorts: "Shorts", instagram_reels: "Reels", x: "X" }[p]}</button>)}</div>
      <label className="field">Title<input defaultValue={tx.title} key={`t${plat}${v.head}`} onBlur={(e) => e.target.value !== tx.title && setTx("title", e.target.value)} /></label>
      <label className="field">Description<textarea rows={3} defaultValue={tx.description} key={`d${plat}${v.head}`} onBlur={(e) => e.target.value !== tx.description && setTx("description", e.target.value)} /></label>
      <label className="field">Hashtags<input defaultValue={(tx.hashtags || []).map((x: string) => "#" + x.replace(/^#/, "")).join(" ")} key={`h${plat}${v.head}`}
        onBlur={(e) => setTx("hashtags", e.target.value.split(/[\s,]+/).filter(Boolean).map((x) => "#" + x.replace(/^#/, "")))} /></label>
      {(v.edl.rules?.tags || []).length > 0 && <span className="muted tiny">Campaign hashtags are always added when posting: {v.edl.rules.tags.join(" ")}</span>}
    </div>
  );
}

export function MusicPanel({ v, onEdl, tracks, post }: { v: View; onEdl: (fn: (e: Edl) => Edl, l: string) => void; tracks: any[]; post: (path: string, b?: any) => void }) {
  const [trend, setTrend] = useState<any>({ trending: [] });
  const [tmsg, setTmsg] = useState("");
  useEffect(() => { api.get(`/clips/${v.clip_id}/edit`).then(setTrend).catch(() => {}); }, [v.clip_id]);
  const exportTrending = async () => {
    try {
      setTmsg("Exporting…");
      const { job_id } = await api.post(`/clips/${v.clip_id}/export-for-trending`);
      await waitForJob(job_id);
      const r = await api.get(`/clips/${v.clip_id}/edit`); setTrend(r);
      setTmsg("Saved a version with no ClipFarm music. Add the trending sound inside TikTok/Instagram when you post.");
    } catch (e: any) { setTmsg(e.message); }
  };
  const m = v.edl.music;
  const a = v.edl.audio;
  const blocked = !!v.edl.rules?.no_music;
  return (
    <div className="stack tight">
      {blocked && <span className="warn-text">This campaign is original audio only: no music can be added.</span>}
      <label className="row" style={{ gap: 8 }}><input type="checkbox" disabled={blocked} checked={!!m.enabled && !blocked}
        onChange={(e) => onEdl((x) => { x.music.enabled = e.target.checked; return x; }, e.target.checked ? "Music on" : "Original audio only")} /> Music (off = original audio only)</label>
      <label className="field">Track
        <select disabled={blocked} value={m.track_id || ""} onChange={(e) => e.target.value
          ? post(`/edit/${v.clip_id}/music`, { track_id: Number(e.target.value) })
          : onEdl((x) => { x.music.enabled = false; return x; }, "No music")}>
          <option value="">(none)</option>
          {tracks.filter((x) => x.enabled !== 0).map((x) => <option key={x.id} value={x.id}>{x.title} · {(x.moods || []).join("/")} · {Math.round(x.bpm || 0)} BPM</option>)}
        </select></label>
      <div><button disabled={blocked} onClick={() => post(`/edit/${v.clip_id}/music`, {})}>🎲 Pick a track for this moment</button></div>
      {m.licence && <span className="muted tiny">Licence: {m.licence}{m.mode ? ` · drop ${m.mode === "drop" ? "lands on the payoff" : m.mode}` : ""}</span>}
      <label className="field">Music volume ({(m.volume_db || 0).toFixed(0)} dB)<input type="range" min={-18} max={6} step={1} value={m.volume_db || 0} disabled={!m.enabled}
        onChange={(e) => onEdl((x) => { x.music.volume_db = +e.target.value; return x; }, "Music volume")} /></label>
      <label className="field">Duck under speech ({(m.duck_db ?? 15).toFixed(0)} dB)<input type="range" min={12} max={18} step={1} value={m.duck_db ?? 15} disabled={!m.enabled}
        onChange={(e) => onEdl((x) => { x.music.duck_db = +e.target.value; return x; }, "Ducking")} /></label>
      <label className="field">Start the song at ({(m.offset || 0).toFixed(1)} s into the track)<input type="range" min={0} max={90} step={0.5} value={m.offset || 0} disabled={!m.enabled}
        onChange={(e) => onEdl((x) => { x.music.offset = +e.target.value; x.music.beats = []; return x; }, "Music start")} /></label>
      <hr />
      <label className="field">Voice volume ({(a.speech_gain_db || 0).toFixed(0)} dB)<input type="range" min={-6} max={9} step={1} value={a.speech_gain_db || 0}
        onChange={(e) => onEdl((x) => { x.audio.speech_gain_db = +e.target.value; return x; }, "Voice volume")} /></label>
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={!!a.enhance} onChange={(e) => onEdl((x) => { x.audio.enhance = e.target.checked; return x; }, "Speech enhancement")} /> Speech enhancement (clearer voice)</label>
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={!!a.denoise} onChange={(e) => onEdl((x) => { x.audio.denoise = e.target.checked; return x; }, "Noise reduction")} /> Noise reduction</label>
      <span className="muted tiny">The final mix is normalised to {a.target_lufs ?? -14} LUFS.</span>
      <hr />
      <b>Trending sound (add it in the app when you post)</b>
      {(trend.trending || []).length ? trend.trending.map((x: any) => (
        <span key={x.id} className="tiny">♪ {x.name}{x.artist ? ` – ${x.artist}` : ""} <span className="muted">({x.platform})</span></span>)) :
        <span className="muted tiny">Add trending sound names in Settings › Music to get suggestions. ClipFarm never puts a trending song in your video.</span>}
      <div><button onClick={exportTrending}>{trend.nomusic_path ? "Re-export for trending sound" : "Export for trending sound"}</button></div>
      {trend.nomusic_path && <span className="muted tiny">Saved: {trend.nomusic_path}</span>}
      {tmsg && <span className="muted tiny">{tmsg}</span>}
    </div>
  );
}

export function EffectsPanel({ v, onEdl, t, post }: { v: View; onEdl: (fn: (e: Edl) => Edl, l: string) => void; t: number; post: (path: string, b?: any) => void }) {
  const e = v.edl;
  const kept = keptOf(v);
  const [sfx, setSfx] = useState<any[]>([]);
  const [broll, setBroll] = useState<any>({ items: [] });
  useEffect(() => { api.get("/sfx").then(setSfx).catch(() => {}); api.get("/broll").then(setBroll).catch(() => {}); }, []);
  const at = () => +toSrc(kept, t).toFixed(3);
  const add = (list: string, item: any, label: string) => onEdl((x) => { x[list] = [...(x[list] || []), item]; return x; }, label);
  return (
    <div className="stack tight">
      <div className="row wrap" style={{ gap: 6 }}>
        <button className="btn-primary" onClick={() => post(`/edit/${v.clip_id}/effects`, {})}>✨ Auto zooms, effects and sounds</button>
        <button onClick={() => post(`/edit/${v.clip_id}/effects`, { clear: true })}>Remove automatic ones</button>
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        <select defaultValue="" aria-label="Add sound effect" onChange={(x) => { const id = x.target.value; x.target.value = ""; id && post(`/edit/${v.clip_id}/sfx`, { id, t_out: t }); }}>
          <option value="">+ Sound effect at {fmt(t)}…</option>
          {sfx.map((x) => <option key={x.id} value={x.id}>{x.title}{x.source === "local" ? " (your folder)" : ""}</option>)}
        </select>
        <select defaultValue="" aria-label="Add B-roll" disabled={!broll.items.length} onChange={(x) => { const p = x.target.value; x.target.value = ""; p && post(`/edit/${v.clip_id}/broll`, { path: p, t_out: t }); }}>
          <option value="">{broll.items.length ? `+ B-roll at ${fmt(t)}…` : "B-roll: set your folder in Settings › Music"}</option>
          {broll.items.map((x: any) => <option key={x.path} value={x.path}>{x.name}</option>)}
        </select>
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        <button onClick={() => add("zooms", { t: at(), kind: "punch", amount: 0.18, dur: 0.6 }, "Add zoom")}>+ Zoom cut at {fmt(t)}</button>
        <button onClick={() => add("overlays", { kind: "flash", t: at(), dur: 0.15 }, "Add flash")}>+ Flash</button>

        {REACTION_EMOJI.map((em) => <button key={em} title="Reaction emoji" onClick={() => add("overlays", { kind: "emoji", t: at(), dur: 1.2, emoji: em }, "Add emoji")}>{em}</button>)}
      </div>
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={e.progress_bar !== false} onChange={(x) => onEdl((y) => { y.progress_bar = x.target.checked; return y; }, "Progress bar")} /> Progress bar</label>
      {[["zooms", "Zoom"], ["overlays", "Effect"], ["sfx", "Sound effect"], ["broll", "B-roll"]].map(([list, label]) => (e[list] || []).map((it: any, i: number) => (
        <div key={`${list}${i}`} className="row" style={{ gap: 6 }}>
          <span className="muted" style={{ width: 44 }}>{fmt(toOut(kept, it.t) ?? 0)}</span>
          <span className="grow">{label}: {it.kind || it.id || it.emoji || it.path?.split(/[\\/]/).pop()}{it.licence ? ` · ${it.licence}` : ""}</span>
          <button className="btn-danger" onClick={() => onEdl((x) => { x[list].splice(i, 1); return x; }, `Remove ${label.toLowerCase()}`)}>✕</button>
        </div>
      )))}
    </div>
  );
}

export function RulesPanel({ v, flags, fix }: { v: View; flags: any[]; fix: (id: string | null) => void }) {
  const r = v.edl.rules || {};
  return (
    <div className="stack tight">
      {!r.program && <span className="muted">This clip isn't linked to a campaign.</span>}
      {r.program && <b>{r.program}</b>}
      {flags.length === 0 && r.program && <span className="ok-text">✓ Follows every campaign rule</span>}
      {flags.map((f) => (
        <div key={f.id} className={f.severity === "error" ? "ed-flag bad" : "ed-flag warn"}>
          <span>⚑ {f.message}</span>
          {f.fix && <button onClick={() => fix(f.id)}>{f.fix}</button>}
        </div>
      ))}
      {flags.filter((f) => f.fix).length > 1 && <button className="btn-primary" onClick={() => fix(null)}>Fix all</button>}
      {r.program && (
        <div className="stack tight muted tiny">
          {r.tags?.length > 0 && <span>Hashtags: {r.tags.join(" ")}</span>}
          {r.credit && <span>Credit: {r.credit}</span>}
          {r.disclosure && <span>Disclosure: {r.disclosure}</span>}
          {r.onscreen?.length > 0 && <span>Must stay in frame: {r.onscreen.join(", ").replace(/_/g, " ")}</span>}
          {r.no_music && <span>Original audio only</span>}
          {r.platforms?.length > 0 && <span>Platforms: {r.platforms.join(", ").replace(/_/g, " ")}</span>}
        </div>
      )}
    </div>
  );
}
