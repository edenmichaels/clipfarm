// Post & Submit: clips waiting to be posted by hand (TikTok/Instagram/X), and the
// "Submit to <campaign> on Content Rewards" checklist with the post link ready to copy.
import { openCampaignBrowser } from "../lib/campaignBrowser";
import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { api, aud, PLATFORM_LABEL } from "../api";

export default function Submissions() {
  const [toPost, setToPost] = useState<any[]>([]);
  const [todo, setTodo] = useState<any[]>([]);
  const [done, setDone] = useState<any[]>([]);
  const [links, setLinks] = useState<Record<number, string>>({});
  const [msg, setMsg] = useState("");

  const load = () => Promise.all([
    api.get("/posts?status=scheduled").then((p: any[]) => setToPost(p)),
    api.get("/submissions?status=todo").then(setTodo),
    api.get("/submissions?status=done").then(setDone),
  ]).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);

  const copy = async (text: string, what: string) => { await navigator.clipboard.writeText(text); setMsg(`${what} copied.`); };
  const posted = async (p: any) => {
    try {
      const r = await api.post(`/clips/${p.clip_id}/posted`, { account_id: p.account_id, url: links[p.id] || "" });
      setMsg(r.submission ? `Recorded. Next: ${r.submission.label}.` : "Recorded.");
      load();
    } catch (e: any) { setMsg(e.message); }
  };
  const tick = async (s: any) => {
    try { await api.post(`/submissions/${s.id}`, { done: true }); load(); } catch (e: any) { setMsg(e.message); }
  };

  return (
    <div className="stack">
      <header className="stack tight" style={{ gap: 4 }}>
        <h1>Post &amp; submit</h1>
        <span className="muted">Content Rewards has no submission API, so you submit each post yourself. Every post can only be submitted once.</span>
      </header>
      {msg && <span className="muted">{msg}</span>}

      <section className="card stack tight">
        <h2 className="accent">To post</h2>
        {toPost.length === 0 ? <span className="muted">Nothing scheduled. Approve clips in Review and schedule them.</span> : toPost.map((p) => (
          <div key={p.id} className="stack tight" style={{ borderBottom: "1px solid #222731", paddingBottom: 10 }}>
            <div className="row between">
              <span><b>{p.title}</b> <span className="muted">→ {PLATFORM_LABEL[p.platform] ?? p.platform} {p.handle} · suggested {p.scheduled_at}{p.program ? ` · ${p.program}` : ""}{p.projected_aud ? ` · est. ${aud(p.projected_aud)}` : ""}</span></span>
              <span className="row" style={{ gap: 6 }}>
                <button className="btn-ghost" onClick={() => copy(p.caption_text || p.title, "Caption")}>Copy caption</button>
                <button className="btn-ghost" onClick={() => copy(p.file_path, "Video path")}>Copy video path</button>
              </span>
            </div>
            {p.manual ? (
              <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                <input className="grow" placeholder="After posting, paste the post link here" value={links[p.id] || ""} onChange={(e) => setLinks({ ...links, [p.id]: e.target.value })} />
                <button onClick={() => posted(p)} disabled={!links[p.id]}>I've posted it</button>
              </div>
            ) : <span className="muted small-text">Uploads automatically at the scheduled time.</span>}
            {p.trending && <span className="small-text">♪ Trending sound idea: <b>{p.trending.name}</b>{p.trending.artist ? ` – ${p.trending.artist}` : ""} <span className="muted">(add it in {PLATFORM_LABEL[p.platform] ?? p.platform}'s editor{p.nomusic_path ? "; use the \"for trending sound\" file" : ""})</span>
              {p.nomusic_path && <button className="btn-quiet" onClick={() => copy(p.nomusic_path, "Video path")}>Copy no-music file path</button>}</span>}
            {p.error && <span className="bad-text small-text">{p.error}</span>}
          </div>))}
      </section>

      <section className="card stack tight">
        <h2 className="accent">Submit to campaigns</h2>
        {todo.length === 0 ? <span className="muted">Nothing to submit. Items appear here as soon as a clip is posted.</span> : todo.map((s) => (
          <div key={s.id} className="row between">
            <span className="row" style={{ gap: 10 }}>
              <button className="tick" aria-label={`Mark submitted: ${s.label}`} onClick={() => tick(s)} />
              <span><b>{s.label}</b> <span className="muted">· {s.title} · {PLATFORM_LABEL[s.platform] ?? s.platform} {s.handle}</span></span>
            </span>
            <span className="row" style={{ gap: 6 }}>
              <button className="btn-ghost" onClick={() => copy(s.post_url, "Post link")}>Copy post link</button>
              {s.program_url && <button onClick={() => openCampaignBrowser(s.program_url).catch(() => openUrl(s.program_url))}>Open campaign to submit</button>}
            </span>
          </div>))}
      </section>

      {done.length > 0 && (
        <section className="card stack tight">
          <h2 className="accent">Submitted</h2>
          {done.slice(0, 50).map((s) => <span key={s.id} className="small-text"><span className="ok-text">✓</span> {s.label} · <span className="muted">{s.post_url} · {s.done_at}</span></span>)}
        </section>
      )}
    </div>
  );
}
