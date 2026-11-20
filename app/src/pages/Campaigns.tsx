// Campaigns (Settings › Campaigns): import from the in-app campaign browser (or paste), edit fields,
// confirm "needs confirming" flags, mark campaigns joined, confirm streamer matches, auto-pilot.
import { useEffect, useMemo, useState } from "react";
import { api, aud, band, PLATFORM_LABEL } from "../api";
import { onCampaignImport, openCampaignBrowser } from "../lib/campaignBrowser";

const FIELD_LABEL: Record<string, string> = {
  campaign_type: "Type", payout_per_1k: "Rate per 1K views (USD)", min_views: "Min views per clip", min_payout: "Min payout per clip (USD)",
  max_payout: "Max payout per clip (USD)", pool_size: "Prize pool (USD)", view_threshold: "Total views to qualify",
  budget_total: "Budget (USD)", budget_remaining: "Budget left (USD)", ends_at: "Ends (YYYY-MM-DD HH:MM:SS)",
  required_tags: "Required hashtags", required_onscreen: "Must stay on screen", required_credit: "Credit text ({url}, {name})",
  disclosure: "Disclosure text", allowed_platforms: "Allowed platforms", pays_au: "Pays Australian clippers (1 = yes, 0 = no)",
  fee: "Platform fee (0.10 = 10%)", approval_rate: "Approval rate (0.6 = 60%)",
};
const LIST_FIELDS = new Set(["required_tags", "required_onscreen", "allowed_platforms"]);
const NUM_FIELDS = new Set(["payout_per_1k", "min_views", "min_payout", "max_payout", "pool_size", "view_threshold",
  "budget_total", "budget_remaining", "pays_au", "fee", "approval_rate"]);
const STATUS_CLS: Record<string, string> = { active: "ok", ending: "warn", out_of_budget: "bad", ended: "bad", paused: "warn" };

const COLS: [string, string, (c: any) => any][] = [
  ["name", "Campaign", (c) => c.name],
  ["type", "Type", (c) => c.campaign_type],
  ["rate", "Rate / pool", (c) => c.campaign_type === "prize_pool" ? c.pool_size ?? 0 : c.payout_per_1k ?? 0],
  ["clip", "Per clip (est.)", (c) => c.best?.per_clip_likely ?? -1],
  ["d7", "Next 7 days", (c) => c.best?.d7_likely ?? -1],
  ["d30", "Next 30 days", (c) => c.best?.d30_likely ?? -1],
  ["hour", "A$ / PC-hour", (c) => c.best?.aud_per_pc_hour ?? -1],
  ["budget", "Budget left", (c) => c.budget_remaining ?? -1],
  ["status", "Status", (c) => c.status],
];

export default function Campaigns() {
  const [rows, setRows] = useState<any[]>([]);
  const [matches, setMatches] = useState<any[]>([]);
  const [sort, setSort] = useState<[string, number]>(["hour", -1]);
  const [edit, setEdit] = useState<any>(null);
  const [paste, setPaste] = useState(""); const [pasteUrl, setPasteUrl] = useState("");
  const [msg, setMsg] = useState(""); const [busy, setBusy] = useState(false);

  const load = () => Promise.all([api.get("/campaigns").then(setRows), api.get("/campaigns/matches").then(setMatches)]).catch((e) => setMsg(e.message));
  useEffect(() => { load(); return onCampaignImport((r) => { setMsg(r.summary); load(); }); }, []);
  const [showPaste, setShowPaste] = useState(false);

  const sorted = useMemo(() => {
    const col = COLS.find(([k]) => k === sort[0])!;
    return [...rows].sort((a, b) => (col[2](a) > col[2](b) ? sort[1] : -sort[1]));
  }, [rows, sort]);

  const doImport = async () => {
    setBusy(true); setMsg("");
    try {
      const r = await api.post("/campaigns/import-paste", { text: paste, url: pasteUrl || null });
      const changes = r.updated.flatMap((u: any) => u.changes.map((c: string) => `${u.name}: ${c}`));
      setMsg([r.summary, ...changes].join(" "));
      setPaste(""); setPasteUrl(""); load();
    } catch (e: any) { setMsg(e.message); }
    setBusy(false);
  };
  const refresh = async () => {
    setBusy(true); setMsg("");
    try { await api.post("/campaigns/match"); await api.post("/projections/refresh"); await load(); setMsg("Matches and projections updated."); }
    catch (e: any) { setMsg(e.message); }
    setBusy(false);
  };
  const setJoined = (id: number, joined: boolean) => api.post(`/campaigns/${id}/joined`, { joined }).then(load).catch((e) => setMsg(e.message));
  const decide = (id: number, accept: boolean) => api.post(`/campaigns/matches/${id}`, { accept }).then(load).catch((e) => setMsg(e.message));

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Campaigns</h1>
          <p className="muted">Open Content Rewards inside ClipFarm, go to a campaign (or the Discover list) and press <b>Import this page</b>. ClipFarm only reads the page you're looking at, and never joins or submits for you.</p>
        </div>
        <button className="btn-primary" onClick={() => openCampaignBrowser().catch((e) => setMsg(e.message))}>Open campaign browser</button>
      </header>
      {msg && <p className="note-ok">{msg}</p>}

      {matches.length > 0 && (
        <section className="card stack tight">
          <h2 className="accent">Confirm these streamer matches</h2>
          <span className="muted">ClipFarm isn't sure these channels belong to the campaign. They won't be clipped until you say yes.</span>
          {matches.map((m) => (
            <div key={m.id} className="row between">
              <span><b>{m.program_name}</b> → {m.platform}: <b>{m.display_name}</b> ({m.candidate_login}) <span className="muted">· {m.reason} · {Math.round(m.confidence * 100)}% sure</span></span>
              <span className="row" style={{ gap: 6 }}>
                <button onClick={() => decide(m.id, true)}>That's them</button>
                <button className="btn-ghost" onClick={() => decide(m.id, false)}>Not them</button>
              </span>
            </div>))}
        </section>
      )}

      <div className="card table-box" style={{ padding: 0 }}>
        <table style={{ minWidth: 1100 }}>
          <thead><tr>{COLS.map(([k, l]) => (
            <th key={k} className="sortable" onClick={() => setSort([k, sort[0] === k ? -sort[1] : -1])}>{l}{sort[0] === k ? (sort[1] < 0 ? " ▼" : " ▲") : ""}</th>))}
            <th>Joined</th><th>Streamer</th><th>Rules</th><th>Needs confirming</th><th /></tr></thead>
          <tbody>{sorted.map((c) => (
            <tr key={c.id} className={c.status === "ended" || c.status === "out_of_budget" ? "blocked" : ""}>
              <td style={{ fontWeight: 600 }}>{c.url ? <a href={c.url} onClick={(e) => { e.preventDefault(); openCampaignBrowser(c.url).catch((er) => setMsg(er.message)); }}>{c.name}</a> : c.name}
                {(c.last_changes || []).length > 0 && <div className="muted small-text">Last import: {c.last_changes.join("; ")}</div>}</td>
              <td>{c.campaign_type === "prize_pool" ? "Prize pool" : "Pay per view"}</td>
              <td className="num">{c.campaign_type === "prize_pool" ? `$${(c.pool_size ?? 0).toLocaleString()} · ${c.view_threshold ? (c.view_threshold / 1000).toLocaleString() + "K views" : "?"}` : c.payout_per_1k != null ? `$${c.payout_per_1k.toFixed(2)}/1K` : "?"}
                {c.min_payout ? <div className="muted small-text">min ${c.min_payout} · max ${c.max_payout ?? "?"}</div> : null}</td>
              <td className="num">{c.best ? band(c.best.per_clip_low, c.best.per_clip_likely, c.best.per_clip_high) : "—"}
                {c.best && <div className="muted small-text">{PLATFORM_LABEL[c.best.platform] ?? c.best.platform}{c.best.warnings?.length ? " · ⚠ estimate uses defaults" : ""}</div>}</td>
              <td className="num">{c.best ? aud(c.best.d7_likely, 0) : "—"}</td>
              <td className="num">{c.best ? aud(c.best.d30_likely, 0) : "—"}</td>
              <td className="num">{c.best ? aud(c.best.aud_per_pc_hour) : "—"}</td>
              <td className="num">{c.budget_remaining != null ? `$${Math.round(c.budget_remaining).toLocaleString()}` : "—"}</td>
              <td><span className={`pill ${STATUS_CLS[c.status] ?? ""}`}>{(c.status || "active").replace(/_/g, " ")}</span></td>
              <td><button className={c.joined ? "tick on" : "tick"} aria-label={`I've joined ${c.name}`} title="Tick once you've pressed Join on the campaign page" onClick={() => setJoined(c.id, !c.joined)}>{c.joined ? "✓" : ""}</button></td>
              <td>{c.streamers.length ? c.streamers.map((s: any) => `${s.display_name} (${s.platform})${s.confirmed ? "" : "?"}`).join(", ") : <span className="muted">{c.streamer_label ? "not matched yet" : "no streamer"}</span>}</td>
              <td className="small-text">{[...(c.required_tags || []), ...(c.required_onscreen || []).map((x: string) => x.replace("streamer_", "keep ")),
                (c.allowed_platforms || []).map((p: string) => PLATFORM_LABEL[p] ?? p).join("/")].filter(Boolean).join(" · ")}</td>
              <td>{(c.needs_confirming || []).map((f: string) => <span key={f} className="flag" title="Not filled from the page or by you; a default is used in projections">{(FIELD_LABEL[f] || f).split(" (")[0]}</span>)}</td>
              <td><button onClick={() => setEdit(c)}>Edit</button></td>
            </tr>))}</tbody>
        </table>
      </div>

      <div className="cards2">
        <section className="card stack tight">
          <h2>Paste instead</h2>
          <span className="muted">If the campaign browser won't work, open the campaign in any browser, press Ctrl+A then Ctrl+C, and paste it here.</span>
          {!showPaste ? <div><button onClick={() => setShowPaste(true)}>Paste a campaign page</button></div> : <>
            <label className="field">Campaign link (optional)<input value={pasteUrl} placeholder="https://contentrewards.com/c/campaigns/…" onChange={(e) => setPasteUrl(e.target.value)} /></label>
            <label className="field">Page text<textarea rows={6} value={paste} onChange={(e) => setPaste(e.target.value)} placeholder="Paste the whole campaign page here" /></label>
            <div className="row" style={{ gap: 8 }}><button onClick={doImport} disabled={busy || !paste.trim()}>Import</button>
              <button className="btn-quiet" onClick={refresh} disabled={busy}>Re-match &amp; re-project all</button></div>
          </>}
        </section>
        <AutoPilot onChange={load} />
      </div>

      {edit && <EditCampaign c={edit} onClose={() => { setEdit(null); load(); }} />}
    </div>
  );
}

function EditCampaign({ c, onClose }: { c: any; onClose: () => void }) {
  const [v, setV] = useState<Record<string, string>>(() => {
    const o: Record<string, string> = {};
    for (const f of Object.keys(FIELD_LABEL)) {
      const x = c[f];
      o[f] = x == null ? "" : LIST_FIELDS.has(f) ? (x as string[]).join(", ") : String(x);
    }
    return o;
  });
  const [err, setErr] = useState("");
  const flags: string[] = c.needs_confirming || [];
  const save = async (fields: string[]) => {
    const body: Record<string, any> = {};
    for (const f of fields) {
      const raw = v[f].trim();
      body[f] = LIST_FIELDS.has(f) ? raw.split(",").map((x) => x.trim()).filter(Boolean)
        : NUM_FIELDS.has(f) ? (raw === "" ? null : Number(raw)) : raw || null;
    }
    try { await api.patch(`/campaigns/${c.id}`, body); onClose(); } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body stack tight" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={`Edit ${c.name}`} style={{ maxWidth: 640 }}>
        <h2>{c.name}</h2>
        <span className="muted">Fields marked ⚠ weren't on the page and use a default. Saving a field marks it as confirmed by you.</span>
        <div className="options" style={{ gap: 10, display: "grid", gridTemplateColumns: "1fr 1fr" }}>
          {Object.entries(FIELD_LABEL).map(([f, label]) => (
            <label key={f} className="field">{flags.includes(f) ? "⚠ " : ""}{label}
              {f === "campaign_type"
                ? <select value={v[f]} onChange={(e) => setV({ ...v, [f]: e.target.value })}><option value="pay_per_view">Pay per view</option><option value="prize_pool">Prize pool</option></select>
                : <input value={v[f]} onChange={(e) => setV({ ...v, [f]: e.target.value })} />}
            </label>))}
        </div>
        {c.rules && <details><summary>Rules from the page</summary><p className="muted small-text" style={{ whiteSpace: "pre-wrap" }}>{c.rules}</p></details>}
        {err && <span className="bad-text">{err}</span>}
        <div className="row" style={{ gap: 8 }}>
          <button className="btn-primary" onClick={() => save(Object.keys(FIELD_LABEL))}>Save all (confirm every field)</button>
          <button className="btn-ghost" onClick={() => save(Object.keys(FIELD_LABEL).filter((f) => !flags.includes(f) || v[f] !== String(c[f] ?? "")))}>Save changes only</button>
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function AutoPilot({ onChange }: { onChange: () => void }) {
  const [ap, setAp] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get("/autopilot").then(setAp).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  if (!ap) return <section className="card"><span className="muted">{msg || "Loading auto-pilot…"}</span></section>;
  const s = ap.settings;
  const put = async (patch: any) => { try { setAp(await api.put("/autopilot", { ...s, ...patch })); onChange(); } catch (e: any) { setMsg(e.message); } };
  const num = (k: string, step = 1) => <input type="number" step={step} style={{ width: 80 }} value={s[k]} onChange={(e) => setAp({ ...ap, settings: { ...s, [k]: +e.target.value } })} onBlur={() => put({})} />;
  return (
    <section className="card stack tight">
      <div className="row between">
        <h2 className="accent">Auto-pilot</h2>
        <button className={s.enabled ? "seg on" : "seg"} onClick={() => put({ enabled: !s.enabled })}>{s.enabled ? "On" : "Off"}</button>
      </div>
      <span className="muted">Picks which campaign streamers to clip, how many clips and where to post, by projected A$ per hour of PC time. It never clips streamers without a campaign.</span>
      <label className="line">Max clips per day {num("max_clips_per_day")}</label>
      <label className="line">Max posts per account per day {num("max_posts_per_account_per_day")}</label>
      <label className="line">Minimum projected A$ per clip {num("min_projected_aud_per_clip", 0.1)}</label>
      <label className="line">Clips per streamer per day {num("clips_per_streamer_per_day")}</label>
      <div className="row" style={{ gap: 8 }}><button className="btn-ghost" onClick={() => api.post("/autopilot/run").then((r) => { setAp(r); onChange(); }).catch((e) => setMsg(e.message))}>Decide now</button>{msg && <span className="muted">{msg}</span>}</div>
      <b>Current plan</b>
      {ap.plan.length === 0 ? <span className="muted">Nothing above your minimum right now.</span> :
        ap.plan.map((c: any) => <span key={c.streamer_id} className="small-text">{c.program}: {c.clips_per_day} clips/day → {c.platforms.map((p: string) => PLATFORM_LABEL[p] ?? p).join(", ")} · {aud(c.per_clip_likely)}/clip · {aud(c.aud_per_pc_hour)}/PC-hour</span>)}
      <b>Why it changed</b>
      <div className="logs" style={{ maxHeight: 200, overflow: "auto" }}>
        {ap.log.length === 0 ? <span className="muted">No decisions yet.</span> : ap.log.map((l: any) => <div key={l.id} className="small-text"><span className="muted">{l.ts}</span> {l.reason}</div>)}
      </div>
    </section>
  );
}
