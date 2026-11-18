// Rewards programs: table with an editing panel beside it (mockup layout).
import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { api } from "../api";

const EMPTY = {
  name: "", operator: "whop", url: "", payout_per_1k: "", currency: "USD", min_views: "", max_payout: "",
  allowed_platforms: "tiktok, youtube_shorts, instagram_reels", required_tags: "", required_credit: "{url}",
  disclosure: "", rules: "", source: "manual", active: true,
};
const OPERATORS: [string, string][] = [["whop", "Whop"], ["vyro", "Vyro"], ["clipping_net", "Clipping.net"], ["discord", "Streamer's Discord"], ["other", "Other"]];

export default function Programs() {
  const [rows, setRows] = useState<any[]>([]);
  const [edit, setEdit] = useState<any>(null);
  const [err, setErr] = useState("");
  const load = () => api.get("/programs").then(setRows).catch(() => {});
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!edit.name.trim()) return setErr("Give the program a name.");
    const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
    const num = (v: any) => (v === "" || v == null ? null : Number(v));
    const body = { ...edit, payout_per_1k: num(edit.payout_per_1k), min_views: num(edit.min_views), max_payout: num(edit.max_payout),
      allowed_platforms: split(edit.allowed_platforms), required_tags: split(edit.required_tags) };
    try {
      await (edit.id ? api.put(`/programs/${edit.id}`, body) : api.post("/programs", body));
      setEdit(null); setErr(""); load();
    } catch (e: any) { setErr(e.message); }
  };
  const open = (r: any) => setEdit({ ...r, payout_per_1k: r.payout_per_1k ?? "", min_views: r.min_views ?? "", max_payout: r.max_payout ?? "",
    allowed_platforms: JSON.parse(r.allowed_platforms || "[]").join(", "), required_tags: JSON.parse(r.required_tags || "[]").join(", "),
    active: !!r.active });
  const F = (k: string, label: string, ph = "") => (
    <label className="field">{label}<input placeholder={ph} value={edit[k] ?? ""} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></label>
  );

  return (
    <div className="stack">
      <header className="row between">
        <h1>Rewards programs</h1>
        <button className="btn-primary" onClick={() => { setErr(""); setEdit({ ...EMPTY }); }}>+ Add program</button>
      </header>
      <p className="muted" style={{ maxWidth: 820 }}>Add the clipping programs you've joined. Rates change often, so check each program's page and update them here. Link streamers to programs from the streamer list.</p>

      <div className="split">
        <div className="card table-box" style={{ flex: "2 1 560px", minWidth: 0, padding: 0 }}>
          {rows.length === 0 ? <p className="muted" style={{ padding: 18 }}>No programs yet. Click "Add program" to add one you've joined.</p> : (
            <table style={{ minWidth: 640 }}>
              <thead><tr><th>Program</th><th>Run by</th><th>Payout / 1k views</th><th>Min views</th><th>Streamers</th><th>Last checked</th><th /></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id} className={r.active ? "" : "blocked"}>
                  <td style={{ fontWeight: 600 }}>{r.url ? <a href={r.url} onClick={(e) => { e.preventDefault(); openUrl(r.url); }}>{r.name}</a> : r.name}</td>
                  <td>{OPERATORS.find(([k]) => k === r.operator)?.[1] ?? r.operator}</td>
                  <td>{r.payout_per_1k != null ? `$${r.payout_per_1k} ${r.currency}` : "?"}</td>
                  <td>{r.min_views?.toLocaleString() ?? "—"}</td>
                  <td className="soft">{r.streamers.map((s: any) => s.display_name).join(", ") || "—"}</td>
                  <td className="muted">{r.verified_at?.slice(0, 10)}</td>
                  <td><button onClick={() => { setErr(""); open(r); }}>Edit</button></td>
                </tr>))}</tbody>
            </table>
          )}
        </div>

        {edit && (
          <section className="card stack tight" style={{ flex: "1 1 320px", minWidth: 0 }}>
            <div className="row between">
              <h2>{edit.id ? "Edit program" : "New program"}</h2>
              <button aria-label="Close" style={{ background: "none", border: "none", fontSize: 18 }} onClick={() => setEdit(null)}>×</button>
            </div>
            {F("name", "Name", "e.g. Streamer clipping program")}
            <label className="field">Run by<select value={edit.operator} onChange={(e) => setEdit({ ...edit, operator: e.target.value })}>
              {OPERATORS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            {F("url", "Program page link", "https://")}
            <div className="options" style={{ gap: 10 }}>{F("payout_per_1k", "Payout per 1k views")}{F("min_views", "Minimum views")}</div>
            {F("required_tags", "Required hashtags (comma separated)", "#tag1, #tag2")}
            {F("required_credit", "On-screen credit ({url} = streamer link, {name} = streamer name)")}
            {F("disclosure", "Required disclosure text", "e.g. #ad")}
            {F("allowed_platforms", "Allowed platforms (comma separated)")}
            <label className="field">Rules<textarea rows={3} placeholder="Paste the program's rules here" value={edit.rules ?? ""} onChange={(e) => setEdit({ ...edit, rules: e.target.value })} /></label>
            <button className="check" style={{ background: "none", border: "none", padding: 0 }} onClick={() => setEdit({ ...edit, active: !edit.active })}>
              <span className={edit.active ? "tick on" : "tick"} aria-hidden="true">{edit.active ? "✓" : ""}</span>Active</button>
            {err && <span className="bad-text">{err}</span>}
            <div className="row" style={{ gap: 8 }}>
              <button className="btn-primary" onClick={save}>Save</button>
              <button className="btn-ghost" onClick={() => setEdit(null)}>Cancel</button>
              {edit.id && <button className="btn-ghost btn-danger" onClick={() => api.del(`/programs/${edit.id}`).then(() => { setEdit(null); load(); })}>Delete</button>}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
