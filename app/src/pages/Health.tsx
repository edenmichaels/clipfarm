// Setup Health: every check with a green/yellow/red light, a plain-English explanation and a one-click fix.
import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { api } from "../api";
import type { Page } from "../App";

// Fixes the UI does itself (open a screen or a web page); everything else is sent to the engine.
const UI_FIXES: Record<string, { label: string; run: (go: (p: Page, tab?: string) => void) => Promise<void> | void }> = {
  open_connections: { label: "Open Connections", run: (go) => go("settings", "Connections") },
  open_settings: { label: "Open Settings", run: (go) => go("settings", "General") },
  open_campaigns: { label: "Open Campaigns", run: (go) => go("campaigns") },
  reconnect_youtube: { label: "Reconnect YouTube", run: async () => { const { url } = await api.get("/oauth/youtube/start"); await openUrl(url); } },
  open_ollama: { label: "Get Ollama", run: () => openUrl("https://ollama.com/download") },
  open_youtube_api_library: { label: "Open API Library", run: () => openUrl("https://console.cloud.google.com/apis/library/youtube.googleapis.com") },
  reinstall: { label: "Download installer", run: () => openUrl("https://github.com/edenmichaels/clipfarm/releases/latest") },
};
const ENGINE_FIX_LABEL: Record<string, string> = {
  migrate: "Update database", cleanup: "Free up space", reset_vault: "Make a fresh backup",
  download_model: "Download model", enable_transcription: "Turn on", pull_llm: "Download AI model", recheck: "Check again",
};

export default function Health({ go }: { go: (p: Page, tab?: string) => void }) {
  const [checks, setChecks] = useState<any[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = () => api.get("/health/checks").then((h) => { setChecks(h.checks); setSummary(h.summary); }).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);

  const runAll = async () => {
    setBusy(true); setMsg("");
    try { const h = await api.post("/health/run"); setChecks(h.checks); setSummary(h.summary); setMsg("All checks re-run."); }
    catch (e: any) { setMsg(e.message); }
    setBusy(false);
  };
  const fix = async (id: string) => {
    setMsg("");
    try {
      if (UI_FIXES[id]) return await UI_FIXES[id].run(go);
      setBusy(true);
      const r = await api.post(`/health/fix/${id}`);
      setChecks(r.checks); setSummary(r.summary); setMsg(r.message);
    } catch (e: any) { setMsg(e.message); }
    setBusy(false);
  };
  const copyDiagnostics = async () => {
    try {
      const d = await api.get("/health/diagnostics");
      await navigator.clipboard.writeText(d.text);
      setMsg(`Diagnostics copied (keys removed). Also saved to ${d.path}`);
    } catch (e: any) { setMsg(e.message); }
  };

  return (
    <div className="stack">
      <header className="row between">
        <div className="stack tight" style={{ gap: 4 }}>
          <h1>Setup health</h1>
          <span className="muted">These checks also run quietly every time ClipFarm starts.</span>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn-ghost" onClick={copyDiagnostics}>Copy diagnostics</button>
          <button className="btn-primary" onClick={runAll} disabled={busy}>{busy ? "Checking…" : "Fix my setup"}</button>
        </div>
      </header>
      {summary && (
        <div className="row" style={{ gap: 14 }}>
          <span><span className="light green" /> {summary.green} OK</span>
          <span><span className="light yellow" /> {summary.yellow} worth a look</span>
          <span><span className="light red" /> {summary.red} need fixing</span>
        </div>
      )}
      {msg && <span className="muted">{msg}</span>}
      <section className="card" style={{ padding: 0 }}>
        {checks.length === 0 && <p className="muted" style={{ padding: 16 }}>No results yet. Click "Fix my setup".</p>}
        {checks.map((c) => (
          <div key={c.id} className="health-row">
            <span className={`light ${c.status}`} aria-label={c.status} />
            <div className="stack tight grow" style={{ gap: 2 }}>
              <b>{c.title}</b>
              <span className={c.status === "green" ? "muted" : ""}>{c.message}</span>
              <span className="muted small-text">Checked {c.checked_at}</span>
            </div>
            {c.fix && (
              <button className={c.status === "red" ? "btn-primary" : "btn-ghost"} onClick={() => fix(c.fix)} disabled={busy}>
                {UI_FIXES[c.fix]?.label ?? ENGINE_FIX_LABEL[c.fix] ?? "Fix"}
              </button>
            )}
          </div>
        ))}
      </section>
    </div>
  );
}
