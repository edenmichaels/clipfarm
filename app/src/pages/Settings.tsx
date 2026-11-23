// Settings. v0.3: also home to Campaigns (edit fields, confirm flags), Rewards programs, Setup health
// and Logs, which used to be separate menu items.
import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { api, PLATFORM_LABEL } from "../api";
import { applyAccent } from "../App";
import Connections from "../components/Connections";
import Disclaimer from "../components/Disclaimer";
import type { Page } from "../App";
import Campaigns from "./Campaigns";
import Programs from "./Programs";
import Health from "./Health";
import Logs from "./Logs";
import { CaptionSettings, HighlightSettings, MusicSettings } from "../components/QualitySettings";
import { StyleReferences, StylesPanel } from "../components/Styles";
import { BrandSettings, LearningSettings, LibrarySettings, ViralModelSettings } from "../components/StudioSettings";

export const TABS = ["General", "Accounts", "Connections", "Campaigns", "Captions", "Styles", "Highlights", "Music", "Brand", "Posting",
  "Rewards programs", "Backup", "Setup health", "Logs", "Rules"];
// Tabs that save themselves (no "Save settings" button).
const SELF_SAVING = ["Connections", "Accounts", "Backup", "Campaigns", "Rewards programs", "Setup health", "Logs",
  "Captions", "Styles", "Highlights", "Music", "Brand"];
const ACCENTS = ["#ffd400", "#3ccf7a", "#4da3ff", "#ff5a7a"];

export default function Settings({ initialTab, go }: { initialTab?: string; go: (p: Page, tab?: string) => void }) {
  const [tab, setTab] = useState(initialTab && TABS.includes(initialTab) ? initialTab : "General");
  useEffect(() => { if (initialTab && TABS.includes(initialTab)) setTab(initialTab); }, [initialTab]);
  const [s, setS] = useState<any>(null);
  const [sys, setSys] = useState<any>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.get("/settings").then(setS).catch((e) => setMsg(e.message));
    api.get("/system").then(setSys).catch(() => {});
  }, []);
  if (!s) return <p className="muted">{msg || "Loading…"}</p>;

  const set = (k: string, v: any) => { setMsg(""); setS({ ...s, [k]: v }); };
  const setIn = (k: string, sub: string, v: any) => set(k, { ...s[k], [sub]: v });
  const save = () => api.put("/settings", s).then(() => setMsg("Saved")).catch((e) => setMsg(e.message));
  const pickFolder = async () => { const d = await open({ directory: true }); if (typeof d === "string") set("media_dir", d); };
  // Plain function (not a component) so inputs keep focus while typing.
  const num = (k: string, sub?: string, step = 1, w = 70) => (
    <input type="number" step={step} style={{ width: w }} value={sub ? s[k][sub] : s[k]}
      onChange={(e) => (sub ? setIn(k, sub, +e.target.value) : set(k, +e.target.value))} />
  );

  return (
    <div className="stack">
      <header className="row between">
        <h1>Settings</h1>
        <div className="row">
          {msg && <span className={msg === "Saved" ? "ok-text" : "bad-text"}>{msg}</span>}
          {!SELF_SAVING.includes(tab) && <button className="btn-primary" onClick={save}>Save settings</button>}
        </div>
      </header>
      <div className="tabs" role="tablist">
        {TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "tab on" : "tab"} onClick={() => setTab(t)}>{t}</button>)}
      </div>

      {tab === "General" && (
        <div className="cards2">
          <CloseBehaviour />
          <section className="card stack">
            <h2 className="accent">Storage</h2>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input aria-label="Media folder" className="grow" readOnly value={s.media_dir || ""} />
              <button onClick={pickFolder}>Change…</button>
            </div>
            <label className="line">Storage limit <span>{num("storage_cap_gb", undefined, 1, 70)} GB</span></label>
            <button className="check" style={{ background: "none", border: "none", padding: 0 }}
              onClick={() => set("delete_sources_after_processing", !s.delete_sources_after_processing)}>
              <span className={s.delete_sources_after_processing ? "tick on" : "tick"} aria-hidden="true">{s.delete_sources_after_processing ? "✓" : ""}</span>
              Delete source videos after processing</button>
          </section>

          <section className="card stack">
            <h2 className="accent">Rendering</h2>
            <label className="line">Encoder
              <select value={s.render.encoder} onChange={(e) => setIn("render", "encoder", e.target.value)}>
                <option value="auto">Auto (best available)</option>
                {sys?.encoders?.filter((e: any) => e.available).map((e: any) => <option key={e.codec} value={e.codec}>{e.label}</option>)}
              </select></label>
            <label className="line">Quality
              <select value={s.render.quality} onChange={(e) => setIn("render", "quality", e.target.value)}>
                <option value="high">High</option><option value="medium">Medium</option><option value="fast">Fast / smaller</option></select></label>
          </section>

          <section className="card stack">
            <h2 className="accent">Default clip settings</h2>
            <label className="line">Clip length (seconds) <span>{num("clip", "min_sec", 1, 60)} – {num("clip", "max_sec", 1, 60)}</span></label>
            <label className="line">Highlights per stream hour {num("clip", "top_n_per_hour", 1, 60)}</label>
            <label className="line">Lead-in / tail padding (s) <span>{num("clip", "pad_before", 0.5, 60)} / {num("clip", "pad_after", 0.5, 60)}</span></label>
            <label className="field">How picky (higher = fewer, better clips): {Math.round((s.clip.highlight_threshold ?? 0.42) * 100)}
              <input type="range" min={25} max={70} value={Math.round((s.clip.highlight_threshold ?? 0.42) * 100)} onChange={(e) => setIn("clip", "highlight_threshold", +e.target.value / 100)} /></label>
            <span className="muted small-text">Caption style, music and what counts as a highlight are in the Captions, Music and Highlights tabs.</span>
            <button className="check" style={{ background: "none", border: "none", padding: 0 }} onClick={() => setIn("transcription", "enabled", !s.transcription.enabled)}>
              <span className={s.transcription.enabled ? "tick on" : "tick"} aria-hidden="true">{s.transcription.enabled ? "✓" : ""}</span>Transcribe speech for captions</button>
            <label className="line">Speech model
              <select value={s.transcription.model} onChange={(e) => setIn("transcription", "model", e.target.value)}>
                <option value="base">Base (fastest)</option><option value="small">Small (balanced)</option><option value="medium">Medium (best, needs a GPU)</option></select></label>
          </section>

          <section className="card stack">
            <h2 className="accent">AI model (hooks and titles)</h2>
            <label className="line">Provider
              <select value={s.llm.provider} onChange={(e) => setIn("llm", "provider", e.target.value)}>
                <option value="ollama">Local (Ollama, free)</option><option value="cloud">Cloud API</option></select></label>
            {s.llm.provider === "ollama"
              ? <label className="line">Model <input style={{ width: 180 }} value={s.llm.model} onChange={(e) => setIn("llm", "model", e.target.value)} /></label>
              : <>
                  <label className="line">API base URL <input style={{ width: 240 }} value={s.llm.cloud_base_url} onChange={(e) => setIn("llm", "cloud_base_url", e.target.value)} /></label>
                  <label className="line">Model <input style={{ width: 180 }} value={s.llm.cloud_model} onChange={(e) => setIn("llm", "cloud_model", e.target.value)} /></label>
                  <span className="muted">Add the API key under Connections.</span>
                </>}
            <span className="muted">If no model is running, ClipFarm uses simple template hooks.</span>
          </section>

          <section className="card stack">
            <h2 className="accent">Money</h2>
            <label className="line">USD → AUD exchange rate {num("usd_to_aud", undefined, 0.01, 80)}</label>
            <span className="muted">Used for every A$ figure. Check the current rate now and then.</span>
          </section>

          <section className="card stack">
            <h2 className="accent">Appearance</h2>
            <div className="line">Accent colour
              <div className="row" style={{ gap: 8 }}>{ACCENTS.map((c) => (
                <button key={c} aria-label={`Accent ${c}`} onClick={() => { set("accent", c); applyAccent(c); }}
                  style={{ width: 30, height: 30, padding: 0, borderRadius: "50%", background: c, border: s.accent === c ? "3px solid #fff" : "1px solid #2b303b" }} />))}</div>
            </div>
          </section>
        </div>
      )}

      {tab === "Connections" && <Connections />}

      {tab === "Posting" && (
        <section className="card stack" style={{ maxWidth: 560 }}>
          <h2 className="accent">Posting schedule</h2>
          <label className="line">Posts per day per account {num("posting", "posts_per_day_per_account", 1, 60)}</label>
          <label className="line">Posting hours <span>{num("posting", "window_start_hour", 1, 55)} to {num("posting", "window_end_hour", 1, 55)}</span></label>
          <label className="line">Minimum minutes between posts {num("posting", "min_gap_minutes", 1, 60)}</label>
          <span className="muted">YouTube Shorts upload automatically at the scheduled time once your channel is connected (about 6 a day on Google's free quota). TikTok, Instagram and X are posted by you: ClipFarm suggests the time, copies the caption and adds the "Submit to campaign" checklist item when you paste the link.</span>
        </section>
      )}

      {tab === "Accounts" && <Accounts />}
      {tab === "Backup" && <Backup />}

      {tab === "Rules" && <Disclaimer />}
      {tab === "Campaigns" && <div className="embedded"><Campaigns /></div>}
      {tab === "Rewards programs" && <div className="embedded"><Programs /></div>}
      {tab === "Setup health" && <div className="embedded"><Health go={go} /></div>}
      {tab === "Logs" && <div className="embedded"><Logs /></div>}
      {tab === "Captions" && <CaptionSettings />}
      {tab === "Highlights" && <div className="stack" style={{ maxWidth: 760 }}><HighlightSettings /><ViralModelSettings /><LearningSettings /></div>}
      {tab === "Music" && <div className="stack" style={{ maxWidth: 760 }}><MusicSettings /><LibrarySettings /></div>}
      {tab === "Brand" && <BrandSettings />}
      {tab === "Styles" && <div className="stack" style={{ maxWidth: 900 }}><StylesPanel /><StyleReferences /></div>}
    </div>
  );
}


function Accounts() {
  const [rows, setRows] = useState<any[]>([]);
  const [username, setUsername] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => {
    api.get("/accounts").then(setRows).catch((e) => setMsg(e.message));
    api.get("/settings").then((s) => setUsername(s.content_rewards_username || "")).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  const patch = (a: any, body: any) => api.patch(`/accounts/${a.id}`, body).then(load).catch((e) => setMsg(e.message));
  return (
    <section className="card stack" style={{ maxWidth: 760 }}>
      <h2 className="accent">Posting accounts</h2>
      <span className="muted">ClipFarm never stores passwords. YouTube signs in with Google (Settings &gt; Connections &gt; Connect YouTube channel); the others are posted by hand for now.</span>
      <table>
        <thead><tr><th>Platform</th><th>Handle</th><th>Sign-in</th><th>Followers</th><th>Max posts/day</th></tr></thead>
        <tbody>{rows.map((a) => (
          <tr key={a.id}>
            <td>{PLATFORM_LABEL[a.platform] ?? a.platform}</td>
            <td>{a.handle}</td>
            <td><span className={`pill ${a.oauth_status === "connected" ? "ok" : a.oauth_status === "needs_reconnect" ? "bad" : "warn"}`}>
              {a.oauth_status === "connected" ? "Connected" : a.oauth_status === "needs_reconnect" ? "Reconnect needed" : a.platform === "youtube" ? "Not signed in" : "Manual posting"}</span></td>
            <td><input type="number" style={{ width: 90 }} defaultValue={a.followers ?? ""} onBlur={(e) => patch(a, { followers: e.target.value === "" ? null : +e.target.value })} /></td>
            <td><input type="number" style={{ width: 60 }} defaultValue={a.max_posts_per_day ?? ""} placeholder="3" onBlur={(e) => patch(a, { max_posts_per_day: e.target.value === "" ? null : +e.target.value })} /></td>
          </tr>))}</tbody>
      </table>
      <span className="muted small-text">Followers make the view projections fit your accounts. Update them now and then.</span>
      <label className="line">Content Rewards / Whop username
        <input style={{ width: 200 }} value={username} onChange={(e) => setUsername(e.target.value)}
          onBlur={() => api.put("/settings", { content_rewards_username: username }).then(() => setMsg("Saved")).catch((e) => setMsg(e.message))} /></label>
      {msg && <span className="muted">{msg}</span>}
    </section>
  );
}

function Backup() {
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState("");
  const [sys, setSys] = useState<any>(null);
  useEffect(() => { api.get("/system").then(setSys).catch(() => {}); }, []);
  const doExport = async () => {
    const path = await saveDialog({ defaultPath: "ClipFarm-backup.clipfarm", filters: [{ name: "ClipFarm backup", extensions: ["clipfarm"] }] });
    if (!path) return;
    try { await api.post("/backup/export", { password: pw, path }); setMsg(`Saved to ${path}. Keep the password safe: without it the file can't be opened.`); }
    catch (e: any) { setMsg(e.message); }
  };
  const doImport = async () => {
    const path = await open({ multiple: false, filters: [{ name: "ClipFarm backup", extensions: ["clipfarm"] }] });
    if (typeof path !== "string") return;
    try { const r = await api.post("/backup/import", { password: pw, path }); setMsg(`Imported ${r.secrets} key(s) and ${r.settings} setting(s).`); }
    catch (e: any) { setMsg(e.message); }
  };
  return (
    <div className="cards2">
      <section className="card stack">
        <h2 className="accent">Your keys are safe across updates</h2>
        <span>API keys and sign-ins are kept in Windows Credential Manager ("ClipFarm"){sys?.keychain ? "" : " (not available on this PC)"} with an encrypted backup at <code>{sys?.vault?.path ?? "%APPDATA%\\ClipFarm\\vault.dat"}</code>{sys?.vault ? ` (${sys.vault.count} item(s), ${sys.vault.protector})` : ""}.</span>
        <span className="muted">Updating or reinstalling ClipFarm never removes them. Only the uninstaller's "Delete the application data" box (unticked by default) does. New versions install over the top, so you don't need to uninstall to update.</span>
      </section>
      <section className="card stack">
        <h2 className="accent">Move to a new PC</h2>
        <span className="muted">Export all keys, sign-ins and settings into one password-protected file. Import it on the new PC.</span>
        <label className="field">Password (8+ characters)<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn-primary" onClick={doExport} disabled={pw.length < 8}>Export settings and keys…</button>
          <button className="btn-ghost" onClick={doImport} disabled={pw.length < 8}>Import…</button>
        </div>
        {msg && <span className="muted">{msg}</span>}
      </section>
    </div>
  );
}


/** What the window's close button does: keep running in the tray, or quit ClipFarm completely. */
function CloseBehaviour() {
  const [action, setAction] = useState("tray");
  const [msg, setMsg] = useState("");
  useEffect(() => { invoke<string>("get_close_action").then(setAction).catch(() => {}); }, []);
  const set = (a: string) => invoke("set_close_action", { action: a }).then(() => { setAction(a); setMsg("Saved"); }).catch((e) => setMsg(String(e)));
  return (
    <section className="card stack">
      <h2 className="accent">When I close the window</h2>
      <label className="row" style={{ gap: 8 }}><input type="radio" name="close" checked={action === "tray"} onChange={() => set("tray")} />
        Keep running in the system tray (projects carry on; right-click the tray icon to pause or quit)</label>
      <label className="row" style={{ gap: 8 }}><input type="radio" name="close" checked={action === "quit"} onChange={() => set("quit")} />
        Quit ClipFarm (stops the engine and all processing)</label>
      <div><button className="btn-ghost btn-danger" onClick={() => invoke("quit_app")}>Quit ClipFarm now</button></div>
      {msg && <span className="muted">{msg}</span>}
    </section>
  );
}
