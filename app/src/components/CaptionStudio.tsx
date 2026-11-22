// Review › caption studio: preview the captions on the actual clip, nudge them up/down, fix a mistranscribed
// word, switch preset, then re-render (only captions/hook/music are redone, from the clip's base video).
// Also: moment type, thumbs up/down with "why?" tags, music (and "original audio only"), trending sound
// suggestions and the "for trending sound" export.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, MOMENT_EMOJI, waitForJob } from "../api";

type Preset = { key: string; label: string; font: string; font_file: string; text: string; highlight: string;
  outline: string; outline_px: number; uppercase: boolean };

let fontsLoaded = false;
function loadFonts(presets: Preset[]) {
  if (fontsLoaded || !presets.length) return;
  fontsLoaded = true;
  const css = presets.map((p) => `@font-face{font-family:"CF ${p.font}";src:url("${api.fontUrl(p.font_file)}")}`).join("");
  const el = document.createElement("style");
  el.textContent = css;
  document.head.appendChild(el);
}

export default function CaptionStudio({ clip, onRendered }: { clip: any; onRendered: () => void }) {
  const [info, setInfo] = useState<any>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [preset, setPreset] = useState("bold_pop");
  const [nudge, setNudge] = useState(0);
  const [words, setWords] = useState<any[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  const [t, setT] = useState(0);
  const [showFinal, setShowFinal] = useState(false);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [dirty, setDirty] = useState(false);
  const [box, setBox] = useState({ w: 300, h: 533 });
  const [stamp, setStamp] = useState(Date.now());
  const vid = useRef<HTMLVideoElement>(null);
  const wrap = useRef<HTMLDivElement>(null);

  const load = () => api.get(`/clips/${clip.id}/edit`).then((r) => {
    setInfo(r); setPreset(r.preset); setNudge(r.nudge || 0); setWords(r.words); setEvents(r.events); setDirty(false);
  }).catch((e) => setMsg(e.message));
  useEffect(() => {
    load();
    api.get("/captions/presets").then((r) => { setPresets(r.presets); loadFonts(r.presets); }).catch(() => {});
  }, [clip.id]);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, [info]);

  // Re-lay out the captions (no render) whenever the nudge, preset or words change.
  useEffect(() => {
    if (!info || !dirty) return;
    const h = setTimeout(() => {
      api.post(`/clips/${clip.id}/caption-preview`, { caption_nudge: nudge, caption_style: preset, words })
        .then((r) => setEvents(r.events)).catch(() => {});
    }, 250);
    return () => clearTimeout(h);
  }, [nudge, preset, words, dirty]);

  const p = presets.find((x) => x.key === preset);
  const scale = box.w / 1080;
  const ev = useMemo(() => events.find((e) => t >= e.start && t < e.end), [events, t]);
  const editable = info?.editable;
  const src = info && (editable && !showFinal ? info.base_path : clip.file_path);

  const rerender = async (extra: any = {}) => {
    setBusy("Re-rendering…"); setMsg("");
    try {
      const { job_id } = await api.post(`/clips/${clip.id}/rerender`,
        { caption_nudge: nudge, caption_style: preset, words, ...extra });
      await waitForJob(job_id);
      setStamp(Date.now()); setShowFinal(true); setMsg("Re-rendered."); await load(); onRendered();
    } catch (e: any) { setMsg(e.message); }
    setBusy("");
  };
  const rate = async (rating: number, tags?: string[]) => {
    try {
      const r = await api.post(`/clips/${clip.id}/feedback`, { rating, tags: tags ?? info?.feedback?.tags ?? [] });
      setInfo({ ...info, feedback: { rating, tags: tags ?? info?.feedback?.tags ?? [] } });
      setMsg(r.refit.length ? "Thanks! Highlight weights updated from your ratings." :
        `Thanks! ${Math.max(0, r.needed - r.ratings)} more ratings until ClipFarm learns your taste.`);
    } catch (e: any) { setMsg(e.message); }
  };
  const toggleTag = (tag: string) => {
    const cur: string[] = info?.feedback?.tags || [];
    rate(info?.feedback?.rating || -1, cur.includes(tag) ? cur.filter((x) => x !== tag) : [...cur, tag]);
  };
  const exportTrending = async () => {
    setBusy("Exporting…");
    try {
      const { job_id } = await api.post(`/clips/${clip.id}/export-for-trending`);
      await waitForJob(job_id);
      await load(); setMsg("Saved a version with no ClipFarm music. Add the trending sound in TikTok/Instagram.");
    } catch (e: any) { setMsg(e.message); }
    setBusy("");
  };

  if (!info) return <div className="preview" />;
  return (
    <div className="studio">
      <div className="preview" ref={wrap}>
        {src ? <video ref={vid} key={src + stamp} src={api.mediaUrl(src) + `&v=${stamp}`} controls
          onTimeUpdate={(e) => setT((e.target as HTMLVideoElement).currentTime)} /> :
          <div className="center muted" style={{ height: "100%" }}>File was cleaned up</div>}
        {editable && !showFinal && ev && p && (
          <div className="cap-layer" aria-hidden="true">
            {ev.rows.map((row: string[], li: number) => (
              <div key={li} className="cap-line" style={{
                top: (ev.y - (ev.rows.length * ev.line_h) / 2 + li * ev.line_h) * scale,
                fontFamily: `"CF ${p.font}", Impact, sans-serif`, fontSize: ev.em * scale, lineHeight: `${ev.line_h * scale}px`,
                WebkitTextStroke: `${(p.outline_px * 2) * scale}px ${p.outline}`, paintOrder: "stroke fill",
              }}>
                {row.map((w, wi) => <span key={wi} style={{ color: ev.hi[li][wi] ? p.highlight : p.text }}>{w}{wi < row.length - 1 ? " " : ""}</span>)}
              </div>))}
          </div>)}
      </div>
      <div className="stack tight grow" style={{ flexBasis: 240 }}>
        {info.moment_type && (
          <div className="row" style={{ gap: 8 }}>
            <span className="pill type">{MOMENT_EMOJI[info.moment_type] || ""} {info.moment_type}</span>
            {info.moment_reason && <span className="muted small-text">{info.moment_reason}</span>}
          </div>)}
        <div className="row" style={{ gap: 6 }}>
          <span className="muted">Good pick?</span>
          <button className={info.feedback?.rating === 1 ? "seg on" : "seg"} onClick={() => rate(1)} aria-label="Thumbs up">👍</button>
          <button className={info.feedback?.rating === -1 ? "seg on" : "seg"} onClick={() => rate(-1)} aria-label="Thumbs down">👎</button>
        </div>
        {info.feedback && (
          <div className="row" style={{ gap: 6 }}>{info.feedback_tags.map((tg: string) => (
            <button key={tg} className={info.feedback.tags.includes(tg) ? "chip on" : "chip"} onClick={() => toggleTag(tg)}>{tg}</button>))}
          </div>)}

        {editable ? <>
          <div className="row between">
            <b>Captions</b>
            <button className="link-btn" onClick={() => setShowFinal(!showFinal)}>{showFinal ? "Edit captions" : "Show finished clip"}</button>
          </div>
          <div className="row" style={{ gap: 6 }}>{presets.map((x) => (
            <button key={x.key} className={preset === x.key ? "pill-btn on" : "pill-btn"} onClick={() => { setPreset(x.key); setDirty(true); }}>{x.label}</button>))}</div>
          <label className="line">Move captions
            <span className="row" style={{ gap: 6 }}>
              <button onClick={() => { setNudge(nudge - 20); setDirty(true); }} aria-label="Move up">▲</button>
              <span className="muted" style={{ minWidth: 54, textAlign: "center" }}>{nudge > 0 ? `+${nudge}` : nudge} px</span>
              <button onClick={() => { setNudge(nudge + 20); setDirty(true); }} aria-label="Move down">▼</button>
            </span>
          </label>
          <details>
            <summary>Fix a word ({words.length})</summary>
            <div className="words">{words.map((w, i) => (
              <input key={i} value={w.word} aria-label={`Word ${i + 1}`} className={ev && Math.abs(w.start - info.offset - ev.start) < 0.01 ? "on" : ""}
                onFocus={() => { if (vid.current) vid.current.currentTime = Math.max(0, w.start - info.offset - 0.3); }}
                onChange={(e) => { const nw = [...words]; nw[i] = { ...w, word: e.target.value }; setWords(nw); setDirty(true); }} />))}
            </div>
          </details>
          <div className="row" style={{ gap: 8 }}>
            <button onClick={() => rerender()} disabled={!!busy || !dirty}>{busy || "Re-render captions"}</button>
            {dirty && <button className="btn-quiet" onClick={load}>Undo</button>}
          </div>
        </> : <span className="muted small-text">Captions on this clip can't be edited (it was made before v0.4).</span>}

        <b>Music</b>
        {info.original_audio_only ? <span className="muted">Original audio only.</span> :
          info.music ? <span className="small-text">{info.music.title} · {info.music.plan?.mode} · <span className="muted">{info.music.licence}</span></span> :
          <span className="muted small-text">No music (campaign rules or your settings).</span>}
        {editable && info.music && !info.original_audio_only &&
          <div><button className="btn-quiet" disabled={!!busy} onClick={() => rerender({ original_audio_only: true })}>Use original audio only</button></div>}

        <b>Trending sound (add it in the app when posting)</b>
        {info.trending.length ? info.trending.map((s: any) => (
          <span key={s.id} className="small-text">♪ {s.name}{s.artist ? ` – ${s.artist}` : ""} <span className="muted">({s.platform})</span></span>)) :
          <span className="muted small-text">Add trending sound names in Settings › Music to get suggestions.</span>}
        {editable && <div><button className="btn-quiet" disabled={!!busy} onClick={exportTrending}>
          {info.nomusic_path ? "Re-export for trending sound" : "Export for trending sound"}</button></div>}
        {info.nomusic_path && <span className="muted small-text">Saved: {info.nomusic_path}</span>}
        {msg && <span className="small-text ok-text">{msg}</span>}
      </div>
    </div>
  );
}
