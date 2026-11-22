// Editor building blocks: live 9:16 preview with caption overlay + safe zones, the multi-track timeline,
// and the right-hand panels (Transcript, Captions, Layout, Hook & Title, Music & Audio, Effects, Campaign rules).
import { useEffect, useMemo, useRef, useState } from "react";
import { api, waitForJob } from "../api";

export type View = any;
export type Edl = any;

// ---- time mapping (source <-> output), same rules as the engine's timeline.py ----------------------------
export function keptOf(v: View): [number, number][] { return (v?.kept || []) as [number, number][]; }
export function toOut(kept: [number, number][], src: number): number | null {
  let acc = 0;
  for (const [a, b] of kept) {
    if (src < a) return null;
    if (src <= b) return acc + (src - a);
    acc += b - a;
  }
  return null;
}
export function toSrc(kept: [number, number][], out: number): number {
  let acc = 0;
  for (const [a, b] of kept) {
    if (out <= acc + (b - a)) return a + Math.max(0, out - acc);
    acc += b - a;
  }
  return kept.length ? kept[kept.length - 1][1] : 0;
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

export function Preview({ v, canvas, videoRef, t, setT, zone, fallback }: {
  v: View; canvas: any; videoRef: { current: HTMLVideoElement | null }; t: number; setT: (x: number) => void;
  zone: string | null; fallback: string | null;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(300);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const s = w / 1080;
  const src = canvas?.base ? api.mediaUrl(canvas.base) : fallback ? api.mediaUrl(fallback) : null;
  const live = !!canvas?.base;
  const ev = live ? (canvas.events || []).filter((e: any) => e.start <= t && t < e.end) : [];
  const st = canvas?.style || {};
  const z = zone ? SAFE[zone] : null;
  return (
    <div className="ed-preview" ref={wrap}>
      {src ? (
        <video ref={videoRef} src={src} playsInline onTimeUpdate={(e) => setT((e.target as HTMLVideoElement).currentTime)}
          onClick={(e) => { const el = e.target as HTMLVideoElement; el.paused ? el.play() : el.pause(); }} />
      ) : <div className="ed-empty muted">Building the preview…</div>}
      {ev.map((e: any, i: number) => (
        <div key={i} className={`ed-cap anim-${e.animation || "pop"}`} style={{
          left: e.x * s, top: e.y * s, fontFamily: `"CF ${st.family}", Impact, sans-serif`, fontSize: e.em * s,
          lineHeight: `${(e.line_h || e.em * 1.12) * s}px`, color: hex(st.text),
          WebkitTextStroke: `${Math.max(1, (st.outline_px || 6) * s)}px ${hex(st.outline)}`,
          textShadow: st.shadow_px ? `0 ${st.shadow_px * s}px ${st.shadow_px * s * 1.5}px rgba(0,0,0,.6)` : undefined,
        }}>
          {(e.rows || [[e.word]]).map((row: string[], r: number) => (
            <div key={r}>{row.map((wd, k) => (
              <span key={k} style={{ color: e.hi?.[r]?.[k] ? hex(st.highlight) : undefined, paintOrder: "stroke fill" }}>{wd}{k < row.length - 1 ? " " : ""}</span>
            ))}</div>
          ))}
        </div>
      ))}
      {z && (
        <div className="ed-safe" aria-hidden>
          <div style={{ top: 0, height: z.top * s, left: 0, right: 0 }} />
          <div style={{ top: z.bottom * s, bottom: 0, left: 0, right: 0 }} />
          <div style={{ top: z.top * s, height: (z.bottom - z.top) * s, left: z.right * s, right: 0 }} />
          <span>{z.label} safe zone</span>
        </div>
      )}
      {!live && src && <span className="ed-badge">Rendered file{canvas ? " (updating preview…)" : ""}</span>}
      <span className="ed-time">{fmt(t)} / {fmt(v?.duration || 0)}</span>
    </div>
  );
}

// ---- timeline ------------------------------------------------------------------------------------------
export function Timeline({ v, t, seek, splits, sel, setSel, onEdl, onRange }: {
  v: View; t: number; seek: (x: number) => void; splits: number[];
  sel: [number, number] | null; setSel: (s: [number, number] | null) => void;
  onEdl: (fn: (e: Edl) => Edl, label: string) => void; onRange: (a: number, b: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  const [drag, setDrag] = useState<any>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const e: Edl = v.edl;
  const kept = keptOf(v);
  const dur = Math.max(0.1, v.duration || 0);
  const px = w / dur;
  const xOf = (out: number) => out * px;
  const outAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    return Math.max(0, Math.min(dur, (clientX - r.left) / px));
  };
  // video segments in output time, subdivided by splits
  const segs = useMemo(() => {
    const out: [number, number][] = [];
    let acc = 0;
    for (const [a, b] of kept) {
      const len = b - a;
      const cuts = splits.filter((s) => s > acc + 0.05 && s < acc + len - 0.05).sort((x, y) => x - y);
      let s0 = acc;
      for (const c of cuts) { out.push([s0, c]); s0 = c; }
      out.push([s0, acc + len]);
      acc += len;
    }
    return out;
  }, [v.kept, splits]);
  const kfs = (e.layout?.keyframes || []).map((k: any, i: number) => ({ i, out: toOut(kept, k.t) ?? (i === 0 ? 0 : null), layout: k.layout }))
    .filter((k: any) => k.out != null);
  const zooms = (e.zooms || []).map((z: any, i: number) => ({ i, out: toOut(kept, z.t), kind: z.kind })).filter((z: any) => z.out != null);
  const sfx = (e.sfx || []).map((x: any, i: number) => ({ i, out: toOut(kept, x.t), name: x.id })).filter((x: any) => x.out != null);
  const broll = (e.broll || []).map((x: any, i: number) => ({ i, out: toOut(kept, x.t), dur: x.dur || 2 })).filter((x: any) => x.out != null);
  const m = e.music || {};
  const musicOn = m.enabled && m.path && !e.rules?.no_music;

  const onMove = (ev: { clientX: number }) => {
    if (!drag) return;
    setDrag({ ...drag, out: outAt(ev.clientX) });
  };
  const onUp = () => {
    if (!drag) return;
    const o = drag.out;
    if (drag.kind === "start") onRange(o <= 0.01 ? e.range[0] - (drag.start - drag.out) : toSrc(kept, o), e.range[1]);
    else if (drag.kind === "end") onRange(e.range[0], o >= dur - 0.01 ? e.range[1] + (o - dur) : toSrc(kept, o));
    else if (drag.kind === "kf") onEdl((x) => { x.layout.keyframes[drag.i].t = +toSrc(kept, o).toFixed(3); x.layout.keyframes.sort((a: any, b: any) => a.t - b.t); return x; }, "Move layout keyframe");
    else if (drag.kind === "zoom") onEdl((x) => { x.zooms[drag.i].t = +toSrc(kept, o).toFixed(3); return x; }, "Move zoom");
    else if (drag.kind === "seek") seek(o);
    setDrag(null);
  };
  const start = (kind: string, extra: any = {}) => (ev: { clientX: number; stopPropagation: () => void }) => {
    ev.stopPropagation();
    setDrag({ kind, out: outAt(ev.clientX), start: outAt(ev.clientX), ...extra });
  };
  const ticks = [];
  const step = dur > 120 ? 15 : dur > 40 ? 5 : 2;
  for (let s = 0; s <= dur; s += step) ticks.push(s);
  return (
    <div className="ed-timeline" onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}>
      <div className="ed-labels">{["", "Video", "Captions", "Music", "SFX", "B-roll"].map((l) => <span key={l}>{l}</span>)}</div>
      <div className="ed-tracks" ref={ref} onMouseDown={start("seek")}>
        <div className="ed-ruler">{ticks.map((s) => <span key={s} style={{ left: xOf(s) }}>{fmt(s)}</span>)}</div>
        <div className="ed-track video">
          {segs.map(([a, b], i) => (
            <div key={i} className={sel && sel[0] === a ? "seg on" : "seg"} style={{ left: xOf(a), width: Math.max(2, xOf(b) - xOf(a)) }}
              onMouseDown={(ev) => { ev.stopPropagation(); setSel(sel && sel[0] === a ? null : [a, b]); seek(a); }}
              title={`${fmt(a)}–${fmt(b)} · click to select, Delete to remove`} />
          ))}
          <div className="handle l" onMouseDown={start("start")} title="Drag to change the start" />
          <div className="handle r" onMouseDown={start("end")} title="Drag to change the end" />
          {kfs.map((k: any) => (
            <div key={`k${k.i}`} className="kf layout" style={{ left: xOf(drag?.kind === "kf" && drag.i === k.i ? drag.out : k.out) }}
              title={`Layout: ${k.layout} (drag to move)`} onMouseDown={k.i === 0 ? undefined : start("kf", { i: k.i })}>◆</div>
          ))}
          {zooms.map((z: any) => (
            <div key={`z${z.i}`} className="kf zoom" style={{ left: xOf(drag?.kind === "zoom" && drag.i === z.i ? drag.out : z.out) }}
              title={`Zoom: ${z.kind} (drag to move)`} onMouseDown={start("zoom", { i: z.i })}>●</div>
          ))}
        </div>
        <div className="ed-track caps">
          {(v.words_out || []).filter((x: any) => x.out != null && !x.deleted).map((x: any) => (
            <div key={x.i} className="word" style={{ left: xOf(x.out), width: Math.max(2, (x.end - x.start) * px) }} title={x.word} />
          ))}
        </div>
        <div className="ed-track music">
          {musicOn && <div className="bar" style={{ left: xOf(Math.max(0, m.delay || 0)), right: 0 }} title={m.title || "Music"} />}
          {musicOn && (m.beats || []).filter((b: number) => b <= dur).map((b: number, i: number) => <div key={i} className="beat" style={{ left: xOf(b) }} />)}
          {!musicOn && <span className="muted tiny">{e.rules?.no_music ? "Original audio only (campaign rule)" : "No music"}</span>}
        </div>
        <div className="ed-track sfx">{sfx.map((x: any) => <div key={x.i} className="kf sfx" style={{ left: xOf(x.out) }} title={x.name}>♪</div>)}</div>
        <div className="ed-track broll">{broll.map((x: any) => <div key={x.i} className="bar" style={{ left: xOf(x.out), width: x.dur * px }} />)}</div>
        <div className="playhead" style={{ left: xOf(drag?.kind === "seek" ? drag.out : t) }} />
        {drag && (drag.kind === "start" || drag.kind === "end") && <div className="ghost" style={{ left: xOf(drag.out) }} />}
      </div>
      <div className="ed-tl-help muted tiny">Space play/pause · J/K/L · S split · Delete remove selected part · Ctrl+Z / Ctrl+Y</div>
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
  const cur = (templates?.templates || []).find((x: any) => x.key === key) || (typeof c.template === "object" ? c.template : null);
  const [draft, setDraft] = useState<any>(null);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState("");
  const tmpl = draft || cur || {};
  const set = (k: string, val: any) => setDraft({ ...tmpl, [k]: val });
  const apply = () => onEdl((e) => { e.captions.template = { ...tmpl, base: tmpl.base || key }; return e; }, "Edit caption style");
  const saveMine = async () => {
    try {
      const r = await api.post("/caption-templates", { name: name || "My style", template: { ...tmpl, base: tmpl.base || key } });
      setMsg(`Saved "${r.template.label || name}"`); reload();
      onEdl((e) => { e.captions.template = r.key; return e; }, "Use saved template");
    } catch (e: any) { setMsg(e.message); }
  };
  const langs = ["", "es", "pt", "fr", "de", "it", "ja", "ko", "hi", "ar", "id", "tr"];
  return (
    <div className="stack tight">
      <div className="row wrap" style={{ gap: 6 }}>
        {(templates?.templates || []).map((x: any) => (
          <button key={x.key} className={x.key === key ? "pill-btn on" : "pill-btn"} style={{ fontFamily: `"CF ${x.family}"` }}
            onClick={() => { setDraft(null); onEdl((e) => { e.captions.template = x.key; return e; }, `Caption style: ${x.label}`); }}>{x.label}</button>
        ))}
      </div>
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={c.enabled !== false} onChange={(e) => onEdl((x) => { x.captions.enabled = e.target.checked; return x; }, e.target.checked ? "Captions on" : "Captions off")} /> Show captions</label>
      <label className="field">Position: move up or down ({Math.round(c.nudge || 0)} px)
        <input type="range" min={-400} max={400} step={10} value={c.nudge || 0} onChange={(e) => onEdl((x) => { x.captions.nudge = +e.target.value; return x; }, "Move captions")} /></label>
      <details>
        <summary>Edit this style</summary>
        <div className="grid2">
          <label className="field">Font<select value={tmpl.family || ""} onChange={(e) => set("family", e.target.value)}>{fonts.map((f) => <option key={f.family}>{f.family}</option>)}</select></label>
          <label className="field">Size<input type="number" min={40} max={140} value={tmpl.em || 90} onChange={(e) => set("em", +e.target.value)} /></label>
          <label className="field">Text<input type="color" value={hex(tmpl.text)} onChange={(e) => set("text", e.target.value)} /></label>
          <label className="field">Highlight<input type="color" value={hex(tmpl.highlight)} onChange={(e) => set("highlight", e.target.value)} /></label>
          <label className="field">Outline<input type="color" value={hex(tmpl.outline)} onChange={(e) => set("outline", e.target.value)} /></label>
          <label className="field">Outline width<input type="number" min={0} max={16} value={tmpl.outline_px ?? 8} onChange={(e) => set("outline_px", +e.target.value)} /></label>
          <label className="field">Shadow<input type="number" min={0} max={16} value={tmpl.shadow_px ?? 4} onChange={(e) => set("shadow_px", +e.target.value)} /></label>
          <label className="field">Words at a time<input type="number" min={1} max={3} value={tmpl.max_words || 3} onChange={(e) => set("max_words", +e.target.value)} /></label>
          <label className="field">Height on screen ({Math.round((tmpl.y ?? 0.64) * 100)}%)<input type="range" min={0.11} max={0.74} step={0.01} value={tmpl.y ?? 0.64} onChange={(e) => set("y", +e.target.value)} /></label>
          <label className="field">Animation<select value={tmpl.animation || "pop"} onChange={(e) => set("animation", e.target.value)}>{(templates?.animations || []).map((a: string) => <option key={a}>{a}</option>)}</select></label>
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

const LAYOUTS: [string, string][] = [["split", "Split: facecam top, game bottom"], ["crop", "Smart crop (follows the action)"],
  ["speaker", "Speaker-tracked crop"], ["pip", "Picture-in-picture facecam"], ["blur_fill", "Full frame, blurred background"]];

export function LayoutPanel({ v, onEdl, post, t }: { v: View; onEdl: (fn: (e: Edl) => Edl, l: string) => void; post: (path: string, b?: any) => void; t: number }) {
  const lay = v.edl.layout;
  const kept = keptOf(v);
  const fc = lay.facecam;
  const [box, setBox] = useState<any>(fc || { x: 0.72, y: 0.02, w: 0.26, h: 0.3 });
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
      <details>
        <summary>Facecam box {fc ? "(set)" : "(auto)"}</summary>
        <span className="muted tiny">Where the webcam is in the stream, as a share of the frame. Saved for this streamer.</span>
        <div className="grid2">
          {(["x", "y", "w", "h"] as const).map((k) => (
            <label key={k} className="field">{{ x: "Left", y: "Top", w: "Width", h: "Height" }[k]} ({Math.round(box[k] * 100)}%)
              <input type="range" min={0} max={1} step={0.01} value={box[k]} onChange={(e) => setBox({ ...box, [k]: +e.target.value })} /></label>
          ))}
        </div>
        <div className="ed-fcbox"><div style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }} /></div>
        <button onClick={() => post(`/edit/${v.clip_id}/facecam`, box)}>Use this facecam box</button>
      </details>
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
        <button onClick={() => add("zooms", { t: at(), kind: "punch", amount: 1.18, dur: 0.6 }, "Add zoom")}>+ Punch-in zoom at {fmt(t)}</button>
        <button onClick={() => add("overlays", { kind: "flash", t: at(), dur: 0.15 }, "Add flash")}>+ Flash</button>
        <button onClick={() => add("overlays", { kind: "shake", t: at(), dur: 0.5 }, "Add shake")}>+ Shake</button>
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
