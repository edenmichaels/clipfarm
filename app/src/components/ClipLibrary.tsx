// My Clips library (v0.8.6): sort (remembered, viral score by default), select (checkbox, Shift+click range,
// Ctrl+click toggle, Ctrl+A, Esc, Delete), a selection bar, delete with a confirmation that shows the space freed,
// and "Recently deleted" (7 days, Restore, Empty now).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, aud } from "../api";
import { Thumb } from "./ClipGrid";

const FILTERS: [string, string][] = [["All", ""], ["Pending", "review_status=pending"], ["Approved", "review_status=approved"], ["Posted", "posted=true"]];
const STATUS_PILL: Record<string, string> = { "not posted": "", posted: "posted", submitted: "ok" };

type Clip = any;

export default function ClipLibrary({ onOpen, onSendToPost }: { onOpen: (c: Clip) => void; onSendToPost: () => void }) {
  const [filter, setFilter] = useState("All");
  const [sorts, setSorts] = useState<{ key: string; label: string }[]>([]);
  const [sort, setSort] = useState("");
  const [clips, setClips] = useState<Clip[]>([]);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<any>(null);
  const [styles, setStyles] = useState<any[]>([]);
  const [styleOpen, setStyleOpen] = useState(false);
  const [msg, setMsg] = useState("");
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get("/clips/sorts").then((r) => { setSorts(r.sorts); setSort(r.current); }).catch(() => {});
    api.get("/styles").then((r) => setStyles(r.styles || [])).catch(() => {});
  }, []);

  const load = useCallback(() => {
    if (!sort) return;
    const q = FILTERS.find(([l]) => l === filter)![1];
    api.get(`/clips?limit=5000&sort=${sort}${q ? "&" + q : ""}`).then((rows: Clip[]) => {
      setClips(rows);
      setSel((s) => new Set([...s].filter((id) => rows.some((r) => r.id === id))));   // drop clips that left the view
    }).catch(() => {});
  }, [filter, sort]);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  const ids = useMemo(() => clips.map((c) => c.id), [clips]);
  const allOn = ids.length > 0 && ids.every((id) => sel.has(id));

  const click = (e: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }, c: Clip, viaBox = false) => {
    if (e.shiftKey && anchor !== null) {
      const a = ids.indexOf(anchor), b = ids.indexOf(c.id);
      const [lo, hi] = a < b ? [a, b] : [b, a];
      const next = new Set(e.ctrlKey || e.metaKey ? sel : []);
      ids.slice(lo, hi + 1).forEach((id) => next.add(id));
      setSel(next);
      return;
    }
    if (viaBox || e.ctrlKey || e.metaKey || sel.size > 0) {
      const next = new Set(sel);
      next.has(c.id) ? next.delete(c.id) : next.add(c.id);
      setSel(next);
      setAnchor(c.id);
      return;
    }
    onOpen(c);
  };

  const askDelete = async (list: number[]) => {
    if (!list.length) return;
    try { setConfirm({ ids: list, ...(await api.post("/clips/delete-preview", { ids: list })) }); } catch (e: any) { setMsg(e.message); }
  };
  const doDelete = async () => {
    try {
      const r = await api.post("/clips/delete", { ids: confirm.ids });
      setMsg(`Deleted ${r.deleted} clip${r.deleted === 1 ? "" : "s"} (in Recently deleted for 7 days).${r.note ? " " + r.note : ""}`);
      setConfirm(null); setSel(new Set()); load();
    } catch (e: any) { setMsg(e.message); }
  };
  const batch = async (action: string, extra: any = {}) => {
    try {
      const r = await api.post("/editor/batch", { ids: [...sel], action, ...extra });
      setMsg(action === "export" ? `Exporting ${r.jobs.length} clip(s).` : `Done for ${r.done.length} clip(s).`);
      if (r.failed?.length) setMsg((m) => `${m} ${r.failed.length} failed.`);
      return r;
    } catch (e: any) { setMsg(e.message); }
  };
  const applyStyle = async (key: string) => {
    setStyleOpen(false);
    let ok = 0;
    for (const id of sel) { try { await api.post(`/edit/${id}/style`, { key }); ok++; } catch { /* reported below */ } }
    setMsg(`Style applied to ${ok} of ${sel.size} clip(s).`);
  };
  const sendToPost = async () => { await batch("keep"); setSel(new Set()); onSendToPost(); };

  // keyboard: Ctrl+A, Esc, Delete (not while typing)
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (!root.current?.offsetParent) return;          // only while My Clips is showing
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); setSel(new Set(ids)); }
      else if (e.key === "Escape") { if (confirm) setConfirm(null); else setSel(new Set()); }
      else if (e.key === "Delete" && sel.size && !confirm) { e.preventDefault(); askDelete([...sel]); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [ids, sel, confirm]);

  return (
    <div className="stack" ref={root}>
      <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
        <label className="row lib-selall" style={{ gap: 8 }}>
          <input type="checkbox" aria-label="Select all" checked={allOn}
            ref={(el) => { if (el) el.indeterminate = sel.size > 0 && !allOn; }}
            onChange={() => setSel(allOn ? new Set() : new Set(ids))} />
          <span>{sel.size ? `${sel.size} of ${ids.length}` : `${ids.length} clip${ids.length === 1 ? "" : "s"}`}</span>
        </label>
        <span className="row" style={{ gap: 6 }}>
          <button className="btn-quiet" onClick={() => setSel(new Set())} disabled={!sel.size}>Select none</button>
          <button className="btn-quiet" onClick={() => setSel(new Set(ids.filter((id) => !sel.has(id))))} disabled={!ids.length}>Invert selection</button>
        </span>
        <span className="row" style={{ gap: 8, marginLeft: "auto" }}>
          {FILTERS.map(([l]) => (
            <button key={l} className={filter === l ? "pill-btn on" : "pill-btn"} style={{ padding: "6px 14px" }} onClick={() => setFilter(l)}>{l}</button>
          ))}
          <label className="row" style={{ gap: 6 }}>Sort
            <select aria-label="Sort clips" value={sort} onChange={(e) => setSort(e.target.value)}>
              {sorts.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
        </span>
      </div>

      {sel.size > 0 && (
        <div className="selbar" role="toolbar" aria-label="Selected clips">
          <b>{sel.size} selected</b>
          <button className="btn-danger" onClick={() => askDelete([...sel])}>Delete</button>
          <button onClick={() => batch("export", { preset: "tiktok" })}>Export</button>
          <button onClick={sendToPost}>Send to Post &amp; Submit</button>
          <span style={{ position: "relative" }}>
            <button onClick={() => setStyleOpen(!styleOpen)}>Apply style ▾</button>
            {styleOpen && (
              <div className="menu-pop">
                {styles.map((s) => <button key={s.key} className="btn-quiet" onClick={() => applyStyle(s.key)}>{s.name}</button>)}
              </div>)}
          </span>
          <button className="btn-quiet" onClick={() => setSel(new Set())}>Clear</button>
        </div>
      )}
      {msg && <span className="muted">{msg}</span>}

      {clips.length === 0 && <p className="muted">No clips here yet.</p>}
      <div className="grid">
        {clips.map((c) => (
          <div key={c.id} className={sel.has(c.id) ? "clip on" : "clip"} role="button" tabIndex={0}
            onClick={(e) => click(e, c)} onKeyDown={(e) => { if (e.key === "Enter") onOpen(c); }}>
            <input type="checkbox" className="clip-check" aria-label={`Select ${c.title}`} checked={sel.has(c.id)}
              onClick={(e) => { e.stopPropagation(); click(e as any, c, true); }} onChange={() => {}} />
            <Thumb clip={c} />
            <div className="meta">
              <b title={c.title}>{c.title}</b>
              <span className="muted" style={{ fontSize: 12 }}>{c.streamer || "—"}{c.program ? ` · ${c.program}` : ""}</span>
              <span style={{ fontSize: 12 }} className="row">
                {c.virality != null && <span className="pill" title="Viral score">🔥 {c.virality}</span>}
                {c.profit_likely != null && <span className="muted">{aud(c.profit_likely)}</span>}
                <span className={`pill ${STATUS_PILL[c.status_name] ?? ""}`}>{c.status_name}</span>
              </span>
            </div>
          </div>
        ))}
      </div>

      {confirm && (
        <div className="modal-back" onClick={() => setConfirm(null)}>
          <div className="card modal stack tight" role="dialog" aria-label="Delete clips" onClick={(e) => e.stopPropagation()}>
            <h2>{confirm.message}</h2>
            {confirm.notes.map((n: string) => <span key={n} className="muted">{n}</span>)}
            <span className="muted small-text">Deleted: the videos, previews, thumbnails and edit projects. Kept: links, views and earnings of posted clips. Source videos other clips use are kept.</span>
            <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button className="btn-ghost" onClick={() => setConfirm(null)}>Cancel</button>
              <button className="btn-danger" autoFocus onClick={doDelete}>Delete {confirm.count}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function RecentlyDeleted() {
  const [rows, setRows] = useState<any[]>([]);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [ask, setAsk] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => api.get("/clips/deleted").then(setRows).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  const total = rows.reduce((n, r) => n + (r.bytes || 0), 0);
  const gb = (b: number) => (b >= 1e9 ? `${(b / 1024 ** 3).toFixed(1)} GB` : `${(b / 1024 ** 2).toFixed(1)} MB`);
  const restore = async (ids: number[]) => {
    try { const r = await api.post("/clips/restore", { ids }); setMsg(`Restored ${r.restored} clip(s).`); setSel(new Set()); load(); }
    catch (e: any) { setMsg(e.message); }
  };
  const empty = async () => {
    try {
      const r = await api.post("/clips/deleted/empty", { ids: sel.size ? [...sel] : [] });
      setMsg(`Removed ${r.purged} clip(s) for good, freed ${r.size}.${r.skipped ? ` ${r.skipped} still rendering, removed later.` : ""}`);
      setAsk(false); setSel(new Set()); load();
    } catch (e: any) { setMsg(e.message); }
  };
  return (
    <div className="stack">
      <div className="row between">
        <span className="muted">Deleted clips stay here for 7 days, then their files are removed for good. Stats of posted clips are always kept.</span>
        <span className="row" style={{ gap: 8 }}>
          <button onClick={() => restore(sel.size ? [...sel] : rows.map((r) => r.id))} disabled={!rows.length}>{sel.size ? `Restore ${sel.size}` : "Restore all"}</button>
          <button className="btn-danger" onClick={() => setAsk(true)} disabled={!rows.length}>Empty now{rows.length ? ` (${gb(sel.size ? rows.filter((r) => sel.has(r.id)).reduce((n, r) => n + r.bytes, 0) : total)})` : ""}</button>
        </span>
      </div>
      {msg && <span className="muted">{msg}</span>}
      {rows.length === 0 ? <p className="muted">Nothing here.</p> : (
        <div className="grid">
          {rows.map((c) => (
            <div key={c.id} className={sel.has(c.id) ? "clip on" : "clip"} role="button" tabIndex={0}
              onClick={() => { const n = new Set(sel); n.has(c.id) ? n.delete(c.id) : n.add(c.id); setSel(n); }}>
              <input type="checkbox" className="clip-check" aria-label={`Select ${c.title}`} checked={sel.has(c.id)} onChange={() => {}} />
              <Thumb clip={c} />
              <div className="meta">
                <b title={c.title}>{c.title}</b>
                <span className="muted" style={{ fontSize: 12 }}>{c.days_left} day{c.days_left === 1 ? "" : "s"} left · {gb(c.bytes || 0)}{c.posted ? " · posted (stats kept)" : ""}</span>
                <button className="btn-ghost" onClick={(e) => { e.stopPropagation(); restore([c.id]); }}>Restore</button>
              </div>
            </div>))}
        </div>)}
      {ask && (
        <div className="modal-back" onClick={() => setAsk(false)}>
          <div className="card modal stack tight" role="dialog" aria-label="Empty now" onClick={(e) => e.stopPropagation()}>
            <h2>Remove {sel.size || rows.length} clip{(sel.size || rows.length) === 1 ? "" : "s"} for good?</h2>
            <span className="muted">This frees {gb(sel.size ? rows.filter((r) => sel.has(r.id)).reduce((n, r) => n + r.bytes, 0) : total)}. You can't restore them afterwards.</span>
            <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button className="btn-ghost" onClick={() => setAsk(false)}>Cancel</button>
              <button className="btn-danger" onClick={empty}>Empty now</button>
            </div>
          </div>
        </div>)}
    </div>
  );
}
