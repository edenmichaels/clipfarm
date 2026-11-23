// Multi-track timeline: Video (cuts, speed ramps, freeze frames, appended clips), Text, Stickers, Captions,
// FX (zooms, transitions, layout keyframes, what happened), Music (+ volume keys), Voice keys, SFX, B-roll.
// Everything can be selected; bars and markers can be dragged; the timeline zooms (Ctrl+wheel, + / -).
import { useEffect, useMemo, useRef, useState } from "react";
import { fmt, keptOf, toOut, toSrc, type Edl, type View } from "./EditorParts";

export type Sel = { kind: string; i: number; a?: number; b?: number } | null;

const TRACKS = ["Video", "Text", "Stickers", "Captions", "FX", "Music", "Voice", "SFX", "B-roll"];

export function Timeline({ v, t, seek, splits, sel, setSel, onEdl, onRange, zoom, setZoom }: {
  v: View; t: number; seek: (x: number) => void; splits: number[]; sel: Sel; setSel: (s: Sel) => void;
  onEdl: (fn: (e: Edl) => Edl, label: string) => void; onRange: (a: number, b: number) => void;
  zoom: number; setZoom: (z: number) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const area = useRef<HTMLDivElement>(null);
  const [vw, setVw] = useState(800);
  const [drag, setDrag] = useState<any>(null);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setVw(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const e: Edl = v.edl;
  const segs = keptOf(v);
  const dur = Math.max(0.1, v.duration || 0);
  // zoom = 0 means "fit": the whole clip fills the visible width
  const px = zoom > 0 ? zoom : Math.max(4, (vw - 8) / dur);
  const width = Math.max(vw - 8, dur * px);
  const xOf = (out: number) => out * px;
  const outAt = (clientX: number) => {
    const r = area.current!.getBoundingClientRect();
    return Math.max(0, Math.min(dur, (clientX - r.left) / px));
  };
  // keep the playhead in view while playing
  useEffect(() => {
    const el = scroller.current;
    if (!el || zoom <= 0) return;
    const x = xOf(t);
    if (x < el.scrollLeft || x > el.scrollLeft + el.clientWidth - 40) el.scrollLeft = Math.max(0, x - 80);
  }, [t, zoom]);

  const parts = useMemo(() => {
    const out: { a: number; b: number; seg: any; si: number }[] = [];
    segs.forEach((s, si) => {
      const cuts = splits.filter((x) => x > s.out_a + 0.05 && x < s.out_b - 0.05).sort((x, y) => x - y);
      let s0 = s.out_a;
      for (const c of cuts) { out.push({ a: s0, b: c, seg: s, si }); s0 = c; }
      out.push({ a: s0, b: s.out_b, seg: s, si });
    });
    return out;
  }, [v.segments, v.kept, splits]);

  const marks = (list: any[], key = "t") => (list || []).map((x: any, i: number) => ({ i, x, out: toOut(segs, x[key]) }))
    .filter((m: any) => m.out != null);
  const kfs = marks(e.layout?.keyframes).map((k: any) => (k.i === 0 ? { ...k, out: k.out ?? 0 } : k));
  const zooms = marks(e.zooms);
  const sfx = marks(e.sfx);
  const evtags = marks(e.moment?.tags);
  const broll = marks(e.broll);
  const m = e.music || {};
  const musicOn = m.enabled && m.path && !e.rules?.no_music;
  const keys = e.audio_keys || {};

  const live = (kind: string, i: number, out: number) => (drag?.kind === kind && drag.i === i ? out + (drag.out - drag.start) : out);
  const liveAB = (kind: string, i: number, L: any): [number, number] => {
    if (drag?.kind !== kind || drag.i !== i) return [L.start, L.end];
    const d = drag.out - drag.start;
    if (drag.edge === "l") return [Math.min(L.end - 0.2, L.start + d), L.end];
    if (drag.edge === "r") return [L.start, Math.max(L.start + 0.2, L.end + d)];
    return [L.start + d, L.end + d];
  };
  const onMove = (ev: { clientX: number }) => { if (drag) setDrag({ ...drag, out: outAt(ev.clientX) }); };
  const onUp = () => {
    if (!drag) return;
    const o = drag.out, d = o - drag.start;
    const k = drag.kind, i = drag.i;
    if (Math.abs(d) < 0.02 && k !== "seek" && k !== "start" && k !== "end") { setDrag(null); return; }
    if (k === "start") onRange(o <= 0.01 ? e.range[0] - (drag.start - o) : toSrc(segs, o), e.range[1]);
    else if (k === "end") onRange(e.range[0], o >= dur - 0.01 ? e.range[1] + (o - dur) : toSrc(segs, o));
    else if (k === "kf") onEdl((x) => { x.layout.keyframes[i].t = +toSrc(segs, drag.orig + d).toFixed(3); x.layout.keyframes.sort((p: any, q: any) => p.t - q.t); return x; }, "Move layout keyframe");
    else if (k === "zoom") onEdl((x) => { x.zooms[i].t = +toSrc(segs, drag.orig + d).toFixed(3); return x; }, "Move zoom");
    else if (k === "sfx") onEdl((x) => { x.sfx[i].t = +toSrc(segs, drag.orig + d).toFixed(3); return x; }, "Move sound effect");
    else if (k === "broll") onEdl((x) => { x.broll[i].t = +toSrc(segs, drag.orig + d).toFixed(3); return x; }, "Move B-roll");
    else if (k === "trans") onEdl((x) => { x.transitions[i].t = +Math.max(0, drag.orig + d).toFixed(3); return x; }, "Move transition");
    else if (k === "mkey" || k === "vkey") {
      const track = k === "mkey" ? "music" : "speech";
      onEdl((x) => { x.audio_keys[track][i].t = +Math.max(0, drag.orig + d).toFixed(3); x.audio_keys[track].sort((p: any, q: any) => p.t - q.t); return x; }, "Move volume key");
    } else if (k === "text" || k === "sticker") {
      const list = k === "text" ? "text_layers" : "stickers";
      onEdl((x) => {
        const L = x[list][i];
        const len = L.end - L.start;
        if (drag.edge === "l") L.start = +Math.max(0, Math.min(L.end - 0.2, drag.orig + d)).toFixed(3);
        else if (drag.edge === "r") L.end = +Math.min(dur, Math.max(L.start + 0.2, drag.orig + d)).toFixed(3);
        else { L.start = +Math.max(0, Math.min(dur - len, drag.orig + d)).toFixed(3); L.end = +(L.start + len).toFixed(3); }
        return x;
      }, k === "text" ? "Move text" : "Move sticker");
    } else if (k === "seek") seek(o);
    setDrag(null);
  };
  const start = (kind: string, extra: any = {}) => (ev: { clientX: number; stopPropagation: () => void }) => {
    ev.stopPropagation();
    const o = outAt(ev.clientX);
    setDrag({ kind, out: o, start: o, ...extra });
    if (kind === "seek") seek(o);
  };
  const isSel = (kind: string, i: number) => sel?.kind === kind && sel.i === i;
  const pick = (kind: string, i: number, extra: any = {}) => (ev: any) => { ev.stopPropagation(); setSel({ kind, i, ...extra }); };

  const step = px > 80 ? 1 : px > 30 ? 2 : px > 12 ? 5 : px > 5 ? 15 : 30;
  const ticks: number[] = [];
  for (let s = 0; s <= dur; s += step) ticks.push(s);

  return (
    <div className="tl" onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}
      onWheel={(ev) => { if (ev.ctrlKey) { ev.preventDefault(); setZoom(Math.max(4, Math.min(400, (zoom || px) * (ev.deltaY < 0 ? 1.25 : 0.8)))); } }}>
      <div className="tl-head">
        <span className="muted tiny">{fmt(t)} / {fmt(dur)}</span>
        <span className="grow" />
        <button className="btn-quiet small" onClick={() => setZoom(0)} title="Fit the whole clip">Fit</button>
        <button className="btn-quiet small" onClick={() => setZoom(Math.max(4, (zoom || px) * 0.8))} aria-label="Zoom out" title="Zoom out (-)">−</button>
        <input type="range" min={4} max={400} value={Math.round(zoom || px)} onChange={(ev) => setZoom(+ev.target.value)} aria-label="Timeline zoom" />
        <button className="btn-quiet small" onClick={() => setZoom(Math.min(400, (zoom || px) * 1.25))} aria-label="Zoom in" title="Zoom in (+)">+</button>
      </div>
      <div className="tl-body">
        <div className="tl-labels"><span className="tl-ruler-l" />{TRACKS.map((l) => <span key={l}>{l}</span>)}</div>
        <div className="tl-scroll" ref={scroller}>
          <div className="tl-area" ref={area} style={{ width }} onMouseDown={start("seek")}>
            <div className="tl-ruler">{ticks.map((s) => <span key={s} style={{ left: xOf(s) }}>{fmt(s)}</span>)}</div>
            {/* Video */}
            <div className="tl-track video">
              {parts.map((p, n) => {
                const s = p.seg;
                const on = sel?.kind === "part" && Math.abs((sel.a ?? -1) - p.a) < 1e-3;
                return (
                  <div key={n} className={`seg${s.item ? " item" : ""}${s.hold ? " hold" : ""}${s.speed !== 1 ? " speed" : ""}${on ? " on" : ""}`}
                    style={{ left: xOf(p.a), width: Math.max(2, xOf(p.b) - xOf(p.a)) }}
                    onMouseDown={(ev) => { ev.stopPropagation(); setSel(on ? null : { kind: "part", i: p.si, a: p.a, b: p.b }); seek(p.a); }}
                    title={`${fmt(p.a)}–${fmt(p.b)}${s.hold ? " · freeze frame" : ""}${s.speed !== 1 ? ` · ${s.speed}× speed` : ""}${s.item ? ` · appended clip ${s.item}` : ""}`}>
                    {s.hold ? <span>❄ {s.hold.toFixed(1)}s</span> : s.speed !== 1 ? <span>{s.speed}×</span> : s.item ? <span>+{s.item}</span> : null}
                  </div>);
              })}
              <div className="handle l" onMouseDown={start("start")} title="Drag to change the start" />
              <div className="handle r" style={{ left: xOf(dur) - 6 }} onMouseDown={start("end")} title="Drag to change the end" />
            </div>
            {/* Text layers */}
            <div className="tl-track text">
              {(e.text_layers || []).map((L: any, i: number) => {
                const [a, b] = liveAB("text", i, L);
                return (
                  <div key={L.id || i} className={isSel("text", i) ? "bar on" : "bar"} style={{ left: xOf(a), width: Math.max(6, xOf(b) - xOf(a)) }}
                    onMouseDown={(ev) => { pick("text", i)(ev); start("text", { i, orig: L.start })(ev); }} title={L.text}>
                    <i className="edge l" onMouseDown={(ev) => { pick("text", i)(ev); start("text", { i, edge: "l", orig: L.start })(ev); }} />
                    <span>T {L.text}</span>
                    <i className="edge r" onMouseDown={(ev) => { pick("text", i)(ev); start("text", { i, edge: "r", orig: L.end })(ev); }} />
                  </div>);
              })}
            </div>
            {/* Stickers */}
            <div className="tl-track stickers">
              {(e.stickers || []).map((L: any, i: number) => {
                const [a, b] = liveAB("sticker", i, L);
                return (
                  <div key={L.id || i} className={isSel("sticker", i) ? "bar on" : "bar"} style={{ left: xOf(a), width: Math.max(6, xOf(b) - xOf(a)) }}
                    onMouseDown={(ev) => { pick("sticker", i)(ev); start("sticker", { i, orig: L.start })(ev); }}>
                    <i className="edge l" onMouseDown={(ev) => { pick("sticker", i)(ev); start("sticker", { i, edge: "l", orig: L.start })(ev); }} />
                    <span>{L.emoji || "🖼"}</span>
                    <i className="edge r" onMouseDown={(ev) => { pick("sticker", i)(ev); start("sticker", { i, edge: "r", orig: L.end })(ev); }} />
                  </div>);
              })}
            </div>
            {/* Captions */}
            <div className="tl-track caps">
              {(v.words_out || []).filter((x: any) => x.out != null && !x.deleted).map((x: any) => (
                <div key={x.i} className="word" style={{ left: xOf(x.out), width: Math.max(2, ((x.end - x.start) / (segs.find((s) => s.a <= x.start && x.start <= s.b)?.speed || 1)) * px) }} title={x.word} />
              ))}
            </div>
            {/* FX */}
            <div className="tl-track fx">
              {kfs.map((k: any) => (
                <div key={`k${k.i}`} className={isSel("kf", k.i) ? "kf layout on" : "kf layout"} style={{ left: xOf(live("kf", k.i, k.out)) }}
                  title={`Layout: ${k.x.layout}`} onMouseDown={(ev) => { pick("kf", k.i)(ev); if (k.i) start("kf", { i: k.i, orig: k.out })(ev); }}>◆</div>))}
              {zooms.map((z: any) => (
                <div key={`z${z.i}`} className={isSel("zoom", z.i) ? "kf zoom on" : "kf zoom"} style={{ left: xOf(live("zoom", z.i, z.out)) }}
                  title={`Zoom: ${z.x.kind}`} onMouseDown={(ev) => { pick("zoom", z.i)(ev); start("zoom", { i: z.i, orig: z.out })(ev); }}>●</div>))}
              {(e.transitions || []).map((x: any, i: number) => (
                <div key={`t${i}`} className={isSel("trans", i) ? "kf trans on" : "kf trans"} style={{ left: xOf(live("trans", i, x.t)) }}
                  title={`Transition: ${x.kind}`} onMouseDown={(ev) => { pick("trans", i)(ev); start("trans", { i, orig: x.t })(ev); }}>◇</div>))}
              {evtags.map((x: any) => <div key={`e${x.i}`} className="kf evtag" style={{ left: xOf(x.out) }} title={`What happened: ${String(x.x.tag).replace("_", " ")}`}>▼</div>)}
            </div>
            {/* Music + volume keys */}
            <div className="tl-track music">
              {musicOn && <div className="mbar" style={{ left: xOf(Math.max(0, m.delay || 0)), right: 0 }} title={m.title || "Music"}><span>♫ {m.title || "Music"}</span></div>}
              {!musicOn && <span className="muted tiny">{e.rules?.no_music ? "Original audio only (campaign rule)" : "No music"}</span>}
              {(keys.music || []).map((k: any, i: number) => (
                <div key={i} className={isSel("mkey", i) ? "vkey on" : "vkey"} style={{ left: xOf(live("mkey", i, k.t)), top: 14 - Math.max(-30, Math.min(6, k.db)) * 0.3 }}
                  title={`${k.db} dB`} onMouseDown={(ev) => { pick("mkey", i)(ev); start("mkey", { i, orig: k.t })(ev); }} />))}
            </div>
            <div className="tl-track voice">
              {(keys.speech || []).map((k: any, i: number) => (
                <div key={i} className={isSel("vkey", i) ? "vkey on" : "vkey"} style={{ left: xOf(live("vkey", i, k.t)), top: 14 - Math.max(-30, Math.min(6, k.db)) * 0.3 }}
                  title={`${k.db} dB`} onMouseDown={(ev) => { pick("vkey", i)(ev); start("vkey", { i, orig: k.t })(ev); }} />))}
            </div>
            <div className="tl-track sfx">{sfx.map((x: any) => (
              <div key={x.i} className={isSel("sfx", x.i) ? "kf sfx on" : "kf sfx"} style={{ left: xOf(live("sfx", x.i, x.out)) }} title={x.x.id}
                onMouseDown={(ev) => { pick("sfx", x.i)(ev); start("sfx", { i: x.i, orig: x.out })(ev); }}>♪</div>))}</div>
            <div className="tl-track broll">{broll.map((x: any) => (
              <div key={x.i} className={isSel("broll", x.i) ? "bar on" : "bar"} style={{ left: xOf(live("broll", x.i, x.out)), width: (x.x.dur || 2) * px }}
                onMouseDown={(ev) => { pick("broll", x.i)(ev); start("broll", { i: x.i, orig: x.out })(ev); }}><span>B-roll</span></div>))}</div>
            <div className="playhead" style={{ left: xOf(drag?.kind === "seek" ? drag.out : t) }} />
            {splits.map((s) => <div key={s} className="split" style={{ left: xOf(s) }} />)}
            {drag && (drag.kind === "start" || drag.kind === "end") && <div className="ghost" style={{ left: xOf(drag.out) }} />}
          </div>
        </div>
      </div>
    </div>
  );
}
