import { useEffect, useState } from "react";
import { api } from "../api";
import type { Page } from "../App";
import ClipGrid from "../components/ClipGrid";

const fmtEta = (s?: number | null) => (s == null ? "" : s > 3600 ? `${(s / 3600).toFixed(1)} h left` : `${Math.max(1, Math.ceil(s / 60))} min left`);
const JOB_LABEL: Record<string, string> = {
  project_tick: "Checking for new content", fetch_sources: "Finding clips and VODs", process_source: "Finding highlights",
  render_moment: "Rendering clip", cleanup: "Freeing up space",
};

function timeLeft(endsAt?: string) {
  if (!endsAt) return "—";
  const ms = new Date(endsAt.replace(" ", "T") + "Z").getTime() - Date.now();
  if (ms <= 0) return "—";
  const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4);
  return h >= 24 ? `${Math.floor(h / 24)} day ${h % 24} h` : `${h} h ${m} min`;
}

export default function Dashboard({ go }: { go: (p: Page) => void }) {
  const [d, setD] = useState<any>(null);
  const [projects, setProjects] = useState<any[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);

  const load = () => {
    api.get("/dashboard").then(setD).catch(() => {});
    api.get("/projects").then(setProjects).catch(() => {});
    api.get("/jobs?active=true").then((j) => setJobs(j.filter((x: any) => x.status === "running"))).catch(() => {});
  };
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, []);

  const act = (id: number, a: string) => api.post(`/projects/${id}/${a}`).then(load);
  const sum = (statuses: string[]) =>
    (d?.earnings || []).filter((e: any) => statuses.includes(e.status)).reduce((s: number, e: any) => s + e.amount, 0);
  const running = projects.filter((p) => p.status === "running").length;
  const usedPct = d ? Math.min(100, (d.disk.used_by_clipfarm / d.disk.cap) * 100) : 0;

  return (
    <div className="stack">
      <header className="row between">
        <div className="stack tight" style={{ gap: 4 }}>
          <h1>Home</h1>
          <span className="muted">{running} project{running === 1 ? "" : "s"} running · {d?.clips?.pending ?? 0} clips waiting for review</span>
        </div>
        <button className="btn-primary big" onClick={() => go("new")}>+ New Project</button>
      </header>

      <section className="tiles">
        <div className="tile"><b>{d?.clips?.total ?? 0}</b><span>Clips made</span></div>
        <div className="tile"><b>{d?.clips?.pending ?? 0}</b><span>Waiting for review</span></div>
        <div className="tile"><b>${sum(["estimated", "pending"]).toFixed(2)}</b><span>Estimated earnings</span></div>
        <div className="tile"><b>${sum(["approved", "paid"]).toFixed(2)}</b><span>Confirmed payouts</span></div>
        <div className="tile" style={{ gap: 6 }}>
          <b>{d ? (d.disk.used_by_clipfarm / 1e9).toFixed(1) : 0} / {d ? (d.disk.cap / 1e9).toFixed(0) : 0} GB</b>
          <span>Storage</span>
          <div className="bar thin"><div style={{ width: `${usedPct}%` }} /></div>
        </div>
      </section>

      <section className="card stack tight">
        <h2>Projects</h2>
        {projects.length === 0 ? <p className="muted">No projects yet. Start one to begin clipping.</p> : (
          <div className="table-box">
            <table style={{ minWidth: 640 }}>
              <thead><tr><th>Name</th><th>Game</th><th>Status</th><th>Time left</th><th>Clips</th><th /></tr></thead>
              <tbody>{projects.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td><td>{p.game_name}</td>
                  <td><span className={`pill ${p.status}`}>{p.status}</span></td>
                  <td className="muted">{["running", "paused"].includes(p.status) ? timeLeft(p.ends_at) : "—"}</td>
                  <td>{p.clip_count}</td>
                  <td className="actions"><div>
                    {p.status === "running" && <button onClick={() => act(p.id, "pause")}>Pause</button>}
                    {p.status === "paused" && <button onClick={() => act(p.id, "resume")}>Resume</button>}
                    {["running", "paused"].includes(p.status) && <button className="btn-danger" onClick={() => act(p.id, "stop")}>Stop</button>}
                    {p.status === "draft" && <button onClick={() => act(p.id, "start")}>Start</button>}
                  </div></td>
                </tr>))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card stack tight">
        <h2>Running jobs</h2>
        {jobs.length === 0 && <p className="muted">Nothing running right now.</p>}
        {jobs.map((j) => (
          <div key={j.id} className="job">
            <span>{JOB_LABEL[j.kind] || j.kind} <span className="muted">· #{j.id}</span></span>
            <div className="bar"><div style={{ width: `${Math.round(j.progress * 100)}%` }} /></div>
            <span className="eta">{fmtEta(j.eta_sec)}</span>
          </div>
        ))}
      </section>

      <section className="card stack tight">
        <ClipGrid title="My clips" onOpen={() => go("review")} />
      </section>
    </div>
  );
}
