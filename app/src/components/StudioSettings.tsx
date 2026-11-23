// Settings (v0.7): Brand templates per account, what ClipFarm has learned (virality weights, predicted vs actual
// views), your own SFX / B-roll folders, and the automatic effects auto-pilot adds.
import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api, PLATFORM_LABEL, waitForJob } from "../api";

const BLANK = { caption_template: null, logo_path: null, logo_pos: "tr", logo_opacity: 0.85, logo_size: 150, intro_text: "",
  outro_text: "", card_bg: "111111", card_fg: "FFFFFF", progress_bar: true, progress_color: "FFE500", credit_pos: null };
const hx = (c?: string) => `#${(c || "000000").replace(/^#/, "")}`;

async function pick(filters?: any[]): Promise<string | null> {
  try { const r = await open({ multiple: false, filters }); return typeof r === "string" ? r : null; } catch { return null; }
}

export function BrandSettings() {
  const [data, setData] = useState<any>({ templates: [], accounts: [] });
  const [caps, setCaps] = useState<any[]>([]);
  const [edit, setEdit] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get("/brand-templates").then(setData).catch((e) => setMsg(e.message));
  useEffect(() => { load(); api.get("/caption-templates").then((r) => setCaps(r.templates || [])).catch(() => {}); }, []);
  const save = async () => {
    try {
      const { id, name, accounts, ...rest } = edit;
      await api.post("/brand-templates", { id, name, data: rest, account_ids: accounts || [] });
      setEdit(null); setMsg("Saved"); load();
    } catch (e: any) { setMsg(e.message); }
  };
  const set = (k: string, v: any) => setEdit({ ...edit, [k]: v });
  return (
    <div className="stack" style={{ maxWidth: 820 }}>
      <section className="card stack tight">
        <h2>Brand templates</h2>
        <span className="muted small-text">Your look per account: caption style, logo or watermark, intro/outro card, progress bar and where the campaign credit sits. Auto-pilot applies the template of the account a clip is made for.</span>
        {data.templates.map((t: any) => (
          <div key={t.id} className="row between">
            <span><b>{t.name}</b> <span className="muted small-text">{t.accounts.length ? `· ${t.accounts.map((a: number) => data.accounts.find((x: any) => x.id === a)?.handle).join(", ")}` : "· not used by any account"}</span></span>
            <span className="row" style={{ gap: 6 }}>
              <button onClick={() => setEdit({ ...t })}>Edit</button>
              <button className="btn-quiet btn-danger" onClick={() => api.del(`/brand-templates/${t.id}`).then(load)}>Delete</button>
            </span>
          </div>
        ))}
        {!edit && <div><button className="btn-primary" onClick={() => setEdit({ name: "", ...BLANK, accounts: [] })}>New brand template</button></div>}
        {msg && <span className="muted">{msg}</span>}
      </section>
      {edit && (
        <section className="card stack tight">
          <label className="field">Name<input value={edit.name} onChange={(e) => set("name", e.target.value)} placeholder="My TikTok look" /></label>
          <div className="grid2">
            <label className="field">Caption style<select value={edit.caption_template || ""} onChange={(e) => set("caption_template", e.target.value || null)}>
              <option value="">(keep the clip's)</option>{caps.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}</select></label>
            <label className="field">Credit position<select value={edit.credit_pos || ""} onChange={(e) => set("credit_pos", e.target.value || null)}>
              <option value="">Automatic</option><option value="top">Top</option><option value="bottom">Bottom</option></select></label>
            <label className="field">Logo / watermark (.png)
              <span className="row" style={{ gap: 6 }}><input value={edit.logo_path || ""} onChange={(e) => set("logo_path", e.target.value || null)} placeholder="C:\\path\\logo.png" />
                <button onClick={async () => { const p = await pick([{ name: "Images", extensions: ["png", "jpg", "jpeg", "webp"] }]); if (p) set("logo_path", p); }}>Browse…</button></span></label>
            <label className="field">Logo corner<select value={edit.logo_pos} onChange={(e) => set("logo_pos", e.target.value)}>
              {[["tl", "Top left"], ["tr", "Top right"], ["bl", "Bottom left"], ["br", "Bottom right"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="field">Logo size ({edit.logo_size}px)<input type="range" min={60} max={400} value={edit.logo_size} onChange={(e) => set("logo_size", +e.target.value)} /></label>
            <label className="field">Logo opacity ({Math.round(edit.logo_opacity * 100)}%)<input type="range" min={0.1} max={1} step={0.05} value={edit.logo_opacity} onChange={(e) => set("logo_opacity", +e.target.value)} /></label>
            <label className="field">Intro card text (first 0.8 s)<input value={edit.intro_text} onChange={(e) => set("intro_text", e.target.value)} placeholder="(none)" /></label>
            <label className="field">Outro card text (last 1.6 s)<input value={edit.outro_text} onChange={(e) => set("outro_text", e.target.value)} placeholder="Follow for more" /></label>
            <label className="field">Card background<input type="color" value={hx(edit.card_bg)} onChange={(e) => set("card_bg", e.target.value)} /></label>
            <label className="field">Card text<input type="color" value={hx(edit.card_fg)} onChange={(e) => set("card_fg", e.target.value)} /></label>
            <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={edit.progress_bar} onChange={(e) => set("progress_bar", e.target.checked)} /> Progress bar</label>
            <label className="field">Progress bar colour<input type="color" value={hx(edit.progress_color)} onChange={(e) => set("progress_color", e.target.value)} /></label>
          </div>
          <b>Use for these accounts</b>
          <div className="row wrap" style={{ gap: 6 }}>{data.accounts.map((a: any) => (
            <button key={a.id} className={(edit.accounts || []).includes(a.id) ? "pill-btn on" : "pill-btn"}
              onClick={() => set("accounts", (edit.accounts || []).includes(a.id) ? edit.accounts.filter((x: number) => x !== a.id) : [...(edit.accounts || []), a.id])}>
              {PLATFORM_LABEL[a.platform] || a.platform} {a.handle}</button>))}
            {!data.accounts.length && <span className="muted small-text">Add accounts in Settings › Accounts first.</span>}</div>
          <div className="row" style={{ gap: 8 }}><button className="btn-primary" onClick={save}>Save</button><button onClick={() => setEdit(null)}>Cancel</button></div>
        </section>
      )}
    </div>
  );
}

const PART_LABEL: Record<string, string> = { hook: "Hook", pacing: "Pacing", retention: "Retention", relevance: "Trends",
  quality: "Edit quality", moment: "Moment" };

export function LearningSettings() {
  const [d, setD] = useState<any>(null);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState("");
  const load = () => api.get("/learning").then((r) => { setD(r); setDraft(r.virality_global); }).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  if (!d) return <p className="muted">{msg || "Loading…"}</p>;
  const pva = d.predicted_vs_actual;
  const total = Object.values(draft).reduce((a, b) => a + b, 0) || 1;
  return (
    <>
      <section className="card stack tight">
        <h2>Virality score weights</h2>
        <span className="muted small-text">How much each part counts towards the 0–100 score. ClipFarm tunes these per streamer/game once you've given about {d.min_ratings} clips feedback (👍/👎, or keeping, exporting and deleting clips in the Editor).</span>
        <div className="weights">{Object.keys(d.virality_default).map((k) => [
          <span key={`l${k}`}>{PART_LABEL[k] || k}</span>,
          <input key={`i${k}`} type="range" min={0} max={0.6} step={0.01} value={draft[k] ?? 0} onChange={(e) => setDraft({ ...draft, [k]: +e.target.value })} aria-label={k} />,
          <span key={`p${k}`} className="muted">{Math.round(((draft[k] ?? 0) / total) * 100)}%</span>])}
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button onClick={() => api.put("/virality/weights", { weights: draft }).then((r) => { setD(r); setMsg("Saved"); })}>Save weights</button>
          <button className="btn-quiet" onClick={() => api.post("/virality/weights/reset", {}).then((r) => { setD(r); setDraft(r.virality_global); })}>Reset to default</button>
          {msg && <span className="muted">{msg}</span>}
        </div>
        {Object.entries(d.virality_learned).map(([scope, v]: any) => (
          <div key={scope} className="row between">
            <span><b>{d.names[scope] || scope}</b> <span className="muted small-text">({v.ratings} clips)</span>: {Object.entries(v.weights).map(([k, x]: any) => `${PART_LABEL[k] || k} ${Math.round(x * 100)}%`).join(" · ")}</span>
            <button className="btn-quiet" onClick={() => api.post("/virality/weights/reset", { scope }).then(load)}>Reset to default</button>
          </div>))}
        <span className="muted small-text">Signals so far: {(d.signals || []).map((s: any) => `${s.n} ${s.kind}`).join(", ") || "none yet"} · {d.ratings} rated.</span>
      </section>
      <section className="card stack tight">
        <h2>Predicted vs actual views</h2>
        {!pva.n ? <span className="muted small-text">Shows up once posted clips have view counts.</span> : <>
          <span>Across {pva.n} posted clips, real views were <b>{pva.median_ratio}×</b> the prediction (median){pva.score_vs_views_correlation != null ? <> · score vs views correlation <b>{pva.score_vs_views_correlation}</b></> : null}.</span>
          <table className="small-text"><thead><tr><th>Clip</th><th>Score</th><th>Predicted</th><th>Actual</th></tr></thead>
            <tbody>{pva.clips.slice(0, 15).map((c: any) => (
              <tr key={c.id}><td>{c.title}</td><td>{c.virality}</td><td>{c.expected_views?.toLocaleString()}</td>
                <td className={c.views >= c.expected_views ? "ok-text" : "warn-text"}>{c.views.toLocaleString()}</td></tr>))}</tbody></table>
        </>}
      </section>
    </>
  );
}

export function LibrarySettings() {
  const [folders, setFolders] = useState<any>({});
  const [fx, setFx] = useState<any>({ auto: true, emoji: true, sfx: true, broll: true });
  const [ok, setOk] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.get("/settings").then((s) => { setFolders(s.music || {}); setFx({ auto: true, emoji: true, sfx: true, broll: true, ...(s.effects || {}) }); }).catch(() => {});
  }, []);
  const setDir = async (k: string, v: string | null) => {
    try { const r = await api.put("/library-folders", { [k]: v, confirm_rights: ok }); setFolders({ ...folders, ...r }); setMsg("Saved"); }
    catch (e: any) { setMsg(e.message); }
  };
  const setAuto = (k: string, v: boolean) => { const n = { ...fx, [k]: v }; setFx(n); api.put("/settings", { effects: n }).catch((e) => setMsg(e.message)); };
  return (
    <>
      <section className="card stack tight">
        <h2>Automatic effects</h2>
        <span className="muted small-text">What auto-pilot and "Auto zooms, effects and sounds" add: zooms and punch-ins on the payoff and reactions (on the beat when there's music), flashes and shake for action, reaction emojis, and sound effects from the bundled pack (original sounds, CC0).</span>
        {[["auto", "Add effects automatically"], ["emoji", "Reaction emojis"], ["sfx", "Sound effects"], ["broll", "B-roll from my folder"]].map(([k, l]) => (
          <label key={k} className="row" style={{ gap: 8 }}><input type="checkbox" checked={fx[k] !== false} disabled={k !== "auto" && fx.auto === false} onChange={(e) => setAuto(k, e.target.checked)} /> {l}</label>))}
      </section>
      <section className="card stack tight">
        <h2>Your SFX and B-roll folders</h2>
        <span className="warn-text small-text">Only add files you have the rights to use commercially on TikTok, Reels, Shorts and X. ClipFarm never downloads sounds or video for you.</span>
        <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I have the rights to use everything in these folders</label>
        {[["sfx_dir", "Sound effects folder"], ["broll_dir", "B-roll folder (name files after what they show, e.g. cops.mp4)"]].map(([k, l]) => (
          <div key={k} className="row between">
            <span>{l}: <span className="muted">{folders[k] || "not set"}</span></span>
            <span className="row" style={{ gap: 6 }}>
              <button disabled={!ok} onClick={async () => { try { const r = await open({ directory: true }); if (typeof r === "string") setDir(k, r); } catch { /* dialog unavailable */ } }}>Choose…</button>
              {folders[k] && <button className="btn-quiet" onClick={() => setDir(k, null)}>Remove</button>}
            </span>
          </div>))}
        {msg && <span className="muted">{msg}</span>}
      </section>
    </>
  );
}

/** The viral moment model: what it learned from real viral clips, how accurate it is, and when it last trained. */
export function ViralModelSettings() {
  const [d, setD] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get("/viral/status").then(setD).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  if (!d) return <p className="muted">{msg || "Loading…"}</p>;
  const act = (p: Promise<any>, ok: string) => p.then((r) => { setD(r); setMsg(ok); }).catch((e) => setMsg(e.message));
  return (
    <section className="card stack tight">
      <h2>Viral moment model</h2>
      <span className="muted small-text">Learns what goes viral from this week's most-viewed Twitch clips of each game
        (only streamers you're allowed to clip), your own posts' views, and your 👍/👎. Refreshes once a day. Never
        downloads anything from TikTok or Instagram.</span>
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={d.settings.viral_dataset}
        onChange={(e) => api.put("/settings", { highlights: { viral_dataset: e.target.checked } }).then(load)} />
        Collect viral examples automatically (daily)</label>
      <label className="field">Only keep moments scoring at least {d.settings.viral_threshold}/100
        <input type="range" min={10} max={80} step={5} value={d.settings.viral_threshold}
          onChange={(e) => api.put("/settings", { highlights: { viral_threshold: +e.target.value } }).then(load)} /></label>
      <table className="small-text"><thead><tr><th>Game</th><th>Viral clips</th><th>Not viral</th><th>Yours</th><th>Updated</th></tr></thead>
        <tbody>{d.dataset.map((r: any) => (
          <tr key={String(r.game_id)}><td>{r.game || "Any game"}</td><td>{r.positives}</td><td>{r.negatives}</td><td>{r.yours}</td><td>{(r.updated || "").slice(0, 10)}</td></tr>))}
          {!d.dataset.length && <tr><td colSpan={5} className="muted">No examples yet: they arrive after the first daily refresh (needs Twitch connected and a permitted streamer).</td></tr>}</tbody></table>
      {d.models.length === 0 ? <span className="muted small-text">Using the built-in starting model (trained on research about what goes viral). It switches to a learned model per game after {d.min_examples} examples.</span> :
        d.models.map((m: any) => (
          <div key={m.game_id} className="row between">
            <span><b>{m.game}</b> <span className="muted small-text">last trained {m.trained_at} · {m.positives} viral / {m.negatives} not</span>
              {m.metrics?.auc != null && <span className="small-text"> · accuracy (AUC) <b>{(m.metrics.auc * 100).toFixed(0)}%</b> vs {(m.metrics.default_auc * 100).toFixed(0)}% for the starting model · top-10 precision {(m.metrics.precision_at_10 * 100).toFixed(0)}%</span>}</span>
            <button className="btn-quiet" onClick={() => act(api.post("/viral/reset", { game_id: m.game_id }), "Reset to the starting model")}>Reset to default</button>
          </div>))}
      {Object.entries(d.trends || {}).filter(([, t]: any) => Object.keys(t).length).map(([g, t]: any) => (
        <span key={g} className="small-text">Trending this week in <b>{g}</b>: {Object.keys(t).join(", ").replace(/_/g, " ")}</span>))}
      <div className="row" style={{ gap: 8 }}>
        <button onClick={() => act(api.post("/viral/refresh"), "Refreshing in the background…")}>Refresh examples now</button>
        <button onClick={() => act(api.post("/viral/retrain"), "Retrained")}>Retrain now</button>
        <button className="btn-quiet" onClick={() => act(api.post("/viral/reset", {}), "Reset to the starting model")}>Reset all to default</button>
        {msg && <span className="muted">{msg}</span>}
      </div>
      <ViralReport />
    </section>
  );
}


/** Detection report on a real VOD: community clips found before/after, and 20 picks with reasons + tags. */
function ViralReport() {
  const [r, setR] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => api.get("/viral/report").then(setR).catch(() => {});
  useEffect(() => { load(); }, []);
  const make = async () => {
    setBusy(true); setMsg("Working… this downloads the VOD's audio and checks the best parts (can take 10–30 minutes).");
    try { const { job_id } = await api.post("/viral/report", {}); await waitForJob(job_id); await load(); setMsg(""); }
    catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="stack tight">
      <div className="row" style={{ gap: 8 }}>
        <button onClick={make} disabled={busy} title="Runs the detector on the newest VOD of a streamer you're allowed to clip and checks it against that VOD's most-viewed community clips">Make detection report on a real VOD</button>
        {r?.report && <span className="small-text">Last report {r.made}: found <b>{r.found_after}/{r.top}</b> of the VOD's top community clips (before: {r.found_before}/{r.top})</span>}
      </div>
      {msg && <span className="muted small-text">{msg}</span>}
      {r?.report && <details><summary>Show the report</summary><pre className="report-pre">{r.report}</pre></details>}
    </div>
  );
}
