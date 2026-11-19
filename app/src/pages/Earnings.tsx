// Earnings: projected vs actual per campaign/platform, pending/approved/paid, and the model refit.
import { useEffect, useState } from "react";
import { api, aud, PLATFORM_LABEL } from "../api";

export default function Earnings() {
  const [e, setE] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [rate, setRate] = useState("");
  const load = () => api.get("/earnings").then((x) => { setE(x); setRate(String(x.usd_to_aud)); }).catch((err) => setMsg(err.message));
  useEffect(() => { load(); }, []);
  if (!e) return <p className="muted">{msg || "Loading…"}</p>;

  const total = (s: string) => e.by_status.find((r: any) => r.status === s)?.aud ?? 0;
  const saveRate = () => api.put("/settings", { usd_to_aud: Number(rate) }).then(() => api.post("/projections/refresh")).then(load).then(() => setMsg("Exchange rate saved; projections updated.")).catch((err) => setMsg(err.message));
  const refit = () => api.post("/forecast/refit").then((r) => { setMsg(r.fitted ? "Model refitted from your own results." : r.reason); load(); }).catch((err) => setMsg(err.message));

  return (
    <div className="stack">
      <header className="row between">
        <div className="stack tight" style={{ gap: 4 }}>
          <h1>Earnings</h1>
          <span className="muted">All amounts in A$. Projections are estimates; actual money comes from what you record as approved or paid.</span>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <label className="line">USD → AUD <input style={{ width: 70 }} value={rate} onChange={(x) => setRate(x.target.value)} /></label>
          <button className="btn-ghost" onClick={saveRate}>Save</button>
          <button className="btn-ghost" onClick={refit}>Refit from my results</button>
        </div>
      </header>
      {msg && <span className="muted">{msg}</span>}

      <div className="tiles">
        {["pending", "approved", "paid", "rejected"].map((s) => (
          <div key={s} className="tile"><span className="muted">{s[0].toUpperCase() + s.slice(1)}</span><b>{aud(total(s))}</b></div>))}
      </div>

      <section className="card stack tight">
        <h2 className="accent">Estimated vs actual</h2>
        <span className="muted">Estimated = what ClipFarm projected when each clip was posted. {e.model?.platforms ? `Model refitted ${e.model.fitted_at}.` : "Using the default model until you have 2+ weeks of results."}</span>
        {e.est_vs_actual.length === 0 ? <span className="muted">Nothing posted yet.</span> : (
          <table>
            <thead><tr><th>Campaign</th><th>Platform</th><th className="num">Posts</th><th className="num">Estimated</th><th className="num">Actual (approved + paid)</th><th className="num">Pending</th></tr></thead>
            <tbody>{e.est_vs_actual.map((r: any, i: number) => (
              <tr key={i}><td>{r.program}</td><td>{PLATFORM_LABEL[r.platform] ?? r.platform}</td><td className="num">{r.posts}</td>
                <td className="num">{aud(r.estimated_aud)}</td><td className="num">{aud(r.actual_aud)}</td><td className="num">{aud(r.pending_aud)}</td></tr>))}</tbody>
          </table>)}
      </section>

      <section className="card stack tight">
        <h2 className="accent">Projected (next 7 / 30 days)</h2>
        {e.projected.length === 0 ? <span className="muted">No projections yet. Link campaigns to streamers on the Campaigns screen.</span> : (
          <table>
            <thead><tr><th>Campaign</th><th>Streamer</th><th>Platform</th><th className="num">Per day</th><th className="num">7 days (low–high)</th><th className="num">30 days (low–high)</th></tr></thead>
            <tbody>{e.projected.map((r: any, i: number) => (
              <tr key={i}><td>{r.program}</td><td>{r.streamer}</td><td>{PLATFORM_LABEL[r.platform] ?? r.platform}</td>
                <td className="num">{aud(r.per_day_likely)}</td>
                <td className="num">{aud(r.d7_likely, 0)} <span className="muted">({aud(r.d7_low, 0)}–{aud(r.d7_high, 0)})</span></td>
                <td className="num">{aud(r.d30_likely, 0)} <span className="muted">({aud(r.d30_low, 0)}–{aud(r.d30_high, 0)})</span></td></tr>))}</tbody>
          </table>)}
      </section>
    </div>
  );
}
