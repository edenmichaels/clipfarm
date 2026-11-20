// Home: one "New Project" button, running projects with progress, clips waiting for review,
// clips to submit and earnings this week. Nothing else above the fold.
import { useEffect, useState } from "react";
import { api, aud } from "../api";
import type { Page } from "../App";

function timeLeft(endsAt?: string) {
  if (!endsAt) return "";
  const ms = new Date(endsAt.replace(" ", "T") + "Z").getTime() - Date.now();
  if (ms <= 0) return "finishing";
  const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4);
  return h >= 24 ? `${Math.floor(h / 24)} days ${h % 24} h left` : h ? `${h} h ${m} min left` : `${m} min left`;
}

export default function Dashboard({ go }: { go: (p: Page, tab?: string) => void }) {
  const [d, setD] = useState<any>(null);
  const [details, setDetails] = useState(false);

  const load = () => api.get("/dashboard").then(setD).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);

  const act = (id: number, a: string) => api.post(`/projects/${id}/${a}`).then(load);
  const projects: any[] = d?.projects_progress ?? [];
  const toReview = d?.clips?.pending ?? 0;
  const toSubmit = d?.to_submit ?? 0;

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Home</h1>
          <p className="muted">{projects.length ? `${projects.length} project${projects.length === 1 ? "" : "s"} running` : "Nothing running right now"}</p>
        </div>
        <button className="btn-primary big" onClick={() => go("new")}>New Project</button>
      </header>

      <section className="tiles three">
        <button className="tile link" onClick={() => go("clips", "To review")}>
          <b>{toReview}</b><span>{toReview === 1 ? "clip" : "clips"} waiting for review</span>
        </button>
        <button className="tile link" onClick={() => go("clips", "Post & Submit")}>
          <b>{toSubmit}</b><span>{toSubmit === 1 ? "clip" : "clips"} to submit to a campaign</span>
        </button>
        <button className="tile link" onClick={() => go("earnings")}>
          <b>{aud(d?.earnings_week_aud ?? 0)}</b><span>earned this week (estimate)</span>
        </button>
      </section>

      <section className="card stack tight">
        <h2>Running projects</h2>
        {projects.length === 0 ? (
          <div className="empty">
            <p>No projects running.</p>
            <p className="muted">Press <b>New Project</b> to pick a game and streamers. ClipFarm fills in your accounts and campaign rules for you.</p>
          </div>
        ) : projects.map((p) => (
          <div key={p.id} className="project-row">
            <div className="grow">
              <div className="row between">
                <b>{p.name}</b>
                <span className="muted">{p.status === "paused" ? "Paused" : timeLeft(p.ends_at)} · {p.clips} clip{p.clips === 1 ? "" : "s"}</span>
              </div>
              <div className="bar" aria-label={`${Math.round((p.progress ?? 0) * 100)}% done`}><div style={{ width: `${Math.round((p.progress ?? 0) * 100)}%` }} /></div>
            </div>
            <div className="row" style={{ gap: 6 }}>
              {p.status === "running" && <button onClick={() => act(p.id, "pause")}>Pause</button>}
              {p.status === "paused" && <button onClick={() => act(p.id, "resume")}>Resume</button>}
              <button className="btn-quiet" onClick={() => window.confirm(`Stop "${p.name}"? Clips already made are kept.`) && act(p.id, "stop")}>Stop</button>
            </div>
          </div>
        ))}
      </section>

      <button className="link-btn" onClick={() => setDetails(!details)}>{details ? "Hide details" : "Show details"}</button>
      {details && d && (
        <section className="card stack tight">
          <div className="row" style={{ gap: 24, flexWrap: "wrap" }}>
            <span><b>{d.clips?.total ?? 0}</b> <span className="muted">clips made in total</span></span>
            <span><b>{(d.disk.used_by_clipfarm / 1e9).toFixed(1)} / {(d.disk.cap / 1e9).toFixed(0)} GB</b> <span className="muted">storage used</span></span>
            <span><b>{d.jobs_running?.length ?? 0}</b> <span className="muted">jobs working now</span></span>
          </div>
        </section>
      )}
    </div>
  );
}
