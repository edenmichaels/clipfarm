// API keys / account connections as cards (mockup Settings > Connections).
import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { api } from "../api";

const GROUPS: { title: string; use: string; help: string; fields: [string, string][] }[] = [
  { title: "Twitch", use: "Finding streamers, clips and VODs (needed)", help: "https://dev.twitch.tv/console/apps",
    fields: [["twitch_client_id", "Client ID"], ["twitch_client_secret", "Client Secret"]] },
  { title: "YouTube", use: "Finding YouTube live streams", help: "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
    fields: [["youtube_api_key", "API key"]] },
  { title: "Kick", use: "Finding Kick live streams", help: "https://kick.com/settings/developer",
    fields: [["kick_client_id", "Client ID"], ["kick_client_secret", "Client Secret"]] },
  { title: "YouTube upload account", use: "Posting Shorts automatically", help: "https://console.cloud.google.com/apis/credentials",
    fields: [["google_oauth_client_id", "OAuth Client ID (Desktop app)"], ["google_oauth_client_secret", "OAuth Client Secret"]] },
  { title: "TikTok", use: "Sending clips to your TikTok inbox (you add a sound and post)", help: "https://developers.tiktok.com/apps/",
    fields: [["tiktok_client_key", "Client key"], ["tiktok_client_secret", "Client secret"]] },
  { title: "Instagram (Meta app)", use: "Posting Reels automatically (Business/Creator account + Facebook Page)", help: "https://developers.facebook.com/apps/",
    fields: [["meta_app_id", "App ID"], ["meta_app_secret", "App secret"]] },
  { title: "X (optional, paid API)", use: "Posting to X directly. Without keys X uses the Post Kit.", help: "https://developer.x.com/en/portal/dashboard",
    fields: [["x_api_key", "API key"], ["x_api_secret", "API key secret"], ["x_access_token", "Access token"], ["x_access_secret", "Access token secret"]] },
  { title: "Cloud AI (optional)", use: "Only if you don't want the free local model", help: "https://ollama.com/download",
    fields: [["llm_api_key", "API key"]] },
];

export default function Connections() {
  const [status, setStatus] = useState<Record<string, boolean>>({});
  const [vals, setVals] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");

  const load = () => api.get("/system").then((s) => setStatus(s.secrets)).catch(() => {});
  useEffect(() => { load(); }, []);

  const save = async () => {
    try {
      for (const [name, value] of Object.entries(vals)) if (value.trim()) await api.post("/secrets", { name, value });
      setVals({}); await load(); setMsg("Saved to your Windows keychain.");
    } catch (e: any) { setMsg(e.message); }
  };
  const connectYouTube = async () => {
    try { const { url } = await api.get("/oauth/youtube/start"); await openUrl(url); } catch (e: any) { setMsg(e.message); }
  };
  const connectTikTok = async () => {
    try { const { url } = await api.get("/oauth/tiktok/start"); await openUrl(url); setMsg("Approve ClipFarm in the browser, then come back."); } catch (e: any) { setMsg(e.message); }
  };
  const [ig, setIg] = useState<any>(null);
  const connectInstagram = async () => {
    try { const r = await api.get("/oauth/instagram/start"); setIg({ ...r, paste: "", result: null }); await openUrl(r.url); } catch (e: any) { setMsg(e.message); }
  };
  const finishInstagram = async () => {
    try { setIg({ ...ig, result: await api.post("/accounts/instagram/connect", { paste: ig.paste }) }); } catch (e: any) { setMsg(e.message); }
  };

  return (
    <div className="stack">
      <div className="cards2">
        {GROUPS.map((g) => {
          const connected = g.fields.every(([n]) => status[n]);
          return (
            <section key={g.title} className="card stack tight">
              <div className="row between"><h2>{g.title}</h2><span className={connected ? "pill ok" : "pill"}>{connected ? "Connected" : "Not set"}</span></div>
              <span className="muted">{g.use}</span>
              {g.fields.map(([name, label]) => (
                <label key={name} className="field">{label}
                  <input type="password" autoComplete="off" placeholder={status[name] ? "•••••• saved" : "not set"}
                    value={vals[name] || ""} onChange={(e) => setVals({ ...vals, [name]: e.target.value })} /></label>
              ))}
              <a href={g.help} style={{ fontSize: 13 }} onClick={(e) => { e.preventDefault(); openUrl(g.help); }}>Where do I get this?</a>
            </section>
          );
        })}
      </div>
      <div className="row">
        <button className="btn-primary" onClick={save}>Save keys</button>
        <button className="btn-ghost" onClick={connectYouTube} disabled={!status.google_oauth_client_id}>Connect YouTube channel…</button>
        <button className="btn-ghost" onClick={connectTikTok} disabled={!status.tiktok_client_key}>Connect TikTok…</button>
        <button className="btn-ghost" onClick={connectInstagram} disabled={!status.meta_app_id}>Connect Instagram…</button>
        {msg && <span className="muted">{msg}</span>}
      </div>
      {ig && (
        <section className="card stack tight">
          <h2>Connect Instagram</h2>
          <span className="muted">Approve every permission in the browser. You'll land on a page that says "Success". Copy that page's whole address and paste it here.</span>
          <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
            <input className="grow" type="password" autoComplete="off" placeholder="https://www.facebook.com/connect/login_success.html#access_token=…" value={ig.paste} onChange={(e) => setIg({ ...ig, paste: e.target.value })} />
            <button className="btn-primary" onClick={finishInstagram} disabled={!ig.paste.trim()}>Connect</button>
          </div>
          {ig.result && (ig.result.ok
            ? <span className="ok-text">Connected @{ig.result.username}. Reels will post automatically ({ig.result.limit?.label}).</span>
            : <div className="stack tight"><span className="warn-text">This account can't auto-post yet, so Instagram uses the Post Kit. {ig.result.problems.join(" ")}</span>
              <ol className="kit-steps">{ig.result.steps.map((s: any) => <li key={s.title}><b>{s.title}.</b> {s.text} {s.link && <a href={s.link} onClick={(e) => { e.preventDefault(); openUrl(s.link); }}>Open</a>}</li>)}</ol></div>)}
          {!ig.result && <details><summary className="muted">Not set up yet? The steps</summary>
            <ol className="kit-steps">{ig.steps.map((s: any) => <li key={s.title}><b>{s.title}.</b> {s.text}</li>)}</ol></details>}
        </section>)}
    </div>
  );
}
