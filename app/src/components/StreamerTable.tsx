// Streamer discovery readout: sortable table + card view, permission editing, program linking.
import { useEffect, useMemo, useState } from "react";
import { api, PERMISSIONS } from "../api";

type Props = { gameId: number; streamers: any[]; selected: number[]; onSelect: (ids: number[]) => void; reload: () => void };
type PermKey = keyof typeof PERMISSIONS;

const COLS: [string, string][] = [
  ["profit_score", "Profit score"], ["display_name", "Streamer"], ["is_live", "Status"], ["current_viewers", "Viewers"],
  ["avg_viewers", "Avg (7d)"], ["earnings_tier", "Size (est.)"], ["permission_status", "Permission"],
];
const clippable = (s: any) => s.permission_status === "allowed" || s.permission_status === "program_member";
const bestProgram = (s: any) => [...(s.programs || [])].sort((a, b) => (b.payout_per_1k || 0) - (a.payout_per_1k || 0))[0];
const programText = (s: any) => {
  const p = bestProgram(s);
  return p ? `${p.name} · ${p.payout_per_1k != null ? `$${p.payout_per_1k}/1k` : "rate ?"}` : "none known";
};
const nice = (a: string) => ({ youtube_shorts: "Shorts", tiktok: "TikTok", instagram_reels: "Reels", x: "X" } as any)[a] || a;

export function Score({ v }: { v: number }) {
  const cls = v >= 60 ? "ok" : v >= 35 ? "warn" : "bad";
  return <b className={`score ${cls}`}>{Math.round(v ?? 0)}</b>;
}

function Perm({ s }: { s: any }) {
  const p = PERMISSIONS[s.permission_status as PermKey];
  return <span className={`pill ${s.permission_status}`}>{p.label}</span>;
}

export default function StreamerTable({ gameId, streamers, selected, onSelect, reload }: Props) {
  const [sort, setSort] = useState<[string, number]>(["profit_score", -1]);
  const [view, setView] = useState<"table" | "cards">("table");
  const [editing, setEditing] = useState<any>(null);
  const [hideBlocked, setHideBlocked] = useState(true);

  const rows = useMemo(() => {
    const r = streamers.filter((s) => !hideBlocked || s.permission_status !== "prohibited");
    const [k, dir] = sort;
    return [...r].sort((a, b) => ((a[k] ?? -1) > (b[k] ?? -1) ? dir : -dir));
  }, [streamers, sort, hideBlocked]);

  const toggle = (s: any) => {
    if (!clippable(s)) return setEditing(s);
    onSelect(selected.includes(s.id) ? selected.filter((x) => x !== s.id) : [...selected, s.id]);
  };

  return (
    <div className="stack tight">
      <div className="row between">
        <div className="row" style={{ gap: 8 }}>
          <button className={view === "table" ? "seg on" : "seg"} onClick={() => setView("table")}>Table</button>
          <button className={view === "cards" ? "seg on" : "seg"} onClick={() => setView("cards")}>Cards</button>
          <button className="check" style={{ background: "none", border: "none" }} onClick={() => setHideBlocked(!hideBlocked)}>
            <span className={hideBlocked ? "tick on" : "tick"} aria-hidden="true">{hideBlocked ? "✓" : ""}</span>Hide prohibited
          </button>
        </div>
      </div>

      {view === "table" ? (
        <div className="card table-box" style={{ padding: 0 }}>
          <table style={{ minWidth: 980 }}>
            <thead><tr><th />{COLS.map(([k, l]) => (
              <th key={k} className="sortable" onClick={() => setSort([k, sort[0] === k ? -sort[1] : -1])}>
                {l}{sort[0] === k ? (sort[1] < 0 ? " ▼" : " ▲") : ""}
              </th>))}
              <th>Program · payout</th><th>Best for</th><th /></tr></thead>
            <tbody>{rows.map((s) => {
              const on = selected.includes(s.id);
              return (
                <tr key={s.id} className={`${on ? "sel" : ""} ${clippable(s) ? "" : "blocked"}`}>
                  <td><button className={on ? "tick on" : "tick"} aria-label={`Select ${s.display_name}`} onClick={() => toggle(s)}>{on ? "✓" : ""}</button></td>
                  <td><Score v={s.profit_score} /></td>
                  <td>{s.avatar_url && <img className="avatar" src={s.avatar_url} alt="" />}<span style={{ fontWeight: 600 }}>{s.display_name}</span> <span className="muted">{s.platform}</span></td>
                  <td>{s.is_live ? <span className="pill live">LIVE</span> : <span className="muted">offline</span>}</td>
                  <td>{s.current_viewers?.toLocaleString() ?? "—"}</td>
                  <td>{s.avg_viewers ? Math.round(s.avg_viewers).toLocaleString() : "—"}</td>
                  <td>{s.earnings_tier}</td>
                  <td><Perm s={s} /></td>
                  <td>{programText(s)}</td>
                  <td className="muted">{(s.best_platforms || []).map(nice).join(", ") || "—"}</td>
                  <td><button onClick={() => setEditing(s)}>Edit</button></td>
                </tr>);
            })}</tbody>
          </table>
        </div>
      ) : (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
          {rows.map((s) => (
            <div key={s.id} className={`card scard ${selected.includes(s.id) ? "on" : ""} ${clippable(s) ? "" : "blocked"}`}>
              <div className="row between" style={{ width: "100%" }}>
                <span style={{ fontWeight: 700, fontSize: 16 }}>{s.display_name}</span><Score v={s.profit_score} />
              </div>
              <span className="muted">{s.platform} · {s.is_live ? "LIVE" : "offline"} · {s.current_viewers?.toLocaleString() ?? "—"} viewers</span>
              <Perm s={s} />
              <span>{programText(s)}</span>
              <div className="row" style={{ gap: 8 }}>
                <button className={selected.includes(s.id) ? "seg on" : "seg"} onClick={() => toggle(s)}>{selected.includes(s.id) ? "Selected" : "Select"}</button>
                <button onClick={() => setEditing(s)}>Edit</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {editing && <EditStreamer s={editing} gameId={gameId} onClose={() => { setEditing(null); reload(); }} />}
    </div>
  );
}

function EditStreamer({ s, gameId, onClose }: { s: any; gameId: number; onClose: () => void }) {
  const [status, setStatus] = useState(s.permission_status);
  const [note, setNote] = useState(s.permission_note || "");
  const [platforms, setPlatforms] = useState<string[]>(s.best_platforms || []);
  const [competition, setCompetition] = useState<string>(s.competition_count != null ? String(s.competition_count) : "");
  const [programs, setPrograms] = useState<any[]>([]);
  const [linked, setLinked] = useState<Set<number>>(new Set((s.programs || []).map((p: any) => p.id)));
  const [err, setErr] = useState("");
  useEffect(() => { api.get("/programs").then(setPrograms).catch(() => {}); }, []);

  const save = async () => {
    try {
      await api.patch(`/streamers/${s.id}`, {
        permission_status: status, permission_note: note, best_platforms: platforms,
        competition_count: competition === "" ? null : Number(competition), game_id: gameId,
      });
      onClose();
    } catch (e: any) { setErr(e.message); }
  };
  const link = async (pid: number) => {
    const on = !linked.has(pid);
    await (on ? api.post(`/streamers/${s.id}/programs/${pid}`) : api.del(`/streamers/${s.id}/programs/${pid}`));
    const n = new Set(linked); on ? n.add(pid) : n.delete(pid); setLinked(n);
    if (on && status === "unknown") setStatus("program_member");
  };
  const AVENUES = ["youtube_shorts", "tiktok", "instagram_reels", "x"];

  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body stack tight" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={`Edit ${s.display_name}`}>
        <h2>{s.display_name}</h2>
        <label className="field">Clipping permission
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {Object.entries(PERMISSIONS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select></label>
        <label className="field">Where did permission come from? (link, Discord message, program page)
          <input value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <label className="field">Other clippers already covering them (your estimate)
          <input type="number" min={0} value={competition} onChange={(e) => setCompetition(e.target.value)} /></label>
        <div className="field">Best posting avenues
          <div className="row" style={{ gap: 8 }}>{AVENUES.map((a) => (
            <button key={a} className={platforms.includes(a) ? "pill-btn on" : "pill-btn"}
              onClick={() => setPlatforms(platforms.includes(a) ? platforms.filter((x) => x !== a) : [...platforms, a])}>{nice(a)}</button>))}</div>
        </div>
        <div className="field">Rewards programs
          {programs.length === 0 && <span>No programs yet. Add them under Rewards Programs.</span>}
          <div className="row" style={{ gap: 8 }}>{programs.map((p) => (
            <button key={p.id} className={linked.has(p.id) ? "pill-btn on" : "pill-btn"} onClick={() => link(p.id)}>
              {p.name}{p.payout_per_1k != null ? ` · $${p.payout_per_1k}/1k` : ""}</button>))}</div>
        </div>
        {err && <span className="bad-text">{err}</span>}
        <div className="row"><button className="btn-primary" onClick={save}>Save</button><button className="btn-ghost" onClick={onClose}>Cancel</button></div>
      </div>
    </div>
  );
}
