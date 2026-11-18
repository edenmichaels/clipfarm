import { useEffect, useState } from "react";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import { api, initApi } from "./api";
import { Brand } from "./components/Icons";
import Setup from "./pages/Setup";
import Dashboard from "./pages/Dashboard";
import NewProject from "./pages/NewProject";
import Review from "./pages/Review";
import Programs from "./pages/Programs";
import Settings from "./pages/Settings";
import Logs from "./pages/Logs";

export type Page = "dashboard" | "new" | "review" | "programs" | "settings" | "logs";

const NAV: [Page, string][] = [
  ["dashboard", "Home"], ["new", "New Project"], ["review", "Review Clips"],
  ["programs", "Rewards Programs"], ["settings", "Settings"], ["logs", "Logs"],
];

/** Apply the user's accent colour app-wide. */
export function applyAccent(color?: string) {
  if (color) document.documentElement.style.setProperty("--accent", color);
}

export default function App() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [setupDone, setSetupDone] = useState(false);
  const [page, setPage] = useState<Page>("dashboard");
  const [encoder, setEncoder] = useState("");

  useEffect(() => {
    initApi()
      .then(() => api.get("/settings"))
      .then((s) => {
        applyAccent(s.accent);
        setSetupDone(!!s.setup_complete && !!s.disclaimer_accepted);
        setReady(true);
        api.get("/system").then((sys) => {
          const e = sys.encoders.find((x: any) => x.available);
          setEncoder(e ? e.label : "");
        }).catch(() => {});
      })
      .catch((e) => setError(String(e.message || e)));
  }, []);

  // Backend events -> desktop toasts (also while minimised to the tray).
  useEffect(() => {
    if (!ready) return;
    const es = api.events();
    es.onmessage = async (m) => {
      try {
        const ev = JSON.parse(m.data);
        let ok = await isPermissionGranted();
        if (!ok) ok = (await requestPermission()) === "granted";
        if (!ok) return;
        if (ev.type === "clip_ready") sendNotification({ title: "New clip ready", body: `${ev.streamer}: ${ev.title}` });
        if (ev.type === "error") sendNotification({ title: "ClipFarm problem", body: ev.message });
        if (ev.type === "project" && ev.status === "completed") sendNotification({ title: "Project finished", body: `Project #${ev.project_id} is done` });
      } catch { /* ignore malformed events */ }
    };
    return () => es.close();
  }, [ready]);

  if (error) return (
    <div className="center">
      <h1>ClipFarm couldn't start</h1>
      <p className="muted">{error}</p>
      <button className="btn-primary" onClick={() => location.reload()}>Try again</button>
    </div>
  );
  if (!ready) return <div className="center"><div className="spinner" /><p className="muted">Starting ClipFarm…</p></div>;
  if (!setupDone) return <Setup onDone={() => setSetupDone(true)} />;

  return (
    <div className="shell">
      <nav className="side" aria-label="Main">
        <Brand />
        {NAV.map(([id, label]) => (
          <button key={id} className={page === id ? "nav active" : "nav"} onClick={() => setPage(id)}>{label}</button>
        ))}
        <div className="engine">
          <span className="muted" style={{ fontSize: 12 }}>Engine</span>
          <span className="engine-status"><span className="dot" />Running{encoder ? ` · ${encoder}` : ""}</span>
        </div>
      </nav>
      <main className="content">
        {page === "dashboard" && <Dashboard go={setPage} />}
        {page === "new" && <NewProject done={() => setPage("dashboard")} />}
        {page === "review" && <Review />}
        {page === "programs" && <Programs />}
        {page === "settings" && <Settings />}
        {page === "logs" && <Logs />}
      </main>
    </div>
  );
}
