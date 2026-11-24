// Editor preset bar + variations (v0.8.8).
// Six preset cards with a live thumbnail of THIS clip in each style; click to apply; "Save as my style" to keep a tweak.
// "Make variations": N slightly different edits (new hook + trim / layout / colour / punch-in), previewed as cheap
// low-res renders; only kept ones become real clips (full render) in My Clips.
import { useEffect, useState } from "react";
import { api, aud } from "../api";

export function PresetBar({ cid, head, current, onApplied, onVariations }:
  { cid: number; head: number; current?: string; onApplied: (r: any) => void; onVariations: (n: number, preset: string) => void }) {
  const [presets, setPresets] = useState<any[]>([]);
  const [n, setN] = useState(4);
  const [which, setWhich] = useState<string>("selected");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get("/presets").then(setPresets).catch((e) => setMsg(e.message)); }, []);
  const apply = async (key: string) => {
    try { onApplied(await api.post(`/edit/${cid}/style`, { key })); setMsg(""); } catch (e: any) { setMsg(e.message); }
  };
  const saveMine = async () => {
    const base = presets.find((p) => p.key === current);
    if (!base) return setMsg("Apply a preset first.");
    const name = prompt("Name for your style", `${base.name} (mine)`);
    if (!name) return;
    try { await api.post("/styles", { name, style: { ...base, base: base.key } }); setMsg(`Saved "${name}" to your styles (Styles tab to tweak it).`); }
    catch (e: any) { setMsg(e.message); }
  };
  const selectedKey = presets.some((p) => p.key === current) ? current! : "streamer_corner";
  return (
    <div className="stack tight" style={{ gap: 4 }}>
      <div className="row" style={{ gap: 8, alignItems: "stretch", flexWrap: "nowrap" }}>
        <div className="preset-bar" role="listbox" aria-label="Presets">
          {presets.map((p) => (
            <button key={p.key} role="option" aria-selected={current === p.key} className={current === p.key ? "preset-card on" : "preset-card"}
              onClick={() => apply(p.key)} title={p.description}>
              <img src={api.presetThumbUrl(cid, p.key, head)} alt="" loading="lazy" onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")} />
              <b>{p.name}</b><span>{p.description}</span>
            </button>))}
        </div>
        <div className="var-tools card">
          <b>Make variations</b>
          <label className="row" style={{ gap: 6 }}>How many
            <select value={n} onChange={(e) => setN(Number(e.target.value))}>{Array.from({ length: 10 }, (_, i) => i + 1).map((k) => <option key={k}>{k}</option>)}</select></label>
          <label className="row" style={{ gap: 6 }}>Preset
            <select value={which} onChange={(e) => setWhich(e.target.value)}>
              <option value="selected">{presets.find((p) => p.key === selectedKey)?.name || "Selected"}</option>
              <option value="mix">Mix presets</option>
              {presets.filter((p) => p.key !== selectedKey).map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
            </select></label>
          <button className="btn-primary" onClick={() => onVariations(n, which === "selected" ? selectedKey : which)}>Make variations</button>
          <button className="btn-quiet small" onClick={saveMine}>Save as my style</button>
        </div>
      </div>
      {msg && <span className="muted small-text">{msg}</span>}
    </div>
  );
}

export function Variations({ cid, rows, reload, onMsg }: { cid: number; rows: any[]; reload: () => void; onMsg: (m: string) => void }) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const previews = rows.filter((r) => r.status === "preview");
  const kept = rows.filter((r) => r.status === "kept");
  useEffect(() => {                                       // previews render in the background: refresh until done
    if (!previews.some((r) => !r.preview_path)) return;
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [rows]);
  if (!rows.length) return null;
  const keep = async (ids: number[], exp = false) => {
    if (!ids.length) return;
    try {
      const r = await api.post("/variations/keep", { ids, export: exp });
      onMsg(`Kept ${r.length} variation${r.length === 1 ? "" : "s"}: rendering them into My Clips.` +
        (r.length ? ` Scores: ${r.map((k: any) => `${k.virality ?? "–"}${k.projected_aud != null ? ` / ${aud(k.projected_aud)}` : ""}`).join(", ")}` : ""));
      setSel(new Set()); reload();
    } catch (e: any) { onMsg(e.message); }
  };
  const discardRest = async () => {
    try { const r = await api.post(`/edit/${cid}/variations/discard`, {}); onMsg(`Discarded ${r.discarded}.`); setSel(new Set()); reload(); }
    catch (e: any) { onMsg(e.message); }
  };
  return (
    <div className="stack tight card" style={{ marginTop: 8 }}>
      <div className="row between">
        <b>Variations</b>
        <span className="row" style={{ gap: 6 }}>
          <button onClick={() => keep(previews.map((r) => r.id))} disabled={!previews.length}>Keep all</button>
          <button onClick={() => keep([...sel])} disabled={!sel.size}>Keep selected{sel.size ? ` (${sel.size})` : ""}</button>
          <button className="btn-quiet" onClick={discardRest} disabled={!previews.length}>Discard the rest</button>
        </span>
      </div>
      <div className="var-grid">
        {previews.map((r) => (
          <div key={r.id} className={sel.has(r.id) ? "var-card on" : "var-card"}>
            {r.preview_path ? <video src={api.mediaUrl(r.preview_path)} muted loop playsInline controls preload="metadata" />
              : <div className="ph row" style={{ justifyContent: "center" }}><div className="spinner small" /></div>}
            <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={sel.has(r.id)}
              onChange={() => { const s = new Set(sel); s.has(r.id) ? s.delete(r.id) : s.add(r.id); setSel(s); }} />
              <b className="small-text">V{r.n}</b></label>
            <span className="small-text">{r.label}</span>
            <span className="muted tiny" title={r.hook}>“{r.hook}”</span>
            <span className="row" style={{ gap: 4 }}>
              <button className="btn-quiet small" onClick={() => keep([r.id])}>Keep</button>
              <button className="btn-quiet small" onClick={() => keep([r.id], true)}>Export</button>
            </span>
          </div>))}
      </div>
      {kept.length > 0 && <span className="muted small-text">Kept: {kept.map((k) => `V${k.n}`).join(", ")} (in My Clips, linked to this clip)</span>}
    </div>
  );
}
