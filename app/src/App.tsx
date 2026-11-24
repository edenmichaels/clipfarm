import { useEffect, useState } from "react";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import { invoke } from "@tauri-apps/api/core";
import { api, initApi, onConnection, type ConnState } from "./api";
import { Brand } from "./components/Icons";
import Setup from "./pages/Setup";
import Dashboard from "./pages/Dashboard";
import NewProject from "./pages/NewProject";
import MyClips from "./pages/MyClips";
import Editor from "./pages/Editor";
import Settings from "./pages/Settings";
import Earnings from "./pages/Earnings";
import { listenForImports } from "./lib/campaignBrowser";

/** The six screens in the menu. */
export type MainPage = "dashboard" | "new" | "editor" | "clips" | "earnings" | "settings";
/** Older screen names still work and open where that screen lives now. */
export type Page = MainPage | "review" | "submissions" | "campaigns" | "programs" | "health" | "logs";

const NAV: [MainPage, string][] = [
  ["editor", "Editor"], ["dashboard", "Home"], ["new", "New Project"], ["clips", "My Clips"], ["earnings", "Earnings"],
  ["settings", "Settings"],
];

/** Where older screens moved to (v0.3 menu; v0.6: Review became the Editor). */
const MOVED: Record<string, [MainPage, string]> = {
  review: ["editor", ""], submissions: ["clips", "Post & Submit"],
  campaigns: ["settings", "Campaigns"], programs: ["settings", "Rewards programs"],
  health: ["settings", "Setup health"], logs: ["settings", "Logs"],
};

/** Apply the user's accent colour app-wide. */
export function applyAccent(color?: string) {
  if (color) document.documentElement.style.setProperty("--accent", color);
}

export default function App() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [setupDone, setSetupDone] = useState(false);
  const [page, setPage] = useState<MainPage>("editor");     // v0.8.3: the Editor is the start screen
  const [encoder, setEncoder] = useState("");
  const [paused, setPaused] = useState(false);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [connDetail, setConnDetail] = useState("");
  const [banner, setBanner] = useState<string | null>(null);
  const [tab, setTab] = useState<string | undefined>(undefined);
  const [toast, setToast] = useState("");

  useEffect(() => onConnection((s, d) => { setConn(s); if (d) setConnDetail(d); }), []);

  // Start-up checks run quietly in the engine; show one banner if something is red.
  useEffect(() => {
    if (!ready) return;
    const load = () => api.get("/health/checks").then((h) => setBanner(h.summary?.banner ?? null)).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [ready]);

  const go = (p: Page, t?: string) => {
    const [main, movedTab] = MOVED[p] ?? [p as MainPage, undefined];
    setTab(t ?? movedTab); setPage(main);
  };

  // "Import this page" in the campaign browser -> engine -> summary toast here.
  useEffect(() => {
    if (!ready) return;
    listenForImports((m) => { setToast(m); window.setTimeout(() => setToast(""), 9000); });
  }, [ready]);

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

  // Engine paused? (also changed from the tray menu, which sends "engine-paused")
  useEffect(() => {
    if (!ready) return;
    const load = () => api.get("/engine/status").then((st) => { setPaused(!!st.paused); invoke("set_engine_paused", { paused: !!st.paused }).catch(() => {}); }).catch(() => {});
    load();
    const t = setInterval(load, 15_000);
    let un: (() => void) | undefined;
    import("@tauri-apps/api/event").then(({ listen }) => listen<boolean>("engine-paused", (e) => setPaused(!!e.payload)))
      .then((f) => { un = f; }).catch(() => {});
    return () => { clearInterval(t); un?.(); };
  }, [ready]);
  const togglePause = async () => {
    try {
      const st = await api.post(paused ? "/engine/resume" : "/engine/pause");
      setPaused(!!st.paused);
      invoke("set_engine_paused", { paused: !!st.paused }).catch(() => {});
    } catch { /* engine unreachable: the reconnect banner explains */ }
  };

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
        if (ev.type === "posted") sendNotification(ev.inbox
          ? { title: "Sent to your TikTok inbox", body: "Open the TikTok app, add a sound if you like, paste the caption and post." }
          : { title: "Posted", body: "Submit it to the campaign from My Clips › Post & Submit." });
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
          <div className="row between" style={{ gap: 6, flexWrap: "nowrap" }}>
            <span className="engine-status"><span className="dot" style={conn !== "connected" ? { background: "#f5a524" } : paused ? { background: "var(--muted)" } : undefined} />
              {conn !== "connected" ? "Reconnecting" : paused ? "Paused" : "Running"}{encoder ? ` · ${encoder}` : ""}</span>
            <button className="engine-toggle" onClick={togglePause} disabled={conn !== "connected"}
              title={paused ? "Resume: start processing again" : "Pause: stop processing now (ffmpeg is stopped too)"}
              aria-label={paused ? "Resume engine" : "Pause engine"}>{paused ? "▶ Resume" : "⏸ Pause"}</button>
          </div>
        </div>
      </nav>
      <main className={page === "editor" ? "content studio-host" : "content"}>
        {update && (
          <div className="banner" style={{ background: "#13283a", borderColor: "#24507a", color: "#bfe0ff" }}>
            <span>ClipFarm {update.version} is available. It installs over this version; your keys and settings stay.</span>
            <button className="btn-primary" onClick={installUpdate} disabled={!!updating}>{updating || "Update now"}</button>
          </div>
        )}
        {banner && page === "dashboard" && (
          <div className="banner" role="alert">
            <span>{banner}</span>
            <button className="btn-ghost" onClick={() => go("health")}>Fix my setup</button>
          </div>
        )}
        {page === "dashboard" && <Dashboard go={go} />}
        {page === "new" && <NewProject done={() => go("dashboard")} go={go} />}
        {page === "editor" && <Editor go={go} initialClip={tab?.startsWith("clip:") ? Number(tab.slice(5)) : undefined} />}
        {page === "clips" && <MyClips initialTab={tab} go={go} />}
        {page === "earnings" && <Earnings />}
        {page === "settings" && <Settings initialTab={tab} go={go} />}
        {toast && <div className="toast" role="status">{toast}</div>}
      </main>
    </div>
  );
}
