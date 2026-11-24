// The Editor is the app (v0.8.3): a full video editor, opened at start-up.
//   top     toolbar: undo/redo, tools (split, freeze, text, transition), auto-edit, fix, variants, rating, export
//   left    Bin (your clips, filters, batch) + Styles, Music, SFX, B-roll, Stickers
//   centre  9:16 preview drawn by the export renderer; drag text, stickers and captions right on it
//   right   inspector: whatever is selected, then the clip's Captions, Title, Layout & facecam, Colour, Audio, ...
//   bottom  multi-track timeline (zoomable)
// Every change is saved as a new version straight away (undo/redo); where you were is restored after a restart
// or a crash, including text you were still typing.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, aud, MOMENT_EMOJI, PLATFORM_LABEL, waitForJob } from "../api";
import type { Page } from "../App";
import { StylesPanel } from "../components/Styles";
import { PresetBar, Variations } from "../components/EditorPresets";
import {
  CaptionsPanel, EffectsPanel, HookPanel, LayoutPanel, MusicPanel, Preview, RulesPanel, TranscriptPanel,
  fmt, keptOf, segAt, toSrc, useCaptionFonts,
} from "../components/EditorParts";
import { Timeline, type Sel } from "../components/EditorTimeline";
import {
  AudioKeysPanel, ColourPanel, FacecamEditor, SelectionInspector, SequencePanel, newSticker, newText,
} from "../components/EditorInspector";

const STATUSES = ["new", "edited", "ready", "posted"];
export const TAG_LABEL: Record<string, string> = { explosion: "Explosion", crash: "Crash", gunfire: "Gunfire",
  police: "Police / wanted / arrest", death: "Death / wasted", stunt: "Stunt / flip / jump", fall: "Fall / ragdoll",
  npc: "NPC fight / reaction", vehicle_steal: "Vehicle steal", plane: "Plane / heli", boat: "Boat", chase: "Chase",
  fail: "Fail / glitch", easter_egg: "Easter egg / animal / myth", money: "Money / heist" };
const LEFT = ["Bin", "Styles", "Music", "SFX", "B-roll", "Stickers"];
const RIGHT = ["Clip", "Captions", "Title", "Layout", "Colour", "Audio", "Effects", "Transcript", "Rules", "Sequence"];
const FEEDBACK_TAGS = ["not funny", "too slow", "cut too early", "cut too late", "boring game part", "wrong moment",
  "bad captions", "wrong music", "bad framing", "great moment", "great timing"];
export const SHORTCUTS: [string, string][] = [
  ["Space", "Play / pause"], ["J / K / L", "Back 2 s / stop / play faster"], ["← / →", "One frame back / forward"],
  ["Shift + ← / →", "One second back / forward"], ["Home / End", "Start / end"], ["S", "Split at the playhead"],
  ["Delete", "Delete the selected part or layer"], ["F", "Freeze frame at the playhead (1 s)"], ["T", "Add text at the playhead"],
  ["Shift + T", "Add a transition at the playhead"], ["Ctrl + D", "Duplicate the selected text or sticker"],
  ["Ctrl + Z / Ctrl + Y", "Undo / redo"], ["Ctrl + E", "Export"], ["+ / − / 0", "Timeline zoom in / out / fit"],
  ["1 – 6", "Left panels (Bin, Styles, Music, SFX, B-roll, Stickers)"], ["Esc", "Deselect"], ["?", "This list"],
];

function readPref(k: string, d: string) { try { return localStorage.getItem(k) ?? d; } catch { return d; } }
function writePref(k: string, v: string) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } }
export function scoreClass(v?: number | null) { return v == null ? "" : v >= 90 ? "hot" : v >= 70 ? "good" : v >= 50 ? "ok" : "low"; }

export default function Editor({ go, initialClip }: { go: (p: Page, tab?: string) => void; initialClip?: number }) {
  const [sort, setSort] = useState(readPref("ed.sort", "profit"));
  const [top, setTop] = useState(false);
  const [filters, setFilters] = useState<any>({ program_id: "", streamer_id: "", moment_type: "", status: "", tag: "", reaction: "" });
  const [clips, setClips] = useState<any[]>([]);
  const [all, setAll] = useState<any[]>([]);
  const [selId, setSelId] = useState<number | null>(initialClip ?? null);
  const [session, setSession] = useState<any>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [msg, setMsg] = useState("");
  useEffect(() => { if (initialClip) setSelId(initialClip); }, [initialClip]);
  // reopen where you were (after a restart or a crash)
  useEffect(() => { api.get("/editor/session").then((s) => { setSession(s); if (!initialClip && s?.clip_id) setSelId(s.clip_id); }).catch(() => setSession({})); }, []);

  const load = useCallback(() => {
    const q = new URLSearchParams({ sort });
    if (top) q.set("min_score", "90");
    Object.entries(filters).forEach(([k, v]) => v && q.set(k, String(v)));
    return api.get(`/editor/clips?${q}`).then((c: any[]) => {
      setClips(c);
      setSelId((cur) => (cur != null && (c.some((x) => x.id === cur) || all.some((x) => x.id === cur)) ? cur : c[0]?.id ?? null));
    }).catch((e) => setMsg(e.message));
  }, [sort, top, filters]);
  useEffect(() => { load(); writePref("ed.sort", sort); }, [load]);
  useEffect(() => { api.get("/editor/clips?sort=newest").then(setAll).catch(() => {}); }, [clips.length]);

  const opts = useMemo(() => {
    const uniq = (k: string, l: string) => Array.from(new Map(all.filter((c) => c[k]).map((c) => [c[k], c[l]])).entries());
    return { programs: uniq("program_id", "program"), streamers: uniq("streamer_id", "streamer"),
      types: Array.from(new Set(all.map((c) => c.moment_type).filter(Boolean))) as string[] };
  }, [all]);
  const batch = async (action: string, extra: any = {}) => {
    try {
      const r = await api.post("/editor/batch", { ids: picked, action, ...extra });
      setMsg(`${r.done.length} clip(s) done${r.failed.length ? `, ${r.failed.length} failed` : ""}${r.jobs.length ? ` · ${r.jobs.length} export(s) running` : ""}`);
      setPicked([]); load();
    } catch (e: any) { setMsg(e.message); }
  };
  const sel = clips.find((c) => c.id === selId) || all.find((c) => c.id === selId) || null;

  const bin = (append?: (id: number) => void) => (
    <div className="stack tight bin">
      <div className="row wrap" style={{ gap: 6 }}>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
          <option value="profit">Most A$ first</option><option value="virality">Highest score first</option><option value="newest">Newest</option></select>
        <button className={top ? "pill-btn on small" : "pill-btn small"} onClick={() => setTop(!top)} title="Only clips scoring 90 or more">90+ only</button>
      </div>
      <details className="bin-filters">
        <summary>Filters{Object.values(filters).some(Boolean) ? " (on)" : ""}</summary>
        <div className="stack tight">
          <select value={filters.program_id} onChange={(e) => setFilters({ ...filters, program_id: e.target.value })} aria-label="Campaign">
            <option value="">All campaigns</option>{opts.programs.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</select>
          <select value={filters.streamer_id} onChange={(e) => setFilters({ ...filters, streamer_id: e.target.value })} aria-label="Streamer">
            <option value="">All streamers</option>{opts.streamers.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</select>
          <select value={filters.moment_type} onChange={(e) => setFilters({ ...filters, moment_type: e.target.value })} aria-label="Moment type">
            <option value="">All moments</option>{opts.types.map((t) => <option key={t} value={t}>{MOMENT_EMOJI[t] || ""} {t}</option>)}</select>
          <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} aria-label="Status">
            <option value="">Any status</option>{STATUSES.map((s) => <option key={s}>{s}</option>)}</select>
          <select value={filters.tag} onChange={(e) => setFilters({ ...filters, tag: e.target.value })} aria-label="What happened">
            <option value="">Any event</option>{Object.entries(TAG_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <select value={filters.reaction} onChange={(e) => setFilters({ ...filters, reaction: e.target.value })} aria-label="Reaction">
            <option value="">Any reaction</option><option value="laugh">…streamer laughing</option><option value="scream">…streamer screaming</option>
            <option value="chat_laugh">…chat laughing</option><option value="disbelief">…"no way" / "bro"</option></select>
        </div>
      </details>
      {picked.length > 0 && (
        <div className="ed-batch">
          <b>{picked.length} selected</b>
          <select defaultValue="" onChange={(e) => e.target.value && batch("template", { template: e.target.value })} aria-label="Apply template">
            <option value="">Caption template…</option>{["bold_pop", "hype_yellow", "clean_white", "minimal"].map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</select>
          <button onClick={() => batch("captions")}>Regenerate captions</button>
          <button onClick={() => batch("fix")}>Fix my clips</button>
          <button className="btn-primary" onClick={() => batch("export")}>Export all</button>
          <button className="btn-danger" onClick={() => batch("delete")}>Delete</button>
          <button onClick={() => setPicked([])}>Clear</button>
        </div>)}
      {clips.length === 0 && <p className="muted small-text">{top ? "No 90+ clips yet." : "No clips yet. New clips show up here as projects run (New Project)."}</p>}
      <div className="ed-cards">
        {clips.map((c) => (
          <div key={c.id} className={c.id === selId ? "ed-card on" : "ed-card"} onClick={() => setSelId(c.id)}>
            <input type="checkbox" aria-label="Select for batch" checked={picked.includes(c.id)} onClick={(e) => e.stopPropagation()}
              onChange={() => setPicked(picked.includes(c.id) ? picked.filter((x) => x !== c.id) : [...picked, c.id])} />
            <div className="thumb">{c.thumb_path && <img src={api.mediaUrl(c.thumb_path)} alt="" />}</div>
            <div className="stack tight grow" style={{ minWidth: 0 }}>
              <b className="ellipsis">{c.moment_type ? `${MOMENT_EMOJI[c.moment_type] || ""} ` : ""}{c.title}</b>
              {(c.moment_tags || []).length > 0 && <span className="tagrow">{c.moment_tags.slice(0, 3).map((t: string) => <span key={t} className="tagchip">{TAG_LABEL[t]?.split(" / ")[0] || t}</span>)}</span>}
              <span className="muted tiny ellipsis">{c.streamer}{c.program ? ` · ${c.program}` : ""}</span>
              <div className="row" style={{ gap: 6 }}>
                <span className={`score ${scoreClass(c.virality)}`} title={c.virality_reason || ""}>{c.virality ?? "–"}</span>
                <span className="money" title="Projected earnings (likely)">{c.profit_likely != null ? aud(c.profit_likely) : "no campaign"}</span>
                {!c.rules_ok && <span className="flag" title="Breaks a campaign rule">⚑</span>}
                <span className={`pill ${c.status}`}>{c.status}</span>
                {append && c.id !== selId && <button className="btn-quiet small" title="Append this clip after the open one" onClick={(e) => { e.stopPropagation(); append(c.id); }}>+ Append</button>}
              </div>
            </div>
          </div>))}
      </div>
    </div>
  );

  if (session == null) return <div className="studio-loading muted">Opening the Editor…</div>;
  return sel
    ? <Studio key={sel.id} clip={sel} bin={bin} onChanged={load} go={go} msg={msg} setMsg={setMsg} session={session} />
    : (
      <div className="studio empty-studio">
        <aside className="st-left card">{bin()}</aside>
        <section className="card st-empty">
          <h2>Editor</h2>
          <p className="muted">{clips.length ? "Pick a clip in the Bin." : "Start a project (New Project) and its clips open here, ready to edit."}</p>
          <button className="btn-primary" onClick={() => go("new")}>New Project</button>
        </section>
      </div>
    );
}

function Studio({ clip, bin, onChanged, go, msg, setMsg, session }: {
  clip: any; bin: (append?: (id: number) => void) => any; onChanged: () => void; go: (p: Page, t?: string) => void;
  msg: string; setMsg: (m: string) => void; session: any;
}) {
  const cid = clip.id;
  const [v, setV] = useState<any>(null);
  const [score, setScore] = useState<any>(null);
  const [buildingNow, setBuilding] = useState(false);
  const [left, setLeft] = useState(session?.clip_id === cid && LEFT.includes(session?.panel) ? session.panel : "Bin");
  const [right, setRight] = useState(session?.clip_id === cid && RIGHT.includes(session?.inspector) ? session.inspector : "Clip");
  const [t, setT] = useState(session?.clip_id === cid ? +(session.t || 0) : 0);
  const [zoom, setZoom] = useState(session?.clip_id === cid ? +(session.zoom || 0) : 0);
  const [zone, setZone] = useState<string | null>(readPref("ed.zone2", "") || null);
  const [busy, setBusy] = useState("");
  const [saved, setSaved] = useState<"saved" | "saving" | "error">("saved");
  const [splits, setSplits] = useState<number[]>([]);
  const [sel, setSel] = useState<Sel>(null);
  const [preset, setPreset] = useState("tiktok");
  const [templates, setTemplates] = useState<any>(null);
  const [tracks, setTracks] = useState<any[]>([]);
  const [brands, setBrands] = useState<any[]>([]);
  const [rated, setRated] = useState<number | null>(null);
  const [tags, setTags] = useState<string[]>([]);
  const [loadErr, setLoadErr] = useState("");
  const [sheet, setSheet] = useState(false);
  const [restore, setRestore] = useState<any>(session?.draft?.clip_id === cid ? session.draft : null);
  const fonts = useCaptionFonts();
  const vid = useRef<HTMLVideoElement>(null);
  const building = useRef({ current: false, again: false }).current;

  const rebuild = useCallback(async () => {
    if (building.current) { building.again = true; return; }
    building.current = true;
    setBuilding(true);
    try {
      do {
        building.again = false;
        const { job_id } = await api.post(`/edit/${cid}/preview`);
        await waitForJob(job_id);
      } while (building.again);
      setV(await api.get(`/edit/${cid}`));
    } catch (e: any) { setMsg(`Preview: ${e.message}`); }
    finally { building.current = false; setBuilding(false); }
  }, [cid]);
  const rescore = () => api.post(`/edit/${cid}/score`).then(setScore).catch(() => {});
  const show = (r: any) => { setV(r); rescore(); if (!r.proxy) rebuild(); onChanged(); };
  const [vars, setVars] = useState<any[]>([]);
  const loadVars = () => api.get(`/edit/${cid}/variations`).then(setVars).catch(() => {});
  useEffect(() => { setVars([]); loadVars(); }, [cid]);
  useEffect(() => {
    api.get(`/edit/${cid}`).then((r) => { setV(r); setPreset(r.edl.export?.preset || "tiktok"); if (!r.proxy) rebuild(); }).catch((e) => setLoadErr(e.message));
    rescore();
    api.get("/caption-templates").then(setTemplates).catch(() => {});
    api.get("/music/tracks").then((r) => setTracks(r.tracks || [])).catch(() => {});
    api.get("/brand-templates").then((r) => setBrands(r.templates || [])).catch(() => {});
  }, [cid]);
  useEffect(() => writePref("ed.zone2", zone || ""), [zone]);
  // remember where you are (debounced): reopened after a restart or crash
  useEffect(() => {
    const id = window.setTimeout(() => api.put("/editor/session", { clip_id: cid, t: +t.toFixed(2), panel: left, inspector: right, zoom }).catch(() => {}), 1200);
    return () => window.clearTimeout(id);
  }, [cid, Math.round(t), left, right, zoom]);

  const run = async (label: string, p: Promise<any>) => {
    setBusy(label);
    try { const r = await p; if (r?.edl) show(r); if (r?.changes) setMsg(r.changes.length ? `Done: ${r.changes.join(", ")}` : "Nothing to fix."); return r; }
    catch (e: any) { setMsg(e.message); } finally { setBusy(""); }
  };
  // every change: a new saved version. While it's being saved, the pending edit is kept as a draft so a crash
  // can't lose it.
  const onEdl = (fn: (e: any) => any, label: string) => {
    if (!v) return;
    const next = fn(JSON.parse(JSON.stringify(v.edl)));
    setV({ ...v, edl: next });
    setSaved("saving");
    api.put("/editor/session", { draft: { clip_id: cid, edl: next, label, at: new Date().toISOString() } }).catch(() => {});
    api.put(`/edit/${cid}`, { edl: next, label })
      .then((r) => { show(r); setSaved("saved"); api.put("/editor/session", { draft: null }).catch(() => {}); })
      .catch((e) => { setSaved("error"); setMsg(e.message); });
    api.post(`/edit/${cid}/signal`, { kind: "edit" }).catch(() => {});
  };
  const op = (b: any) => run("Editing", api.post(`/edit/${cid}/transcript`, b));
  const post = (path: string, b: any = {}) => run("Working", api.post(path, b));
  const del = (path: string) => run("Working", api.del(path));
  const undo = () => run("Undo", api.post(`/edit/${cid}/undo`));
  const redo = () => run("Redo", api.post(`/edit/${cid}/redo`));
  const seek = (x: number) => { setT(x); if (vid.current) vid.current.currentTime = x; };
  const fixFlag = (id: string | null) => run("Fixing", api.post(`/edit/${cid}/fix`, id ? { id } : { all: true }));
  const dur = v?.duration || 0;
  const segs = keptOf(v);
  const addText = () => { onEdl((x) => { (x.text_layers ||= []).push(newText(t, dur)); return x; }, "Add text"); setSel({ kind: "text", i: (v.edl.text_layers || []).length }); setRight("Clip"); };
  const addSticker = (s: { emoji?: string; path?: string }) => { onEdl((x) => { (x.stickers ||= []).push(newSticker(t, dur, s)); return x; }, "Add sticker"); setSel({ kind: "sticker", i: (v.edl.stickers || []).length }); };
  const addTransition = () => { onEdl((x) => { (x.transitions ||= []).push({ t: +t.toFixed(2), kind: "fade", dur: 0.4 }); return x; }, "Add transition"); setSel({ kind: "trans", i: (v.edl.transitions || []).length }); };
  const freeze = () => { const s = segAt(segs, t); if (s && !s.item && !s.hold) onEdl((x) => { (x.freezes ||= []).push({ t: +toSrc(segs, t).toFixed(3), dur: 1.0 }); return x; }, "Freeze frame"); };
  const split = () => { if (t > 0.05 && t < dur - 0.05) setSplits((s) => Array.from(new Set([...s, +t.toFixed(3)]))); };
  const deleteSel = () => {
    if (!sel || !v) return;
    if (sel.kind === "part") {
      const a = toSrc(segs, sel.a ?? 0), b = toSrc(segs, sel.b ?? 0);
      onEdl((e) => { e.cuts = [...(e.cuts || []), [+a.toFixed(3), +b.toFixed(3)]]; return e; }, "Delete part");
      setSplits(splits.filter((s) => s < (sel.a ?? 0) || s > (sel.b ?? 0)));
    } else {
      const lists: Record<string, string[]> = { text: ["text_layers"], sticker: ["stickers"], trans: ["transitions"], zoom: ["zooms"],
        sfx: ["sfx"], broll: ["broll"], mkey: ["audio_keys", "music"], vkey: ["audio_keys", "speech"] };
      const path = lists[sel.kind];
      if (!path) return;
      onEdl((e) => { const L = path.length === 1 ? e[path[0]] : e[path[0]][path[1]]; L.splice(sel.i, 1); return e; }, "Delete");
    }
    setSel(null);
  };
  const doExport = async (then?: () => void) => {
    setBusy("Exporting");
    try {
      if (preset !== (v.edl.export?.preset || "tiktok")) await api.put(`/edit/${cid}`, { edl: { ...v.edl, export: { ...v.edl.export, preset } }, label: `Export for ${preset}` });
      const { job_id } = await api.post(`/edit/${cid}/export`, { preset });
      await waitForJob(job_id);
      api.post(`/edit/${cid}/signal`, { kind: "export" }).catch(() => {});
      setMsg(`Exported for ${preset}.`); onChanged();
      then?.();
    } catch (e: any) { setMsg(e.message); } finally { setBusy(""); }
  };
  const sendToPost = () => doExport(async () => { await api.post(`/edit/${cid}/signal`, { kind: "keep" }).catch(() => {}); go("clips", "Post & Submit"); });
  const rate = async (r: number, tg: string[] = tags) => {
    setRated(r);
    try { await api.post(`/clips/${cid}/feedback`, { rating: r, tags: tg }); } catch (e: any) { setMsg(e.message); }
  };

  // keyboard shortcuts (see SHORTCUTS)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tg = (e.target as HTMLElement)?.tagName;
      if (tg === "INPUT" || tg === "TEXTAREA" || tg === "SELECT" || (e.target as HTMLElement)?.isContentEditable) return;
      const el = vid.current;
      const k = e.key, mod = e.ctrlKey || e.metaKey;
      if (mod && k.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
      if (mod && k.toLowerCase() === "y") { e.preventDefault(); redo(); return; }
      if (mod && k.toLowerCase() === "e") { e.preventDefault(); doExport(); return; }
      if (mod && k.toLowerCase() === "d") {
        e.preventDefault();
        if (sel && (sel.kind === "text" || sel.kind === "sticker")) {
          const list = sel.kind === "text" ? "text_layers" : "stickers";
          onEdl((x) => { const L = x[list][sel.i]; x[list].push({ ...L, id: Math.random().toString(36).slice(2, 9), x: Math.min(0.9, L.x + 0.04), y: Math.min(0.9, L.y + 0.04) }); return x; }, "Duplicate");
        }
        return;
      }
      if (mod) return;
      if (k === " ") { e.preventDefault(); if (el) { el.paused ? el.play() : el.pause(); } }
      else if (k === "k" || k === "K") { el?.pause(); if (el) el.playbackRate = 1; }
      else if (k === "l" || k === "L") { if (el) { el.playbackRate = el.paused ? 1 : Math.min(4, el.playbackRate * 2); el.play(); } }
      else if (k === "j" || k === "J") { el?.pause(); seek(Math.max(0, t - 2)); }
      else if (k === "s" || k === "S") split();
      else if (k === "f" || k === "F") freeze();
      else if (k === "t") addText();
      else if (k === "T") addTransition();
      else if (k === "Delete" || k === "Backspace") { if (sel) { e.preventDefault(); deleteSel(); } }
      else if (k === "ArrowLeft") { e.preventDefault(); seek(Math.max(0, t - (e.shiftKey ? 1 : 1 / 30))); }
      else if (k === "ArrowRight") { e.preventDefault(); seek(Math.min(dur, t + (e.shiftKey ? 1 : 1 / 30))); }
      else if (k === "Home") seek(0);
      else if (k === "End") seek(dur);
      else if (k === "+" || k === "=") setZoom((z) => Math.min(400, (z || 20) * 1.25));
      else if (k === "-") setZoom((z) => Math.max(4, (z || 20) * 0.8));
      else if (k === "0") setZoom(0);
      else if (k === "Escape") { setSel(null); setSheet(false); }
      else if (k === "?") setSheet((s) => !s);
      else if (/^[1-6]$/.test(k)) setLeft(LEFT[+k - 1]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!v) return <div className="studio-loading muted">{loadErr || "Loading the edit…"}</div>;
  const vir = score?.virality;
  const pr = score?.profit;
  const flags: any[] = score?.flags || [];

  // on-canvas handles: text, stickers and the caption block can be dragged on the preview
  const overlay = (s: number) => <CanvasLayers v={v} t={t} s={s} sel={sel} setSel={setSel} onEdl={onEdl}
    onPip={(corner, w) => post(`/edit/${cid}/pip`, { corner, w })} />;

  return (
    <div className="studio">
      <header className="st-bar">
        <div className="row" style={{ gap: 4 }}>
          <button onClick={undo} title="Undo (Ctrl+Z)" aria-label="Undo">↶</button>
          <button onClick={redo} title="Redo (Ctrl+Y)" aria-label="Redo">↷</button>
        </div>
        <div className="row st-tools" style={{ gap: 4 }}>
          <button onClick={split} title="Split at the playhead (S)">✂<span className="lbl"> Split</span></button>
          <button onClick={freeze} title="Freeze frame (F)">❄<span className="lbl"> Freeze</span></button>
          <button onClick={addText} title="Add text (T)">T<span className="lbl"> Text</span></button>
          <button onClick={() => setLeft("Stickers")} title="Stickers">☺<span className="lbl"> Sticker</span></button>
          <button onClick={addTransition} title="Transition (Shift+T)">◇<span className="lbl"> Transition</span></button>
          <button onClick={() => post(`/edit/${cid}/effects`, {})} title="Automatic zooms, effects and sounds">✨<span className="lbl"> Effects</span></button>
        </div>
        <span className="sep" />
        <button className="btn-quiet" disabled={!!busy} onClick={() => run("Auto-editing", api.post(`/edit/${cid}/auto-edit`))} title="Rebuild the edit from the automatic version">Auto-edit</button>
        <button className="btn-quiet" disabled={!!busy} onClick={() => run("Fixing", api.post(`/edit/${cid}/fix-my-clip`))} title="Trim dead air, fillers and a long ending, add a hook, fix campaign rules">Fix my clip</button>
        <button className="btn-quiet" disabled={!!busy} onClick={async () => {
          setBusy("Making variants");
          try { const r = await api.post(`/edit/${cid}/variants`, { n: 2 }); setMsg(`Made ${r.variants.map((x: any) => `[${x.label}] ${x.what}`).join(" · ")}`); onChanged(); }
          catch (e: any) { setMsg(e.message); } finally { setBusy(""); }
        }} title="2 versions with a different hook, length and layout, for A/B tests">Variants</button>
        {brands.length > 0 && <select value={v.edl.brand?.id ?? ""} onChange={(e) => post(`/edit/${cid}/brand`, { brand_template_id: e.target.value ? Number(e.target.value) : null })} aria-label="Brand template">
          <option value="">No brand</option>{brands.map((b) => <option key={b.id} value={b.id}>Brand: {b.name}</option>)}</select>}
        <span className="grow" />
        <span className={`saved ${saved}`} role="status" title="Every change is saved as a new version">{busy ? `${busy}…` : saved === "saving" ? "Saving…" : saved === "error" ? "Not saved" : "All changes saved"}</span>
        <button className={rated === 1 ? "pill-btn on" : "pill-btn"} onClick={() => rate(1)} aria-label="Thumbs up">👍</button>
        <button className={rated === -1 ? "pill-btn on" : "pill-btn"} onClick={() => rate(-1)} aria-label="Thumbs down">👎</button>
        <select value={preset} onChange={(e) => setPreset(e.target.value)} aria-label="Export for">
          <option value="tiktok">TikTok</option><option value="shorts">YouTube Shorts</option><option value="reels">Instagram Reels</option>
          <option value="x">X</option><option value="srt">Video + .srt captions</option></select>
        <button className="btn-ghost" disabled={!!busy} onClick={() => doExport()} title="Export (Ctrl+E)">Export</button>
        <button className="btn-primary" disabled={!!busy} onClick={sendToPost}>Send to Post</button>
        <button className="btn-quiet" onClick={() => setSheet(true)} aria-label="Keyboard shortcuts" title="Keyboard shortcuts (?)">⌨</button>
      </header>
      {(msg || restore || rated != null || v.notice || v.edl.layout?.facecam_ask) && (
        <div className="st-msgs">
          {v.notice && <div className="ed-flag"><span>ℹ {v.notice}</span>
            <button className="btn-quiet" onClick={() => api.post(`/edit/${cid}/notice/dismiss`).then(() => setV({ ...v, notice: null }))}>OK</button></div>}
          {v.edl.layout?.facecam_ask && <div className="ed-flag"><span>We're not sure exactly where this streamer's webcam is. Draw it once and every clip of theirs uses it.</span>
            <button onClick={() => setRight("Layout")}>Draw the facecam</button></div>}
          {restore && <div className="ed-flag"><span>Unsaved change from {new Date(restore.at).toLocaleTimeString()} ({restore.label}) was found after ClipFarm closed.</span>
            <button onClick={() => { onEdl(() => restore.edl, `Restored: ${restore.label}`); setRestore(null); }}>Restore it</button>
            <button className="btn-quiet" onClick={() => { setRestore(null); api.put("/editor/session", { draft: null }).catch(() => {}); }}>Discard</button></div>}
          {msg && <span className="muted small-text" role="status">{msg} <button className="btn-quiet small" onClick={() => setMsg("")} aria-label="Dismiss">✕</button></span>}
          {rated != null && <div className="row wrap" style={{ gap: 6 }}><span className="muted tiny">Why?</span>
            {FEEDBACK_TAGS.filter((x) => (rated > 0) === x.startsWith("great")).map((x) => (
              <button key={x} className={tags.includes(x) ? "pill-btn on small" : "pill-btn small"} onClick={() => { const n = tags.includes(x) ? tags.filter((y) => y !== x) : [...tags, x]; setTags(n); rate(rated, n); }}>{x}</button>))}</div>}
        </div>)}

      <aside className="st-left">
        <div className="st-tabs" role="tablist">{LEFT.map((x, i) => (
          <button key={x} role="tab" aria-selected={left === x} className={left === x ? "tab on" : "tab"} onClick={() => setLeft(x)} title={`${x} (${i + 1})`}>{x}</button>))}</div>
        <div className="st-pane">
          {left === "Bin" && bin((id) => post(`/edit/${cid}/sequence`, { clip_id: id }))}
          {left === "Styles" && <StylesPanel compact cid={cid} current={v.edl.style?.key} onApplied={show} />}
          {left === "Music" && <MusicPanel v={v} onEdl={onEdl} tracks={tracks} post={post} />}
          {left === "SFX" && <SfxBin t={t} onAdd={(id) => post(`/edit/${cid}/sfx`, { id, t_out: t })} />}
          {left === "B-roll" && <BrollBin t={t} onAdd={(p) => post(`/edit/${cid}/broll`, { path: p, t_out: t })} go={go} />}
          {left === "Stickers" && <StickerBin onAdd={addSticker} go={go} />}
        </div>
      </aside>

      <section className="st-centre">
        <PresetBar cid={cid} head={v.head} current={v.edl.style?.key} onApplied={show}
          onVariations={async (n, preset) => {
            try { await api.post(`/edit/${cid}/variations`, { n, preset }); setMsg(`Making ${n} variation${n === 1 ? "" : "s"}: quick previews first.`); loadVars(); }
            catch (e: any) { setMsg(e.message); } }} />
        <Preview v={v} videoRef={vid} t={t} setT={setT} zone={zone} building={buildingNow} overlay={overlay} />
        <div className="row wrap st-under" style={{ gap: 6, justifyContent: "center" }}>
          <button onClick={() => seek(Math.max(0, t - 1 / 30))} aria-label="Previous frame">⏮</button>
          <button onClick={() => { const el = vid.current; if (el) { el.paused ? el.play() : el.pause(); } }} aria-label="Play or pause">⏯</button>
          <button onClick={() => seek(Math.min(dur, t + 1 / 30))} aria-label="Next frame">⏭</button>
          <span className="muted tiny">{fmt(t)} / {fmt(dur)}</span>
          {["tiktok", "reels", "shorts"].map((z) => <button key={z} className={zone === z ? "pill-btn on small" : "pill-btn small"} onClick={() => setZone(zone === z ? null : z)}>{z === "tiktok" ? "TikTok" : z === "reels" ? "Reels" : "Shorts"} zone</button>)}
        </div>
        <Variations cid={cid} rows={vars} reload={loadVars} onMsg={setMsg} />
      </section>

      <aside className="st-right">
        <SelectionInspector v={v} sel={sel} setSel={setSel} onEdl={onEdl} t={t} />
        <div className="st-tabs wrap" role="tablist">{RIGHT.map((x) => (
          <button key={x} role="tab" aria-selected={right === x} className={right === x ? "tab on" : "tab"} onClick={() => setRight(x)}>
            {x}{x === "Rules" && flags.length ? ` (${flags.length})` : ""}{x === "Sequence" && (v.edl.sequence || []).length ? ` (${v.edl.sequence.length + 1})` : ""}</button>))}</div>
        <div className="st-pane">
          {right === "Clip" && (
            <div className="stack tight">
              <div className="row" style={{ gap: 12 }}>
                <span className={`score big ${scoreClass(vir?.score)}`}>{vir?.score ?? "–"}</span>
                <div className="stack tight grow"><b>{vir?.reason || "Scoring…"}</b>
                  <span className="muted tiny">{v.edl.moment?.type ? `${MOMENT_EMOJI[v.edl.moment.type] || ""} ${v.edl.moment.type}` : ""}{v.edl.style?.name ? ` · Style: ${v.edl.style.name}` : ""}</span></div>
              </div>
              <div className="row wrap" style={{ gap: 14 }}>
                <span><span className="muted tiny">Projected</span><br /><b className="money">{pr ? aud(pr.aud[1]) : "—"}</b>{pr && <span className="muted tiny"> ({aud(pr.aud[0], 0)}–{aud(pr.aud[2], 0)})</span>}</span>
                <span><span className="muted tiny">Campaign</span><br /><b>{pr?.campaign || v.edl.rules?.program || "none"}</b></span>
                {pr && <span><span className="muted tiny">Expected views</span><br /><b>{pr.expected_views.toLocaleString()}</b> <span className="muted tiny">{PLATFORM_LABEL[pr.platform] || pr.platform}</span></span>}
                <span><span className="muted tiny">Rules</span><br />{flags.some((f) => f.severity === "error")
                  ? <b className="warn-text" style={{ cursor: "pointer" }} onClick={() => setRight("Rules")}>⚑ {flags.length} to fix</b> : <b className="ok-text">✓ OK</b>}</span>
              </div>
              {flags.filter((f) => f.fix).map((f) => <div key={f.id} className="ed-flag bad"><span>⚑ {f.message}</span><button onClick={() => fixFlag(f.id)}>{f.fix}</button></div>)}
              {(v.edl.moment?.why || []).length > 0 && (
                <details className="ed-why" open>
                  <summary>Why this moment?{v.edl.moment?.viral != null ? ` Viral score ${v.edl.moment.viral}/100` : ""}</summary>
                  {v.edl.moment.why.slice(0, 10).map((r: any) => (
                    <div key={r.feature} className="why-row" title={`${r.feature}: ${r.value}`}>
                      <span>{r.label}</span><i className={r.contribution >= 0 ? "pos" : "neg"} style={{ width: `${Math.min(100, Math.abs(r.contribution) * 40)}%` }} />
                      <b className={r.contribution >= 0 ? "ok-text" : "warn-text"}>{r.contribution >= 0 ? "+" : ""}{r.contribution.toFixed(2)}</b></div>))}
                </details>)}
              {vir?.parts && <div className="ed-parts">{Object.entries(vir.parts).map(([k, x]: any) => <span key={k} title={k}><i style={{ width: `${x}%` }} />{k}</span>)}</div>}
            </div>)}
          {right === "Captions" && <CaptionsPanel v={v} onEdl={onEdl} templates={templates} fonts={fonts}
            reload={() => { api.get(`/edit/${cid}`).then(show); api.get("/caption-templates").then(setTemplates); }} />}
          {right === "Title" && <HookPanel v={v} onEdl={onEdl} post={post} />}
          {right === "Layout" && <div className="stack"><LayoutPanel v={v} onEdl={onEdl} post={post} t={t} /><FacecamEditor v={v} onSaved={show} /></div>}
          {right === "Colour" && <ColourPanel v={v} onEdl={onEdl} />}
          {right === "Audio" && <AudioKeysPanel v={v} onEdl={onEdl} t={t} />}
          {right === "Effects" && <EffectsPanel v={v} onEdl={onEdl} t={t} post={post} />}
          {right === "Transcript" && <TranscriptPanel v={v} seek={seek} op={op} t={t} />}
          {right === "Rules" && <RulesPanel v={v} flags={flags} fix={fixFlag} />}
          {right === "Sequence" && <SequencePanel v={v} del={del} />}
        </div>
      </aside>

      <section className="st-bottom">
        <Timeline v={v} t={t} seek={seek} splits={splits} sel={sel} setSel={(s) => { setSel(s); }} onEdl={onEdl}
          onRange={(a, b) => op({ op: "range", start: +a.toFixed(3), end: +b.toFixed(3) })} zoom={zoom} setZoom={setZoom} />
      </section>

      {sheet && (
        <div className="modal-back" onClick={() => setSheet(false)}>
          <div className="modal card" role="dialog" aria-label="Keyboard shortcuts" onClick={(e) => e.stopPropagation()}>
            <div className="row between"><h2>Keyboard shortcuts</h2><button className="btn-quiet" onClick={() => setSheet(false)}>Close</button></div>
            <table className="shortcuts"><tbody>{SHORTCUTS.map(([k, d]) => <tr key={k}><td><kbd>{k}</kbd></td><td>{d}</td></tr>)}</tbody></table>
          </div>
        </div>)}
    </div>
  );
}

/** Drag text layers, stickers and the caption block right on the preview. */
function CanvasLayers({ v, t, s, sel, setSel, onEdl, onPip }: { v: any; t: number; s: number; sel: Sel; setSel: (x: Sel) => void;
  onEdl: (fn: (e: any) => any, l: string) => void; onPip: (corner: string, w: number) => void }) {
  const [drag, setDrag] = useState<any>(null);
  const e = v.edl;
  const W = 1080 * s, H = 1920 * s;
  const items: any[] = [];
  (e.text_layers || []).forEach((L: any, i: number) => { if ((t >= L.start && t <= L.end) || (sel?.kind === "text" && sel.i === i)) items.push({ kind: "text", i, L }); });
  (e.stickers || []).forEach((L: any, i: number) => { if ((t >= L.start && t <= L.end) || (sel?.kind === "sticker" && sel.i === i)) items.push({ kind: "sticker", i, L }); });
  const capY = v.caption_y ?? null;
  const up = () => {
    if (!drag) return;
    const { kind, i, dx, dy } = drag;
    if (kind === "pip" || kind === "pipsize") {
      const [x, y, w, h] = v.pip_rect;
      if (kind === "pip" && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) {
        const cx = (x + w / 2) * s + dx, cy = (y + h / 2) * s + dy;         // snap to the nearest corner
        onPip(`${cy < 960 * s ? "t" : "b"}${cx < 540 * s ? "l" : "r"}`, v.edl.layout.pip?.w ?? 0.34);
      } else if (kind === "pipsize" && Math.abs(dx) > 2) {
        const right = (v.edl.layout.pip?.corner || "tr").endsWith("r");
        const nw = Math.max(0.25, Math.min(0.45, (w + (right ? -dx : dx) / s) / 1080));
        onPip(v.edl.layout.pip?.corner || "tr", +nw.toFixed(3));
      }
    } else if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
      if (kind === "caps") onEdl((x) => { x.captions.nudge = Math.max(-120, Math.min(60, Math.round((x.captions.nudge || 0) + dy / s))); return x; }, "Caption height");
      else {
        const list = kind === "text" ? "text_layers" : "stickers";
        onEdl((x) => { const L = x[list][i]; L.x = +Math.max(0, Math.min(1, L.x + dx / W)).toFixed(4); L.y = +Math.max(0, Math.min(1, L.y + dy / H)).toFixed(4); return x; }, kind === "text" ? "Move text" : "Move sticker");
      }
    }
    setDrag(null);
  };
  return (
    <div className="canvas-layers" onMouseMove={(ev) => drag && setDrag({ ...drag, dx: ev.clientX - drag.x0, dy: ev.clientY - drag.y0 })} onMouseUp={up} onMouseLeave={up}
      style={{ pointerEvents: drag ? "auto" : "none" }}>
      {items.map(({ kind, i, L }) => {
        const on = !!sel && sel.kind === kind && sel.i === i;
        const d = drag && drag.kind === kind && drag.i === i ? drag : { dx: 0, dy: 0 };
        const w = kind === "text" ? Math.max(60, String(L.text).length * L.size * 0.55) * s : L.size * s;
        const h = (kind === "text" ? L.size * 1.25 : L.size) * s;
        return (
          <div key={kind + i} className={on ? "cv-item on" : "cv-item"} title={kind === "text" ? "Drag to move the text" : "Drag to move the sticker"}
            style={{ left: L.x * W - w / 2 + d.dx, top: L.y * H - h / 2 + d.dy, width: w, height: h, pointerEvents: "auto" }}
            onMouseDown={(ev) => { ev.preventDefault(); ev.stopPropagation(); setSel({ kind, i }); setDrag({ kind, i, x0: ev.clientX, y0: ev.clientY, dx: 0, dy: 0 }); }} />);
      })}
      {v.pip_rect && (() => {
        const [x, y, w, h] = v.pip_rect;
        const d = drag && drag.kind === "pip" ? drag : { dx: 0, dy: 0 };
        const right = (e.layout.pip?.corner || "tr").endsWith("r");
        return (
          <div className="cv-item cv-pip" title="Drag the facecam to another corner; drag its inner corner to resize"
            style={{ left: x * s + d.dx, top: y * s + d.dy, width: w * s, height: h * s, pointerEvents: "auto" }}
            onMouseDown={(ev) => { ev.preventDefault(); ev.stopPropagation(); setDrag({ kind: "pip", i: 0, x0: ev.clientX, y0: ev.clientY, dx: 0, dy: 0 }); }}>
            <i className={right ? "rs l" : "rs r"} onMouseDown={(ev) => { ev.preventDefault(); ev.stopPropagation(); setDrag({ kind: "pipsize", i: 0, x0: ev.clientX, y0: ev.clientY, dx: 0, dy: 0 }); }} />
          </div>);
      })()}
      {e.captions?.enabled !== false && capY != null && (
        <div className="cv-caps" title="Drag up or down to move the captions"
          style={{ top: (capY + Math.max(-120, Math.min(60, e.captions?.nudge || 0))) * s - 30 * s + (drag?.kind === "caps" ? drag.dy : 0), pointerEvents: "auto" }}
          onMouseDown={(ev) => { ev.preventDefault(); setDrag({ kind: "caps", i: 0, x0: ev.clientX, y0: ev.clientY, dx: 0, dy: 0 }); }}>⇕ captions</div>)}
    </div>
  );
}

function SfxBin({ t, onAdd }: { t: number; onAdd: (id: string) => void }) {
  const [list, setList] = useState<any[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => { api.get("/sfx").then(setList).catch(() => {}); }, []);
  return (
    <div className="stack tight">
      <input placeholder="Search sounds" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search sounds" />
      <span className="muted tiny">Click to add at {fmt(t)}. Bundled pack (licensed) + your own folder.</span>
      <div className="lib-list">{list.filter((x) => !q || x.title.toLowerCase().includes(q.toLowerCase())).map((x) => (
        <button key={x.id} className="lib-item" onClick={() => onAdd(x.id)}>♪ {x.title}{x.source === "local" ? <span className="muted tiny"> · yours</span> : null}</button>))}</div>
    </div>
  );
}

function BrollBin({ t, onAdd, go }: { t: number; onAdd: (p: string) => void; go: (p: Page, t?: string) => void }) {
  const [b, setB] = useState<any>({ items: [] });
  useEffect(() => { api.get("/broll").then(setB).catch(() => {}); }, []);
  return (
    <div className="stack tight">
      <span className="muted tiny">Your own B-roll folder only. Click to add at {fmt(t)}.</span>
      {!b.items.length && <button className="btn-quiet" onClick={() => go("settings", "Music")}>Set your B-roll folder</button>}
      <div className="lib-grid">{b.items.map((x: any) => (
        <button key={x.path} className="lib-tile" onClick={() => onAdd(x.path)} title={x.name}>
          {/\.(png|jpe?g|webp|gif)$/i.test(x.path) ? <img src={api.mediaUrl(x.path)} alt="" loading="lazy" /> : <span>🎞</span>}<span className="tiny ellipsis">{x.name}</span></button>))}</div>
    </div>
  );
}

function StickerBin({ onAdd, go }: { onAdd: (s: { emoji?: string; path?: string }) => void; go: (p: Page, t?: string) => void }) {
  const [d, setD] = useState<any>({ emoji: [], images: [] });
  useEffect(() => { api.get("/stickers").then(setD).catch(() => {}); }, []);
  return (
    <div className="stack tight">
      <span className="muted tiny">Click to add at the playhead, then drag it on the preview.</span>
      <div className="sticker-grid">{d.emoji.map((em: string) => <button key={em} className="sticker" onClick={() => onAdd({ emoji: em })} aria-label={`Sticker ${em}`}>{em}</button>)}</div>
      <b className="small-text">Your stickers</b>
      {d.images.length ? <div className="lib-grid">{d.images.map((x: any) => (
        <button key={x.path} className="lib-tile" onClick={() => onAdd({ path: x.path })} title={x.name}><img src={api.mediaUrl(x.path)} alt="" loading="lazy" /></button>))}</div>
        : <button className="btn-quiet" onClick={() => go("settings", "Music")}>{d.folder ? "Your sticker folder is empty" : "Set your sticker folder (Settings › Music & B-roll)"}</button>}
    </div>
  );
}
