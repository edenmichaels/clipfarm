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
  { title: "YouTube upload account", use: "Posting Shorts (next version)", help: "https://console.cloud.google.com/apis/credentials",
    fields: [["google_oauth_client_id", "OAuth Client ID (Desktop app)"], ["google_oauth_client_secret", "OAuth Client Secret"]] },
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
        {msg && <span className="muted">{msg}</span>}
      </div>
    </div>
  );
}
