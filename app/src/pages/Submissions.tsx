// Post & Submit (v0.8.6): one screen for posting. Each clip shows every platform's status (Auto-posted ✓ /
// In TikTok inbox / Post Kit needed / Posted / Submitted). "Post all ready clips" schedules everything (spread
// across the day, limits respected); YouTube (and Instagram/X when set up) post themselves, TikTok goes to your
// inbox, the rest get a Post Kit. Then the "Submit to <campaign> on Content Rewards" checklist, as before.
import { openCampaignBrowser } from "../lib/campaignBrowser";
import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { api, aud, PLATFORM_LABEL } from "../api";

const NAME: Record<string, string> = { youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram", x: "X" };

export default function Submissions() {
  const [ov, setOv] = useState<any>({ clips: [], done: [], accounts: [] });
  const [todo, setTodo] = useState<any[]>([]);
  const [done, setDone] = useState<any[]>([]);
  const [kit, setKit] = useState<any>(null);
  const [phone, setPhone] = useState<any>(null);
  const [audit, setAudit] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => Promise.all([
    api.get("/posting/overview").then(setOv),
    api.get("/submissions?status=todo").then(setTodo),
    api.get("/submissions?status=done").then(setDone),
  ]).catch((e) => setMsg(e.message));
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const copy = async (text: string, what: string) => { await navigator.clipboard.writeText(text); setMsg(`${what} copied.`); };
  const tick = async (s: any) => { try { await api.post(`/submissions/${s.id}`, { done: true }); load(); } catch (e: any) { setMsg(e.message); } };
  const postAll = async () => {
    setBusy("all");
    try { const r = await api.post("/posting/post-all"); setMsg(r.message + (r.skipped.length ? ` Skipped ${r.skipped.length}: ${r.skipped[0]}` : "")); load(); }
    catch (e: any) { setMsg(e.message); } finally { setBusy(""); }
  };
  const schedule = async (cid: number, aid: number) => {
    try { const r = await api.post(`/clips/${cid}/schedule`, { account_ids: [aid] }); setMsg(`Scheduled for ${r[0].scheduled_at.slice(11, 16)}.`); load(); }
    catch (e: any) { setMsg(e.message); }
  };
  const openKit = async (s: any, row: any, auto = true) => {
    try {
      let pid = s.post_id;
      if (!pid) pid = (await api.post(`/clips/${row.clip_id}/schedule`, { account_ids: [s.account_id] }))[0].post_id;
      const k = await api.get(`/posts/${pid}/kit`);
      setKit({ ...k, clip_id: row.clip_id, account_id: s.account_id, link: "", code: s.code });
      if (auto && s.code !== "inbox_posted") {           // the three Post Kit actions, in one click
        await navigator.clipboard.writeText(k.caption).catch(() => {});
        if (s.code !== "inbox") {
          if (k.upload_url) await openUrl(k.upload_url).catch(() => {});
          await api.post(`/posts/${pid}/reveal`).catch(() => {});
        }
      }
    } catch (e: any) { setMsg(e.message); }
  };
  const saveLink = async () => {
    try {
      const r = await api.post(`/clips/${kit.clip_id}/posted`, { account_id: kit.account_id, url: kit.link.trim() });
      setMsg(r.submission ? `Saved. Next: ${r.submission.label}.` : "Saved."); setKit(null); load();
    } catch (e: any) { setMsg(e.message); }
  };
  const toPhone = async (row: any, nomusic = false) => {
    try { setPhone({ ...(await api.post(`/clips/${row.clip_id}/phone`, { nomusic })), title: row.title }); } catch (e: any) { setMsg(e.message); }
  };
  const toFolder = async (row: any) => {
    try { const r = await api.post(`/clips/${row.clip_id}/sync-folder`); setMsg(`Copied to ${r.path}.`); } catch (e: any) { setMsg(e.message); }
  };
  const check = async () => {
    setBusy("check");
    try { const [a, c] = await Promise.all([api.get("/posting/audit"), api.post("/posting/check-accounts")]); setAudit({ ...a, results: c.results }); load(); }
    catch (e: any) { setMsg(e.message); } finally { setBusy(""); }
  };

  return (
    <div className="stack">
      <header className="row between">
        <div className="stack tight" style={{ gap: 4 }}>
          <h1>Post &amp; submit</h1>
          <span className="muted">YouTube posts itself. TikTok goes to your TikTok inbox. Anything else gets a Post Kit. Posts are spread across the day and never go to the same account twice.</span>
        </div>
        <span className="row" style={{ gap: 8 }}>
          <button className="btn-ghost" onClick={check} disabled={busy === "check"}>{busy === "check" ? "Checking…" : "Check accounts"}</button>
          <button className="btn-primary" onClick={postAll} disabled={busy === "all" || !ov.clips.length}>Post all ready clips</button>
        </span>
      </header>
      {msg && <span className="muted">{msg}</span>}

      <div className="acct-cards">
        {ov.accounts.map((a: any) => (
          <section key={a.id} className="card stack tight" style={{ gap: 4 }}>
            <div className="row between"><b>{NAME[a.platform] ?? a.platform}</b><span className={a.mode === "kit" ? "pill warn" : "pill ok"}>{a.mode_label}</span></div>
            <span className="muted small-text">{a.handle}{a.connected ? "" : " · not connected (Settings › Connections)"}</span>
            {a.quota?.label && <span className="small-text">{a.quota.label}</span>}
            {a.reminder && <span className="warn-text small-text">{a.reminder}</span>}
            {a.problems?.map((p: string) => <span key={p} className="warn-text small-text">{p}</span>)}
          </section>))}
      </div>

      <section className="card stack tight">
        <h2 className="accent">Waiting to post</h2>
        {ov.clips.length === 0 ? <span className="muted">Nothing waiting. Approve clips (or use "Send to Post &amp; Submit" in My Clips) and they appear here.</span> : ov.clips.map((row: any) => (
          <div key={row.clip_id} className="post-row">
            {row.thumb_path ? <img src={api.mediaUrl(row.thumb_path)} alt="" /> : <div className="thumbless" />}
            <div className="stack tight" style={{ gap: 6 }}>
              <div className="row between">
                <span><b>{row.title}</b> <span className="muted">{row.streamer ? `· ${row.streamer}` : ""}{row.program ? ` · ${row.program}` : ""}{row.projected_aud ? ` · est. ${aud(row.projected_aud)}` : ""}</span></span>
                <span className="row" style={{ gap: 6 }}>
                  <button className="btn-quiet" onClick={() => toPhone(row)}>Send to phone</button>
                  <button className="btn-quiet" onClick={() => toFolder(row)}>Phone folder</button>
                </span>
              </div>
              <div className="plat-chips">
                {row.statuses.map((s: any) => (
                  <span key={s.account_id} className={`plat-chip ${s.code}`} title={s.error || s.note || ""}>
                    <b>{NAME[s.platform]}</b> {s.label}
                    {s.code === "waiting" && <button className="btn-quiet" onClick={() => schedule(row.clip_id, s.account_id)}>Schedule</button>}
                    {(s.code === "kit" || s.code === "failed" || (s.code === "waiting" && s.mode === "kit")) &&
                      <button onClick={() => openKit(s, row)}>Post to {NAME[s.platform]}</button>}
                    {s.code === "inbox" && <button className="btn-quiet" onClick={() => openKit(s, row)}>Caption</button>}
                    {s.code === "inbox_posted" && <button onClick={() => openKit(s, row, false)}>Paste link</button>}
                    {(s.code === "auto_posted" || s.code === "posted") && s.url && <button className="btn-quiet" onClick={() => openUrl(s.url)}>Open</button>}
                  </span>))}
              </div>
              {row.statuses.filter((s: any) => s.note && s.code !== "waiting").map((s: any) => <span key={s.account_id} className="muted small-text">{NAME[s.platform]}: {s.note}</span>)}
            </div>
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

      {kit && (
        <div className="modal-back" onClick={() => setKit(null)}>
          <div className="card modal stack tight" role="dialog" aria-label="Post Kit" onClick={(e) => e.stopPropagation()}>
            <h2>{kit.code === "inbox" ? `Post from your TikTok inbox` : kit.code === "inbox_posted" ? "Paste the TikTok link" : `Post to ${kit.platform_name}`} <span className="muted">{kit.handle}</span></h2>
            {kit.code === "inbox" && <span className="muted">The video is in the TikTok app: Inbox › ClipFarm notification. Add a sound if you like, paste the caption (copied), then Post.</span>}
            {kit.code !== "inbox" && kit.code !== "inbox_posted" && <span className="muted">Caption copied, upload page opened, and the video is selected in Explorer: drag it in.</span>}
            {kit.code !== "inbox_posted" && <ol className="kit-steps">{kit.checklist.map((c: string) => <li key={c}>{c}</li>)}</ol>}
            <textarea readOnly value={kit.caption} rows={5} />
            <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
              <button className="btn-ghost" onClick={() => copy(kit.caption, "Caption")}>Copy caption</button>
              {kit.upload_url && <button className="btn-ghost" onClick={() => openUrl(kit.upload_url)}>Open {kit.platform_name}</button>}
              <button className="btn-ghost" onClick={() => api.post(`/posts/${kit.post_id}/reveal`).catch((e) => setMsg(e.message))}>Show video in Explorer</button>
              {kit.nomusic_file && <button className="btn-ghost" onClick={() => api.post(`/posts/${kit.post_id}/reveal`, { nomusic: true })}>No-music version</button>}
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
              <input className="grow" placeholder="After posting, paste the post link here" value={kit.link} onChange={(e) => setKit({ ...kit, link: e.target.value })} />
              <button className="btn-primary" onClick={saveLink} disabled={!kit.link.trim().startsWith("http")}>Save link</button>
            </div>
          </div>
        </div>)}

      {phone && (
        <div className="modal-back" onClick={() => setPhone(null)}>
          <div className="card modal stack tight" role="dialog" aria-label="Send to phone" onClick={(e) => e.stopPropagation()}>
            <h2>Scan with your phone</h2>
            <span className="muted">Your phone must be on the same Wi-Fi. The page has Download and Copy caption. The link stops working at {phone.expires_at}.</span>
            <div className="qr-box" dangerouslySetInnerHTML={{ __html: phone.qr_svg }} />
            <code className="small-text">{phone.url}</code>
            <span className="muted small-text">If Windows asks whether ClipFarm may use the network, choose Allow (private networks).</span>
          </div>
        </div>)}

      {audit && (
        <div className="modal-back" onClick={() => setAudit(null)}>
          <div className="card modal stack tight" style={{ maxWidth: 900 }} role="dialog" aria-label="Posting check" onClick={(e) => e.stopPropagation()}>
            <h2>Your accounts</h2>
            {audit.results.map((r: any) => (
              <div key={r.account_id} className="stack tight" style={{ gap: 2 }}>
                <b>{NAME[r.platform]} {r.handle} <span className={r.ok ? "pill ok" : "pill warn"}>{r.mode}</span></b>
                {r.checks.map((c: any) => <span key={c.check} className="small-text"><span className={c.ok === false ? "bad-text" : c.ok ? "ok-text" : "muted"}>{c.ok === false ? "✗" : c.ok ? "✓" : "–"}</span> {c.check}: <span className="muted">{c.detail}</span></span>)}
              </div>))}
            <h2>What changed in 0.8.6</h2>
            <table className="audit"><thead><tr><th>Platform</th><th>Worked</th><th>Failed, and why</th><th>Fixed</th></tr></thead>
              <tbody>{audit.table.map((t: any) => <tr key={t.platform}><td><b>{t.platform}</b></td><td>{t.worked}</td><td>{t.failed}</td><td>{t.fixed}</td></tr>)}</tbody></table>
          </div>
        </div>)}
    </div>
  );
}
