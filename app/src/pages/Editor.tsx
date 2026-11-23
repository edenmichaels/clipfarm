// Editor: every clip is an editable project. Left: clips ranked by projected earnings or virality.
// Centre: 9:16 live preview. Right: Transcript / Captions / Layout / Hook & Title / Music & Audio / Effects / Campaign rules.
// Bottom: multi-track timeline. Simple mode (default) shows Auto-edit, Fix my clip and Export; "Show more" opens the rest.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, aud, MOMENT_EMOJI, PLATFORM_LABEL, waitForJob } from "../api";
import type { Page } from "../App";
import {
  CaptionsPanel, EffectsPanel, HookPanel, LayoutPanel, MusicPanel, Preview, RulesPanel, Timeline, TranscriptPanel,
  keptOf, toSrc, useCaptionFonts,
} from "../components/EditorParts";

const STATUSES = ["new", "edited", "ready", "posted"];
export const TAG_LABEL: Record<string, string> = { explosion: "Explosion", crash: "Crash", gunfire: "Gunfire",
  police: "Police / wanted / arrest", death: "Death / wasted", stunt: "Stunt / flip / jump", fall: "Fall / ragdoll",
  npc: "NPC fight / reaction", vehicle_steal: "Vehicle steal", plane: "Plane / heli", boat: "Boat", chase: "Chase",
  fail: "Fail / glitch", easter_egg: "Easter egg / animal / myth", money: "Money / heist" };
const TABS = ["Transcript", "Captions", "Layout", "Hook & Title", "Music & Audio", "Effects", "Campaign rules"];
const FEEDBACK_TAGS = ["not funny", "too slow", "cut too early", "cut too late", "boring game part", "wrong moment",
  "bad captions", "wrong music", "bad framing", "great moment", "great timing"];

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
  useEffect(() => { if (initialClip) setSelId(initialClip); }, [initialClip]);
  const [picked, setPicked] = useState<number[]>([]);
  const [simple, setSimple] = useState(readPref("ed.simple", "1") === "1");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    const q = new URLSearchParams({ sort });
    if (top) q.set("min_score", "90");
    Object.entries(filters).forEach(([k, v]) => v && q.set(k, String(v)));
    return api.get(`/editor/clips?${q}`).then((c: any[]) => {
      setClips(c);
      setSelId((cur) => (c.some((x) => x.id === cur) ? cur : c[0]?.id ?? null));
    }).catch((e) => setMsg(e.message));
  }, [sort, top, filters]);
  useEffect(() => { load(); writePref("ed.sort", sort); }, [load]);
  useEffect(() => { api.get("/editor/clips?sort=newest").then(setAll).catch(() => {}); }, [clips.length]);
  useEffect(() => writePref("ed.simple", simple ? "1" : "0"), [simple]);

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
  const sel = clips.find((c) => c.id === selId) || null;

  return (
    <div className={simple ? "editor simple" : "editor"}>
      <header className="row between wrap" style={{ gap: 10 }}>
        <div className="row" style={{ gap: 10 }}>
          <h1>Editor</h1>
          <span className="muted">{clips.length} clip{clips.length === 1 ? "" : "s"}</span>
        </div>
        <button className="btn-ghost" onClick={() => setSimple(!simple)}>{simple ? "Show more" : "Show less"}</button>
      </header>
      {msg && <span className="muted" role="status">{msg}</span>}
      <div className="ed-grid">
        <aside className="ed-list">
          <div className="row wrap" style={{ gap: 6 }}>
            <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
              <option value="profit">Most A$ first</option><option value="virality">Highest score first</option><option value="newest">Newest</option>
            </select>
            <button className={top ? "pill-btn on" : "pill-btn"} onClick={() => setTop(!top)} title="Only clips scoring 90 or more">Post the 90+ first</button>
          </div>
          {!simple && (
            <div className="row wrap" style={{ gap: 6 }}>
              <select value={filters.program_id} onChange={(e) => setFilters({ ...filters, program_id: e.target.value })} aria-label="Campaign">
                <option value="">All campaigns</option>{opts.programs.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</select>
              <select value={filters.streamer_id} onChange={(e) => setFilters({ ...filters, streamer_id: e.target.value })} aria-label="Streamer">
                <option value="">All streamers</option>{opts.streamers.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</select>
              <select value={filters.moment_type} onChange={(e) => setFilters({ ...filters, moment_type: e.target.value })} aria-label="Moment type">
                <option value="">All moments</option>{opts.types.map((t) => <option key={t} value={t}>{MOMENT_EMOJI[t] || ""} {t}</option>)}</select>
              <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} aria-label="Status">
                <option value="">Any status</option>{STATUSES.map((s) => <option key={s}>{s}</option>)}</select>
              <select value={filters.tag} onChange={(e) => setFilters({ ...filters, tag: e.target.value })} aria-label="What happened" title="What happened in the game (event tags)">
                <option value="">Any event</option>{Object.entries(TAG_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
              <select value={filters.reaction} onChange={(e) => setFilters({ ...filters, reaction: e.target.value })} aria-label="Reaction" title="How the streamer or chat reacted">
                <option value="">Any reaction</option><option value="laugh">…with the streamer laughing</option>
                <option value="scream">…with the streamer screaming</option><option value="chat_laugh">…with chat laughing</option>
                <option value="disbelief">…with "no way" / "bro"</option></select>
            </div>
          )}
          {picked.length > 0 && (
            <div className="ed-batch">
              <b>{picked.length} selected</b>
              <select defaultValue="" onChange={(e) => e.target.value && batch("template", { template: e.target.value })} aria-label="Apply template">
                <option value="">Apply caption template…</option>
                {["bold_pop", "hype_yellow", "clean_white", "minimal"].map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
              </select>
              <button onClick={() => batch("captions")}>Regenerate captions</button>
              <button onClick={() => batch("fix")}>Fix my clips</button>
              <button className="btn-primary" onClick={() => batch("export")}>Export all</button>
              <button className="btn-danger" onClick={() => batch("delete")}>Delete</button>
              <button onClick={() => setPicked([])}>Clear</button>
            </div>
          )}
          {clips.length === 0 && <p className="muted">{top ? "No 90+ clips yet." : "No clips yet. New clips show up here as projects run."}</p>}
          <div className="ed-cards">
            {clips.map((c) => (
              <div key={c.id} className={c.id === selId ? "ed-card on" : "ed-card"} onClick={() => setSelId(c.id)}>
                <input type="checkbox" aria-label="Select for batch" checked={picked.includes(c.id)} onClick={(e) => e.stopPropagation()}
                  onChange={() => setPicked(picked.includes(c.id) ? picked.filter((x) => x !== c.id) : [...picked, c.id])} />
                <div className="thumb">{c.thumb_path && <img src={api.mediaUrl(c.thumb_path)} alt="" />}</div>
                <div className="stack tight grow" style={{ minWidth: 0 }}>
                  <b className="ellipsis">{c.moment_type ? `${MOMENT_EMOJI[c.moment_type] || ""} ` : ""}{c.title}</b>
                  {c.variant_label && <span className="muted tiny">Variant {c.variant_label} of clip #{c.variant_of}</span>}
                  {(c.moment_tags || []).length > 0 && <span className="tagrow">{c.moment_tags.slice(0, 3).map((t: string) => <span key={t} className="tagchip">{TAG_LABEL[t]?.split(" / ")[0] || t}</span>)}</span>}
                  <span className="muted tiny ellipsis">{c.streamer}{c.program ? ` · ${c.program}` : ""}</span>
                  <div className="row" style={{ gap: 6 }}>
                    <span className={`score ${scoreClass(c.virality)}`} title={c.virality_reason || ""}>{c.virality ?? "–"}</span>
                    <span className="money" title="Projected earnings (likely)">{c.profit_likely != null ? aud(c.profit_likely) : "no campaign"}</span>
                    {!c.rules_ok && <span className="flag" title="Breaks a campaign rule">⚑</span>}
                    <span className={`pill ${c.status}`}>{c.status}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </aside>
        {sel ? <Workspace key={sel.id} clip={sel} simple={simple} onChanged={load} go={go} setMsg={setMsg} /> :
          <section className="card muted">Pick a clip on the left.</section>}
      </div>
    </div>
  );
}

function Workspace({ clip, simple, onChanged, go, setMsg }: { clip: any; simple: boolean; onChanged: () => void; go: (p: Page, t?: string) => void; setMsg: (m: string) => void }) {
  const cid = clip.id;
  const [v, setV] = useState<any>(null);
  const [score, setScore] = useState<any>(null);
  const [buildingNow, setBuilding] = useState(false);
  const [tab, setTab] = useState(TABS[0]);
  const [t, setT] = useState(0);
  const [zone, setZone] = useState<string | null>(readPref("ed.zone2", "") || null);
  const [busy, setBusy] = useState("");
  const [splits, setSplits] = useState<number[]>([]);
  const [sel, setSel] = useState<[number, number] | null>(null);
  const [preset, setPreset] = useState("tiktok");
  const [templates, setTemplates] = useState<any>(null);
  const [tracks, setTracks] = useState<any[]>([]);
  const [rated, setRated] = useState<number | null>(null);
  const [tags, setTags] = useState<string[]>([]);
  const [brands, setBrands] = useState<any[]>([]);
  const [loadErr, setLoadErr] = useState("");
  const fonts = useCaptionFonts();
  const vid = useRef<HTMLVideoElement>(null);
  const building = useRef<{ current: boolean; again: boolean }>({ current: false, again: false }).current;

  // Rebuild the playable preview (same renderer as export) after every change; one at a time.
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
      const r = await api.get(`/edit/${cid}`);
      setV(r);
    } catch (e: any) { setMsg(`Preview: ${e.message}`); }
    finally { building.current = false; setBuilding(false); }
  }, [cid]);
  const rescore = () => api.post(`/edit/${cid}/score`).then(setScore).catch(() => {});
  const show = (r: any) => { setV(r); rescore(); if (!r.proxy) rebuild(); onChanged(); };
  useEffect(() => {
    api.get(`/edit/${cid}`).then((r) => { setV(r); setPreset(r.edl.export?.preset || "tiktok"); if (!r.proxy) rebuild(); }).catch((e) => setLoadErr(e.message));
    rescore();
    api.get("/caption-templates").then(setTemplates).catch(() => {});
    api.get("/music/tracks").then((r) => setTracks(r.tracks || [])).catch(() => {});
    api.get("/brand-templates").then((r) => setBrands(r.templates || [])).catch(() => {});
  }, [cid]);
  useEffect(() => writePref("ed.zone2", zone || ""), [zone]);

  const run = async (label: string, p: Promise<any>) => {
    setBusy(label);
    try { const r = await p; if (r?.edl) show(r); if (r?.changes) setMsg(r.changes.length ? `Done: ${r.changes.join(", ")}` : "Nothing to fix."); return r; }
    catch (e: any) { setMsg(e.message); } finally { setBusy(""); }
  };
  const onEdl = (fn: (e: any) => any, label: string) => {
    if (!v) return;
    const next = fn(JSON.parse(JSON.stringify(v.edl)));
    run(label, api.put(`/edit/${cid}`, { edl: next, label }));
    api.post(`/edit/${cid}/signal`, { kind: "edit" }).catch(() => {});
  };
  const op = (b: any) => run("Editing", api.post(`/edit/${cid}/transcript`, b));
  const post = (path: string, b: any = {}) => run("Working", api.post(path, b));
  const undo = () => run("Undo", api.post(`/edit/${cid}/undo`));
  const redo = () => run("Redo", api.post(`/edit/${cid}/redo`));
  const seek = (x: number) => { setT(x); if (vid.current) vid.current.currentTime = x; };
  const fixFlag = (id: string | null) => run("Fixing", api.post(`/edit/${cid}/fix`, id ? { id } : { all: true }));
  const rippleDelete = () => {
    if (!sel || !v) return;
    const kept = keptOf(v);
    const a = toSrc(kept, sel[0]), b = toSrc(kept, sel[1]);
    onEdl((e) => { e.cuts = [...(e.cuts || []), [+a.toFixed(3), +b.toFixed(3)]]; return e; }, "Delete part");
    setSel(null); setSplits(splits.filter((s) => s < sel[0] || s > sel[1]));
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
  const sendToPost = () => doExport(async () => {
    await api.post(`/edit/${cid}/signal`, { kind: "keep" }).catch(() => {});
    go("clips", "Post & Submit");
  });
  const rate = async (r: number, tg: string[] = tags) => {
    setRated(r);
    try { await api.post(`/clips/${cid}/feedback`, { rating: r, tags: tg }); } catch (e: any) { setMsg(e.message); }
  };

  // keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tg = (e.target as HTMLElement)?.tagName;
      if (tg === "INPUT" || tg === "TEXTAREA" || tg === "SELECT") return;
      const el = vid.current;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); return; }
      if (e.key === " ") { e.preventDefault(); if (el) { el.paused ? el.play() : el.pause(); } }
      else if (e.key === "k" || e.key === "K") { el?.pause(); if (el) el.playbackRate = 1; }
      else if (e.key === "l" || e.key === "L") { if (el) { el.playbackRate = el.paused ? 1 : Math.min(4, el.playbackRate * 2); el.play(); } }
      else if (e.key === "j" || e.key === "J") { if (el) { el.pause(); seek(Math.max(0, el.currentTime - 2)); } }
      else if (e.key === "s" || e.key === "S") { if (t > 0.05) setSplits((s) => Array.from(new Set([...s, +t.toFixed(3)]))); }
      else if (e.key === "Delete" || e.key === "Backspace") { if (sel) { e.preventDefault(); rippleDelete(); } }
      else if (e.key === "ArrowLeft") seek(Math.max(0, t - 1 / 30));
      else if (e.key === "ArrowRight") seek(t + 1 / 30);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!v) return <section className="card muted">{loadErr || "Loading the edit…"}</section>;
  const vir = score?.virality;
  const pr = score?.profit;
  const flags: any[] = score?.flags || [];

  return (
    <section className="ed-work">
      <div className="ed-top row wrap">
        <button onClick={undo} title="Undo (Ctrl+Z)" aria-label="Undo">↶</button>
        <button onClick={redo} title="Redo (Ctrl+Y)" aria-label="Redo">↷</button>
        {!simple && <select defaultValue="" onChange={(e) => { const k = e.target.value; e.target.value = ""; k && onEdl((x) => { x.captions.template = k; return x; }, `Template: ${k}`); }} aria-label="Apply template">
          <option value="">Apply template…</option>{(templates?.templates || []).map((x: any) => <option key={x.key} value={x.key}>{x.name}</option>)}</select>}
        {!simple && brands.length > 0 && <select value={v.edl.brand?.id ?? ""} onChange={(e) => post(`/edit/${cid}/brand`, { brand_template_id: e.target.value ? Number(e.target.value) : null })} aria-label="Brand template">
          <option value="">No brand template</option>{brands.map((b) => <option key={b.id} value={b.id}>Brand: {b.name}</option>)}</select>}
        <button disabled={!!busy} onClick={async () => {
          setBusy("Making variants");
          try { const r = await api.post(`/edit/${cid}/variants`, { n: 2 }); setMsg(`Made ${r.variants.map((x: any) => `[${x.label}] ${x.what}`).join(" · ")}`); onChanged(); }
          catch (e: any) { setMsg(e.message); } finally { setBusy(""); }
        }} title="2 versions with a different hook, length and layout, for A/B tests">Make variants</button>
        <span className="grow" />
        <div className="row" style={{ gap: 4 }}>
          <button className={rated === 1 ? "pill-btn on" : "pill-btn"} onClick={() => rate(1)} aria-label="Thumbs up">👍</button>
          <button className={rated === -1 ? "pill-btn on" : "pill-btn"} onClick={() => rate(-1)} aria-label="Thumbs down">👎</button>
        </div>
        <select value={preset} onChange={(e) => setPreset(e.target.value)} aria-label="Export for">
          <option value="tiktok">TikTok</option><option value="shorts">YouTube Shorts</option><option value="reels">Instagram Reels</option>
          <option value="x">X</option><option value="srt">Video + .srt captions</option></select>
        <button className="btn-ghost" disabled={!!busy} onClick={() => doExport()}>Export</button>
        <button className="btn-primary" disabled={!!busy} onClick={sendToPost}>Send to Post & Submit</button>
      </div>
      {rated != null && (
        <div className="row wrap" style={{ gap: 6 }}>
          <span className="muted tiny">Why?</span>
          {FEEDBACK_TAGS.filter((x) => (rated > 0) === x.startsWith("great")).map((x) => (
            <button key={x} className={tags.includes(x) ? "pill-btn on small" : "pill-btn small"} onClick={() => {
              const n = tags.includes(x) ? tags.filter((y) => y !== x) : [...tags, x]; setTags(n); rate(rated, n);
            }}>{x}</button>))}
        </div>
      )}
      {busy && <span className="muted" role="status">{busy}…</span>}

      <div className="ed-main">
        <div className="ed-centre">
          <Preview v={v} videoRef={vid} t={t} setT={setT} zone={zone} building={buildingNow} />
          <div className="row wrap" style={{ gap: 6, justifyContent: "center" }}>
            {["tiktok", "reels", "shorts"].map((z) => <button key={z} className={zone === z ? "pill-btn on small" : "pill-btn small"} onClick={() => setZone(zone === z ? null : z)}>{z === "tiktok" ? "TikTok" : z === "reels" ? "Reels" : "Shorts"} zone</button>)}
          </div>
        </div>
        <div className="ed-side">
          <div className="ed-scorecard card">
            <div className="row" style={{ gap: 12 }}>
              <span className={`score big ${scoreClass(vir?.score)}`}>{vir?.score ?? "–"}</span>
              <div className="stack tight grow">
                <b>{vir?.reason || "Scoring…"}</b>
                <span className="muted tiny">{v.edl.moment?.type ? `${MOMENT_EMOJI[v.edl.moment.type] || ""} ${v.edl.moment.type}` : ""}{v.edl.moment?.reason ? ` · ${v.edl.moment.reason}` : ""}</span>
              </div>
            </div>
            <div className="row wrap" style={{ gap: 14 }}>
              <span><span className="muted tiny">Projected</span><br /><b className="money">{pr ? aud(pr.aud[1]) : "—"}</b>
                {pr && <span className="muted tiny"> ({aud(pr.aud[0], 0)}–{aud(pr.aud[2], 0)})</span>}</span>
              <span><span className="muted tiny">Campaign</span><br /><b>{pr?.campaign || v.edl.rules?.program || "none"}</b></span>
              {pr && <span><span className="muted tiny">Expected views</span><br /><b>{pr.expected_views.toLocaleString()}</b> <span className="muted tiny">{PLATFORM_LABEL[pr.platform] || pr.platform}</span></span>}
              <span><span className="muted tiny">Rules</span><br />{flags.some((f) => f.severity === "error")
                ? <b className="warn-text" style={{ cursor: "pointer" }} onClick={() => { setTab("Campaign rules"); }}>⚑ {flags.length} to fix</b> : <b className="ok-text">✓ OK</b>}</span>
            </div>
            {(v.edl.moment?.why || []).length > 0 && (
              <details className="ed-why">
                <summary>Why this moment?{v.edl.moment?.viral != null ? ` Viral score ${v.edl.moment.viral}/100` : ""}</summary>
                {v.edl.moment.why.slice(0, 10).map((r: any) => (
                  <div key={r.feature} className="why-row" title={`${r.feature}: ${r.value}`}>
                    <span>{r.label}</span>
                    <i className={r.contribution >= 0 ? "pos" : "neg"} style={{ width: `${Math.min(100, Math.abs(r.contribution) * 40)}%` }} />
                    <b className={r.contribution >= 0 ? "ok-text" : "warn-text"}>{r.contribution >= 0 ? "+" : ""}{r.contribution.toFixed(2)}</b>
                  </div>))}
              </details>
            )}
            {!simple && vir?.parts && (
              <div className="ed-parts">{Object.entries(vir.parts).map(([k, x]: any) => (
                <span key={k} title={k}><i style={{ width: `${x}%` }} />{k}</span>))}</div>
            )}
          </div>
          {simple ? (
            <div className="stack" style={{ gap: 10 }}>
              <button className="btn-primary big" disabled={!!busy} onClick={() => run("Auto-editing", api.post(`/edit/${cid}/auto-edit`))}>✨ Auto-edit</button>
              <button className="btn-ghost big" disabled={!!busy} onClick={() => run("Fixing", api.post(`/edit/${cid}/fix-my-clip`))}>🩹 Fix my clip</button>
              <button className="btn-ghost big" disabled={!!busy} onClick={() => doExport()}>⬇ Export for {preset === "srt" ? "SRT" : preset}</button>
              {flags.filter((f) => f.fix).map((f) => (
                <div key={f.id} className="ed-flag bad"><span>⚑ {f.message}</span><button onClick={() => fixFlag(f.id)}>{f.fix}</button></div>
              ))}
              <span className="muted tiny">Auto-edit rebuilds the edit from the automatic version. Fix my clip trims dead air, filler words and a long ending, adds a hook and fixes campaign rules. Click "Show more" for the full editor.</span>
            </div>
          ) : (
            <div className="card stack tight">
              <div className="ed-tabs" role="tablist">{TABS.map((x) => (
                <button key={x} role="tab" aria-selected={tab === x} className={tab === x ? "tab on" : "tab"} onClick={() => setTab(x)}>
                  {x}{x === "Campaign rules" && flags.length ? ` (${flags.length})` : ""}</button>))}</div>
              <div className="ed-panel">
                {tab === "Transcript" && <TranscriptPanel v={v} seek={seek} op={op} t={t} />}
                {tab === "Captions" && <CaptionsPanel v={v} onEdl={onEdl} templates={templates} fonts={fonts}
                  reload={() => { api.get(`/edit/${cid}`).then(show); api.get("/caption-templates").then(setTemplates); }} />}
                {tab === "Layout" && <LayoutPanel v={v} onEdl={onEdl} post={post} t={t} />}
                {tab === "Hook & Title" && <HookPanel v={v} onEdl={onEdl} post={post} />}
                {tab === "Music & Audio" && <MusicPanel v={v} onEdl={onEdl} tracks={tracks} post={post} />}
                {tab === "Effects" && <EffectsPanel v={v} onEdl={onEdl} t={t} post={post} />}
                {tab === "Campaign rules" && <RulesPanel v={v} flags={flags} fix={fixFlag} />}
              </div>
            </div>
          )}
        </div>
      </div>
      {!simple && (
        <Timeline v={v} t={t} seek={seek} splits={splits} sel={sel} setSel={setSel} onEdl={onEdl}
          onRange={(a, b) => op({ op: "range", start: +a.toFixed(3), end: +b.toFixed(3) })} />
      )}
    </section>
  );
}
