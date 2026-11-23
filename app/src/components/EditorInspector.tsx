// Right-hand inspector: settings for whatever is selected (text, sticker, part of the video, transition, zoom,
// layout keyframe, sound, B-roll, volume key) plus clip-wide panels: Colour & crop, Audio keys, Facecam, Sequence.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmt, keptOf, toSrc, type Edl, type View } from "./EditorParts";
import type { Sel } from "./EditorTimeline";

type OnEdl = (fn: (e: Edl) => Edl, label: string) => void;
const uid = () => Math.random().toString(36).slice(2, 9);
export const FONT_CHOICES = ["Montserrat Black", "Montserrat ExtraBold", "Anton", "Archivo Black"];   // the bundled (OFL) fonts

export function newText(t: number, dur: number) {
  return { id: uid(), text: "Your text", start: +t.toFixed(2), end: +Math.min(dur, t + 3).toFixed(2), x: 0.5, y: 0.3,
    size: 80, color: "FFFFFF", box: "outline", font: "Montserrat Black", rotate: 0 };
}
export function newSticker(t: number, dur: number, s: { emoji?: string; path?: string }) {
  return { id: uid(), ...s, start: +t.toFixed(2), end: +Math.min(dur, t + 2).toFixed(2), x: 0.5, y: 0.4, size: 260, rotate: 0 };
}

function Num({ label, value, min, max, step, onChange, unit = "" }: { label: string; value: number; min: number; max: number; step: number; onChange: (n: number) => void; unit?: string }) {
  return (
    <label className="field">{label} <b>{Number(value).toFixed(step < 1 ? (step < 0.1 ? 2 : 1) : 0)}{unit}</b>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(+e.target.value)} /></label>
  );
}

/** Edits apply on release (sliders) or after you stop typing, so every change is one undo step. */
function useCommit(onEdl: OnEdl) {
  const timer = useRef<number | undefined>(undefined);
  return (fn: (e: Edl) => Edl, label: string, delay = 450) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onEdl(fn, label), delay);
  };
}

export function SelectionInspector({ v, sel, setSel, onEdl, t }: { v: View; sel: Sel; setSel: (s: Sel) => void; onEdl: OnEdl; t: number }) {
  const e = v.edl;
  const commit = useCommit(onEdl);
  const [local, setLocal] = useState<any>(null);
  const segs = keptOf(v);
  useEffect(() => {
    if (!sel) { setLocal(null); return; }
    const src = { text: e.text_layers, sticker: e.stickers, trans: e.transitions, zoom: e.zooms, kf: e.layout?.keyframes,
      sfx: e.sfx, broll: e.broll, mkey: e.audio_keys?.music, vkey: e.audio_keys?.speech }[sel.kind];
    setLocal(src ? JSON.parse(JSON.stringify(src[sel.i] ?? null)) : null);
  }, [sel?.kind, sel?.i, v.head]);
  if (!sel) return null;
  const listOf: Record<string, (x: Edl) => any[]> = {
    text: (x) => (x.text_layers ||= []), sticker: (x) => (x.stickers ||= []), trans: (x) => (x.transitions ||= []),
    zoom: (x) => (x.zooms ||= []), kf: (x) => x.layout.keyframes, sfx: (x) => (x.sfx ||= []), broll: (x) => (x.broll ||= []),
    mkey: (x) => ((x.audio_keys ||= {}).music ||= []), vkey: (x) => ((x.audio_keys ||= {}).speech ||= []),
  };
  const set = (k: string, val: any, label: string, delay = 450) => {
    const nx = { ...local, [k]: val };
    setLocal(nx);
    commit((x) => { listOf[sel.kind](x)[sel.i] = nx; return x; }, label, delay);
  };
  const remove = (label: string) => { onEdl((x) => { listOf[sel.kind](x).splice(sel.i, 1); return x; }, label); setSel(null); };
  const dup = () => onEdl((x) => { const L = listOf[sel.kind](x); L.push({ ...L[sel.i], id: uid(), start: L[sel.i].end, end: L[sel.i].end + (L[sel.i].end - L[sel.i].start) }); return x; }, "Duplicate");

  if (sel.kind === "part") {
    const a = toSrc(segs, sel.a ?? 0), b = toSrc(segs, sel.b ?? 0);
    const seg = segs[sel.i];
    const setSpeed = (speed: number) => onEdl((x) => {
      x.speed = (x.speed || []).filter((r: any) => r.b <= a + 1e-3 || r.a >= b - 1e-3);
      if (speed !== 1) x.speed.push({ a: +a.toFixed(3), b: +b.toFixed(3), speed });
      return x;
    }, speed === 1 ? "Normal speed" : `Speed ${speed}×`);
    return (
      <div className="card stack tight insp">
        <b>Part {fmt(sel.a ?? 0)}–{fmt(sel.b ?? 0)}{seg?.item ? ` · appended clip ${seg.item}` : ""}</b>
        {seg?.hold ? (
          <>
            <span className="muted small-text">Freeze frame</span>
            <Num label="Hold for" value={seg.hold} min={0.2} max={10} step={0.1} unit="s" onChange={(d) => commit((x) => {
              const f = (x.freezes || []).find((q: any) => Math.abs(q.t - seg.a) < 1e-3); if (f) f.dur = d; return x; }, "Freeze length")} />
            <button className="btn-danger" onClick={() => onEdl((x) => { x.freezes = (x.freezes || []).filter((q: any) => Math.abs(q.t - seg.a) > 1e-3); return x; }, "Remove freeze frame")}>Remove freeze frame</button>
          </>
        ) : seg?.item ? <span className="muted small-text">Speed and freeze work on the main clip. Edit the appended clip itself to change it.</span> : (
          <>
            <span className="small-text">Speed</span>
            <div className="row wrap" style={{ gap: 4 }}>
              {[0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4].map((sp) => (
                <button key={sp} className={(seg?.speed ?? 1) === sp ? "pill-btn on small" : "pill-btn small"} onClick={() => setSpeed(sp)}>{sp}×</button>))}
            </div>
            <button onClick={() => onEdl((x) => { (x.freezes ||= []).push({ t: +toSrc(segs, t).toFixed(3), dur: 1.0 }); return x; }, "Freeze frame")}>❄ Freeze frame at the playhead (F)</button>
          </>
        )}
        <button className="btn-danger" onClick={() => { onEdl((x) => { x.cuts = [...(x.cuts || []), [+a.toFixed(3), +b.toFixed(3)]]; return x; }, "Delete part"); setSel(null); }}>Delete this part (Del)</button>
      </div>
    );
  }
  if (!local) return null;
  if (sel.kind === "text") return (
    <div className="card stack tight insp">
      <b>Text layer</b>
      <textarea rows={2} value={local.text} onChange={(ev) => set("text", ev.target.value, "Edit text", 700)} aria-label="Text" />
      <div className="row" style={{ gap: 6 }}>
        <select value={local.font} onChange={(ev) => set("font", ev.target.value, "Font", 0)} aria-label="Font">{FONT_CHOICES.map((f) => <option key={f}>{f}</option>)}</select>
        <select value={local.box} onChange={(ev) => set("box", ev.target.value, "Text look", 0)} aria-label="Look">
          <option value="outline">Outline</option><option value="white_box">White box</option><option value="black_box">Black box</option></select>
        {local.box === "outline" && <input type="color" value={"#" + (local.color || "FFFFFF")} onChange={(ev) => set("color", ev.target.value.slice(1).toUpperCase(), "Text colour")} aria-label="Colour" />}
      </div>
      <Num label="Size" value={local.size} min={24} max={220} step={2} unit="px" onChange={(n) => set("size", n, "Text size")} />
      <Num label="Rotate" value={local.rotate || 0} min={-45} max={45} step={1} unit="°" onChange={(n) => set("rotate", n, "Rotate text")} />
      <div className="row" style={{ gap: 6 }}>
        <label className="field">From <input type="number" step={0.1} value={local.start} onChange={(ev) => set("start", +ev.target.value, "Text timing")} /></label>
        <label className="field">To <input type="number" step={0.1} value={local.end} onChange={(ev) => set("end", +ev.target.value, "Text timing")} /></label>
      </div>
      <span className="muted tiny">Drag the text on the preview to move it.</span>
      <div className="row" style={{ gap: 6 }}><button onClick={dup}>Duplicate (Ctrl+D)</button><button className="btn-danger" onClick={() => remove("Delete text")}>Delete</button></div>
    </div>
  );
  if (sel.kind === "sticker") return (
    <div className="card stack tight insp">
      <b>Sticker {local.emoji || ""}</b>
      <Num label="Size" value={local.size} min={60} max={900} step={10} unit="px" onChange={(n) => set("size", n, "Sticker size")} />
      <Num label="Rotate" value={local.rotate || 0} min={-45} max={45} step={1} unit="°" onChange={(n) => set("rotate", n, "Rotate sticker")} />
      <div className="row" style={{ gap: 6 }}>
        <label className="field">From <input type="number" step={0.1} value={local.start} onChange={(ev) => set("start", +ev.target.value, "Sticker timing")} /></label>
        <label className="field">To <input type="number" step={0.1} value={local.end} onChange={(ev) => set("end", +ev.target.value, "Sticker timing")} /></label>
      </div>
      <div className="row" style={{ gap: 6 }}><button onClick={dup}>Duplicate</button><button className="btn-danger" onClick={() => remove("Delete sticker")}>Delete</button></div>
    </div>
  );
  if (sel.kind === "trans") return (
    <div className="card stack tight insp">
      <b>Transition at {fmt(local.t)}</b>
      <select value={local.kind} onChange={(ev) => set("kind", ev.target.value, "Transition", 0)} aria-label="Transition">
        <option value="fade">Dip to black</option><option value="flash">Flash</option><option value="zoom">Zoom punch</option><option value="whip">Whip (zoom + shake)</option></select>
      <Num label="Length" value={local.dur ?? 0.4} min={0.1} max={1.5} step={0.05} unit="s" onChange={(n) => set("dur", n, "Transition length")} />
      <button className="btn-danger" onClick={() => remove("Delete transition")}>Delete</button>
    </div>
  );
  if (sel.kind === "zoom") return (
    <div className="card stack tight insp">
      <b>Zoom</b>
      <select value={local.kind} onChange={(ev) => set("kind", ev.target.value, "Zoom kind", 0)} aria-label="Zoom kind"><option value="punch">Punch-in</option><option value="pulse">Pulse</option></select>
      <Num label="Amount" value={local.amount ?? 0.12} min={0.03} max={0.5} step={0.01} onChange={(n) => set("amount", n, "Zoom amount")} />
      <Num label="Length" value={local.dur ?? 1} min={0.2} max={4} step={0.1} unit="s" onChange={(n) => set("dur", n, "Zoom length")} />
      <button className="btn-danger" onClick={() => remove("Delete zoom")}>Delete</button>
    </div>
  );
  if (sel.kind === "kf") return (
    <div className="card stack tight insp">
      <b>Layout from here</b>
      <select value={local.layout} onChange={(ev) => set("layout", ev.target.value, "Layout", 0)} aria-label="Layout">
        {[["split", "Facecam + gameplay"], ["cam_full", "Streamer full screen"], ["crop", "Gameplay crop"], ["blur_fill", "Full frame (blurred bars)"], ["pip", "Picture in picture"], ["speaker", "Speaker"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      {sel.i > 0 && <button className="btn-danger" onClick={() => remove("Delete layout change")}>Delete</button>}
    </div>
  );
  if (sel.kind === "sfx") return (
    <div className="card stack tight insp"><b>Sound effect {local.id}</b>
      <Num label="Volume" value={local.gain_db ?? -8} min={-30} max={6} step={1} unit=" dB" onChange={(n) => set("gain_db", n, "SFX volume")} />
      <button className="btn-danger" onClick={() => remove("Delete sound effect")}>Delete</button></div>
  );
  if (sel.kind === "broll") return (
    <div className="card stack tight insp"><b>B-roll</b>
      <Num label="Length" value={local.dur ?? 2} min={0.5} max={8} step={0.1} unit="s" onChange={(n) => set("dur", n, "B-roll length")} />
      <button className="btn-danger" onClick={() => remove("Delete B-roll")}>Delete</button></div>
  );
  if (sel.kind === "mkey" || sel.kind === "vkey") return (
    <div className="card stack tight insp"><b>{sel.kind === "mkey" ? "Music" : "Voice"} volume at {fmt(local.t)}</b>
      <Num label="Level" value={local.db} min={-60} max={12} step={1} unit=" dB" onChange={(n) => set("db", n, "Volume key")} />
      <button className="btn-danger" onClick={() => remove("Delete volume key")}>Delete</button></div>
  );
  return null;
}

export function ColourPanel({ v, onEdl }: { v: View; onEdl: OnEdl }) {
  const commit = useCommit(onEdl);
  const [c, setC] = useState<any>(v.edl.color || {});
  const [tf, setTf] = useState<any>(v.edl.transform || {});
  useEffect(() => { setC(v.edl.color || {}); setTf(v.edl.transform || {}); }, [v.head]);
  const sc = (k: string, n: number) => { const nx = { ...c, [k]: n }; setC(nx); commit((x) => ({ ...x, color: nx }), "Colour"); };
  const st = (k: string, n: any) => { const nx = { ...tf, [k]: n }; setTf(nx); commit((x) => ({ ...x, transform: nx }), "Crop / rotate"); };
  const crop = tf.crop || {};
  const scr = (k: string, n: number) => st("crop", { ...crop, [k]: n });
  return (
    <div className="stack tight">
      <h3>Colour</h3>
      <Num label="Brightness" value={c.brightness ?? 0} min={-0.3} max={0.3} step={0.01} onChange={(n) => sc("brightness", n)} />
      <Num label="Contrast" value={c.contrast ?? 1} min={0.5} max={1.5} step={0.01} onChange={(n) => sc("contrast", n)} />
      <Num label="Saturation" value={c.saturation ?? 1} min={0} max={2} step={0.01} onChange={(n) => sc("saturation", n)} />
      <Num label="Warmth" value={c.warmth ?? 0} min={-1} max={1} step={0.05} onChange={(n) => sc("warmth", n)} />
      <h3>Crop, zoom &amp; rotate</h3>
      <div className="grid2">
        {(["l", "r", "t", "b"] as const).map((k) => <Num key={k} label={{ l: "Crop left", r: "Crop right", t: "Crop top", b: "Crop bottom" }[k]} value={crop[k] ?? 0} min={0} max={0.4} step={0.01} onChange={(n) => scr(k, n)} />)}
      </div>
      <Num label="Zoom" value={tf.zoom ?? 1} min={1} max={2} step={0.01} unit="×" onChange={(n) => st("zoom", n)} />
      <Num label="Pan left/right" value={tf.x ?? 0} min={-1} max={1} step={0.05} onChange={(n) => st("x", n)} />
      <Num label="Pan up/down" value={tf.y ?? 0} min={-1} max={1} step={0.05} onChange={(n) => st("y", n)} />
      <Num label="Rotate" value={tf.rotate ?? 0} min={-45} max={45} step={0.5} unit="°" onChange={(n) => st("rotate", n)} />
      <button className="btn-quiet" onClick={() => onEdl((x) => ({ ...x, color: null, transform: null }), "Reset colour and crop")}>Reset</button>
    </div>
  );
}

export function AudioKeysPanel({ v, onEdl, t }: { v: View; onEdl: OnEdl; t: number }) {
  const keys = v.edl.audio_keys || {};
  const add = (track: "speech" | "music", db: number) => onEdl((x) => {
    const k = ((x.audio_keys ||= {})[track] ||= []);
    k.push({ t: +t.toFixed(2), db });
    k.sort((p: any, q: any) => p.t - q.t);
    return x;
  }, "Add volume key");
  return (
    <div className="stack tight">
      <h3>Volume keyframes</h3>
      <span className="muted tiny">Add points on the Voice or Music track; the level glides between them. Drag them on the timeline.</span>
      <div className="row wrap" style={{ gap: 6 }}>
        <button onClick={() => add("speech", 0)}>+ Voice key at {fmt(t)}</button>
        <button onClick={() => add("music", 0)}>+ Music key at {fmt(t)}</button>
        <button onClick={() => { add("music", -20); }} title="Music down to -20 dB from here">Duck music here</button>
      </div>
      {(["speech", "music"] as const).map((tr) => (keys[tr] || []).length > 0 && (
        <div key={tr} className="small-text">{tr === "speech" ? "Voice" : "Music"}: {(keys[tr] || []).map((k: any) => `${fmt(k.t)} ${k.db} dB`).join(" · ")}
          <button className="btn-quiet small" onClick={() => onEdl((x) => { x.audio_keys[tr] = []; return x; }, "Clear volume keys")}>Clear</button></div>))}
    </div>
  );
}

/** Drag the facecam box on the source picture; it's used by every split / picture-in-picture / full-reaction part. */
export function FacecamEditor({ v, onEdl }: { v: View; onEdl: OnEdl }) {
  const e = v.edl;
  const [box, setBox] = useState<any>(e.layout?.facecam || { x: 0.72, y: 0.04, w: 0.26, h: 0.3 });
  const [t, setT] = useState(+(e.range[0] + 1).toFixed(1));
  const ref = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<any>(null);
  useEffect(() => setBox(e.layout?.facecam || box), [v.head]);
  const rel = (ev: { clientX: number; clientY: number }) => {
    const r = ref.current!.getBoundingClientRect();
    return { x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height };
  };
  const clamp = (b: any) => ({ x: Math.max(0, Math.min(1 - b.w, b.x)), y: Math.max(0, Math.min(1 - b.h, b.y)),
    w: Math.max(0.05, Math.min(1, b.w)), h: Math.max(0.05, Math.min(1, b.h)) });
  const move = (ev: any) => {
    if (!drag) return;
    const p = rel(ev), dx = p.x - drag.p.x, dy = p.y - drag.p.y, b = drag.b;
    setBox(clamp(drag.mode === "move" ? { ...b, x: b.x + dx, y: b.y + dy } : { ...b, w: b.w + dx, h: b.h + dy }));
  };
  const save = () => onEdl((x) => {
    const cam = { x: +box.x.toFixed(4), y: +box.y.toFixed(4), w: +box.w.toFixed(4), h: +box.h.toFixed(4) };
    x.layout.facecam = cam;
    x.layout.keyframes = (x.layout.keyframes || []).map((k: any) => (["split", "pip", "cam_full"].includes(k.layout) ? { ...k, cam } : k));
    return x;
  }, "Facecam position");
  return (
    <div className="stack tight">
      <h3>Facecam</h3>
      <span className="muted tiny">Drag the box over the streamer's camera (corner to resize). Used by every split, picture-in-picture and full-reaction part.</span>
      <div className="fc-frame" ref={ref} onMouseMove={move} onMouseUp={() => setDrag(null)} onMouseLeave={() => setDrag(null)}>
        <img src={api.sourceFrameUrl(v.clip_id, t)} alt="Source frame" draggable={false} />
        <div className="fc-box" style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}
          onMouseDown={(ev) => { ev.preventDefault(); setDrag({ mode: "move", p: rel(ev), b: box }); }}>
          <i onMouseDown={(ev) => { ev.preventDefault(); ev.stopPropagation(); setDrag({ mode: "size", p: rel(ev), b: box }); }} />
        </div>
      </div>
      <Num label="Source time" value={t} min={e.range[0]} max={e.range[1]} step={0.5} unit="s" onChange={setT} />
      <button className="btn" onClick={save}>Use this facecam box</button>
    </div>
  );
}

export function SequencePanel({ v, del }: { v: View; del: (p: string) => void }) {
  const seq = v.edl.sequence || [];
  return (
    <div className="stack tight">
      <h3>Sequence</h3>
      <span className="muted tiny">Append other clips after this one (Bin › “+ Append”). Each keeps its own cuts and layout; captions follow.</span>
      {seq.length === 0 && <span className="muted small-text">Just this clip.</span>}
      {seq.map((it: any, i: number) => (
        <div key={i} className="row between small-text"><span>{i + 1}. {it.label || `Clip #${it.clip_id}`} · {fmt(it.range[1] - it.range[0])}</span>
          <button className="btn-quiet small" onClick={() => del(`/edit/${v.clip_id}/sequence/${i}`)}>Remove</button></div>))}
    </div>
  );
}
