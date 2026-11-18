import { useEffect, useState } from "react";
import { api } from "../api";

export default function Logs() {
  const [level, setLevel] = useState("");
  const [logs, setLogs] = useState<any[]>([]);
  const [failed, setFailed] = useState<any[]>([]);
  const load = () => {
    api.get(`/logs${level ? `?level=${level}` : ""}`).then(setLogs).catch(() => {});
    api.get("/jobs?active=false&limit=200").then((j) => setFailed(j.filter((x: any) => x.status === "failed"))).catch(() => {});
  };
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [level]);

  return (
    <div className="stack">
      <div className="row between"><h1>Logs</h1>
        <select aria-label="Level" value={level} onChange={(e) => setLevel(e.target.value)}>
          <option value="">All</option><option value="error">Errors</option><option value="warn">Warnings</option><option value="info">Info</option></select></div>
      {failed.length > 0 && (
        <section className="card stack tight"><h2>Failed jobs</h2>
          {failed.map((j) => (
            <details key={j.id}><summary>#{j.id} {j.kind} <button onClick={() => api.post(`/jobs/${j.id}/retry`).then(load)}>Retry</button></summary>
              <pre className="logs">{j.error}</pre></details>))}
        </section>)}
      <pre className="logs">{logs.length ? logs.map((l) => `${l.ts}  ${l.level.toUpperCase().padEnd(5)}  [${l.source}] ${l.message}`).join("\n") : "No log entries yet."}</pre>
    </div>
  );
}
