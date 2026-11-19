import { useEffect, useState } from "react";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import { invoke } from "@tauri-apps/api/core";
import { api, initApi, onConnection, type ConnState } from "./api";
import { Brand } from "./components/Icons";
import Setup from "./pages/Setup";
import Dashboard from "./pages/Dashboard";
import NewProject from "./pages/NewProject";
import Review from "./pages/Review";
import Programs from "./pages/Programs";
import Settings from "./pages/Settings";
import Logs from "./pages/Logs";
import Health from "./pages/Health";
import Campaigns from "./pages/Campaigns";
import Earnings from "./pages/Earnings";
import Submissions from "./pages/Submissions";

export type Page = "dashboard" | "new" | "review" | "campaigns" | "submissions" | "earnings" | "programs" | "settings" | "health" | "logs";

const NAV: [Page, string][] = [
  ["dashboard", "Home"], ["new", "New Project"], ["review", "Review Clips"], ["campaigns", "Campaigns"],
  ["submissions", "Post & Submit"], ["earnings", "Earnings"], ["programs", "Rewards Programs"],
  ["settings", "Settings"], ["health", "Setup Health"], ["logs", "Logs"],
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
  const [conn, setConn] = useState<ConnState>("connecting");
  const [connDetail, setConnDetail] = useState("");
  const [banner, setBanner] = useState<string | null>(null);
  const [settingsTab, setSettingsTab] = useState<string | undefined>(undefined);

  useEffect(() => onConnection((s, d) => { setConn(s); if (d) setConnDetail(d); }), []);

  // Start-up checks run quietly in the engine; show one banner if something is red.
  useEffect(() => {
    if (!ready) return;
    const load = () => api.get("/health/checks").then((h) => setBanner(h.summary?.banner ?? null)).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [ready]);

  const go = (p: Page, tab?: string) => { setSettingsTab(tab); setPage(p); };

  // Updates install over the top of the current version, so keys and settings are untouched.
  const [update, setUpdate] = useState<any>(null);
  const [updating, setUpdating] = useState("");
  useEffect(() => {
    if (!ready) return;
    (async () => {
      try {
        if (!(await invoke<boolean>("updates_enabled"))) return;
        const { check } = await import("@tauri-apps/plugin-updater");
        const u = await check();
        if (u) setUpdate(u);
      } catch { /* offline or not signed for updates */ }
    })();
  }, [ready]);
  const installUpdate = async () => {
    try {
      setUpdating("Downloading…");
      await update.downloadAndInstall();
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    } catch (e: any) { setUpdating(`Update failed: ${e?.message || e}`); }
  };

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
        if (ev.type === "health") { sendNotification({ title: "ClipFarm setup", body: ev.message }); api.get("/health/checks").then((h) => setBanner(h.summary?.banner ?? null)).catch(() => {}); }
        if (ev.type === "posted") sendNotification({ title: "Posted", body: "Submit it to the campaign from Post & Submit." });
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
      {(conn === "reconnecting" || conn === "failed") && (
        <div className="reconnect" role="status" aria-live="polite">
          {conn === "reconnecting" ? <><div className="spinner small" /> Reconnecting to the ClipFarm engine… {connDetail}</>
            : <>The ClipFarm engine stopped and couldn't be restarted. {connDetail} <button onClick={() => location.reload()}>Try again</button></>}
        </div>
      )}
      <nav className="side" aria-label="Main">
        <Brand />
        {NAV.map(([id, label]) => (
          <button key={id} className={page === id ? "nav active" : "nav"} onClick={() => go(id)}>{label}</button>
        ))}
        <div className="engine">
          <span className="muted" style={{ fontSize: 12 }}>Engine</span>
          <span className="engine-status"><span className="dot" style={conn === "connected" ? undefined : { background: "#f5a524" }} />
            {conn === "connected" ? "Running" : "Reconnecting"}{encoder ? ` · ${encoder}` : ""}</span>
        </div>
      </nav>
      <main className="content">
        {update && (
          <div className="banner" style={{ background: "#13283a", borderColor: "#24507a", color: "#bfe0ff" }}>
            <span>ClipFarm {update.version} is available. It installs over this version; your keys and settings stay.</span>
            <button className="btn-primary" onClick={installUpdate} disabled={!!updating}>{updating || "Update now"}</button>
          </div>
        )}
        {banner && page !== "health" && (
          <div className="banner" role="alert">
            <span>{banner}</span>
            <button className="btn-ghost" onClick={() => go("health")}>Fix my setup</button>
          </div>
        )}
        {page === "dashboard" && <Dashboard go={setPage} />}
        {page === "new" && <NewProject done={() => setPage("dashboard")} />}
        {page === "review" && <Review />}
        {page === "programs" && <Programs />}
        {page === "settings" && <Settings initialTab={settingsTab} />}
        {page === "logs" && <Logs />}
        {page === "health" && <Health go={go} />}
        {page === "campaigns" && <Campaigns />}
        {page === "earnings" && <Earnings />}
        {page === "submissions" && <Submissions />}
      </main>
    </div>
  );
}
