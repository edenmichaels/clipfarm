// New Project in 3 steps: 1 Game (or Auto-pilot) -> 2 Streamers (grouped by campaign) -> 3 Review and start.
// Accounts, campaigns and their rules are filled in automatically; last project's choices are the defaults.
import { useEffect, useMemo, useState } from "react";
import { api, DURATIONS, PLATFORM_LABEL } from "../api";
import type { Page } from "../App";
import StreamerSections, { clippable } from "../components/StreamerSections";
import StreamerTable from "../components/StreamerTable";

const PLATFORMS: [string, string][] = [["tiktok", "TikTok"], ["youtube_shorts", "YouTube Shorts"], ["instagram_reels", "Instagram Reels"], ["x", "X"]];
const CAPTIONS: [string, string][] = [["bold_pop", "Bold pop"], ["clean", "Clean"], ["neon", "Neon"]];
const LAYOUTS: [string, string][] = [["auto", "Auto"], ["split", "Facecam + gameplay"], ["crop", "Action crop"], ["blur_fill", "Full frame on blur"]];
const STEPS = ["Game", "Streamers", "Review and start"];
const PRETICK = 3;

export default function NewProject({ done, go }: { done: () => void; go: (p: Page, tab?: string) => void }) {
  const [step, setStep] = useState(1);
  const [games, setGames] = useState<any[]>([]);
  const [query, setQuery] = useState("");
  const [game, setGame] = useState<{ id: number; name: string } | null>(null);
  const [streamers, setStreamers] = useState<any[]>([]);
  const [groups, setGroups] = useState<any>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [warn, setWarn] = useState("");
  const [allDetails, setAllDetails] = useState(false);
  const [opts, setOpts] = useState<any>(null);          // defaults = last project's choices
  const [fill, setFill] = useState<any>(null);
  const [details, setDetails] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get("/games/trending").then(setGames).catch((e) => setWarn(e.message));
    api.get("/projects/defaults").then(setOpts).catch(() => {});
  }, []);
  const search = () => { if (query.trim()) api.get(`/games/search?q=${encodeURIComponent(query)}`).then(setGames).catch((e) => setWarn(e.message)); };

  const loadGroups = async (id: number, pretick = false) => {
    const g = await api.get(`/games/${id}/streamers/grouped`);
    setGroups(g);
    if (pretick) {
      // Joined campaigns, best projected earnings first: match them now and tick the top few.
      const top = g.joined.slice(0, PRETICK);
      await Promise.all(top.filter((s: any) => !clippable(s)).map((s: any) => api.post(`/streamers/${s.id}/match`).catch(() => null)));
      setSelected(top.map((s: any) => s.id));
    }
    return g;
  };
  const refresh = async (id: number, pretick = false) => {
    setLoading(true); setWarn("");
    try {
      const r = await api.post(`/games/${id}/discover`);
      setStreamers(r.streamers);
      if (r.errors.length) setWarn(`Some sites didn't answer: ${r.errors.join("; ")}`);
      await loadGroups(id, pretick);
    } catch (e: any) { setWarn(e.message); }
    setLoading(false);
  };
  const pickGame = async (g: any) => {
    const { id } = await api.post("/games", {
      name: g.name, twitch_id: g.platform === "twitch" ? g.platform_game_id : null,
      kick_slug: g.platform === "kick" ? g.platform_game_id : null, box_art_url: g.box_art_url,
    });
    setGame({ id, name: g.name }); setSelected([]); setGroups(null); setStep(2); refresh(id, true);
  };
  const autopilot = async () => {
    setBusy(true); setErr("");
    try {
      const ap = await api.get("/autopilot");
      await api.put("/autopilot", { ...ap.settings, enabled: true });
      await api.post("/autopilot/run");
      done();
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };

  // Step 3: what gets filled in automatically, for the chosen streamers and platforms.
  useEffect(() => {
    if (step !== 3 || !opts) return;
    api.post("/projects/autofill", { streamer_ids: selected, platforms: opts.platforms }).then(setFill).catch((e) => setErr(e.message));
  }, [step, selected, opts?.platforms?.join(",")]);

  const create = async (confirmNoPayout = false) => {
    setErr(""); setBusy(true);
    try {
      const { duration_sec, ...output } = opts;
      await api.post("/projects", {
        name: `${game!.name} · ${new Date().toLocaleDateString()}`, game_id: game!.id,
        streamer_ids: selected, duration_sec, output_settings: output, confirm_no_payout: confirmNoPayout,
      });
      done();
    } catch (e: any) {
      if (e.status === 409 && e.detail?.needs_confirmation && !confirmNoPayout) {
        setBusy(false);
        if (window.confirm(`${e.detail.message}\n\nOK = clip them anyway (no payout). Cancel = go back.`)) return create(true);
        return;
      }
      setErr(e.message);
    }
    setBusy(false);
  };
  const toggleIn = (arr: string[], v: string) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const set = (k: string, v: any) => setOpts({ ...opts, [k]: v });
  const canGo = (n: number) => n === 1 || (n === 2 && !!game) || (n === 3 && selected.length > 0);
  const selectedNames = useMemo(() => {
    const all = [...(groups?.joined ?? []), ...(groups?.no_campaign ?? []), ...streamers];
    return selected.map((id) => all.find((s: any) => s.id === id)?.display_name).filter(Boolean);
  }, [selected, groups, streamers]);

  return (
    <div className="stack">
      <ol className="steps">{STEPS.map((s, i) => (
        <li key={s}><button className={`step ${i + 1 === step ? "on" : i + 1 < step ? "done" : ""}`}
          disabled={!canGo(i + 1)} onClick={() => setStep(i + 1)}>{i + 1}  {s}</button></li>))}</ol>

      {step === 1 && (
        <section className="stack">
          <h1>Pick a game</h1>
          <button className="autopilot-card" onClick={autopilot} disabled={busy}>
            <b>Auto-pilot: let ClipFarm choose</b>
            <span className="muted">Picks the best-paying campaign streamers, how many clips and where to post. Changes as campaigns end or budgets run out. Only clips streamers with a campaign you've joined.</span>
          </button>
          <div className="row" style={{ maxWidth: 620, flexWrap: "nowrap" }}>
            <label htmlFor="gamesearch" className="sr-only">Search games</label>
            <input id="gamesearch" className="grow" placeholder="Or search any game…" value={query}
              onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} />
            <button onClick={search}>Search</button>
          </div>
          {warn && <p className="warn-text">{warn}</p>}
          {err && <p className="bad-text">{err}</p>}
          <span className="muted">Trending now</span>
          <div className="games">{games.map((g) => (
            <button key={`${g.platform}-${g.platform_game_id ?? g.name}`} className="game" onClick={() => pickGame(g)}>
              <div className="art">{g.box_art_url && <img src={g.box_art_url.replace("{width}x{height}", "285x380")} alt="" />}</div>
              <span style={{ fontWeight: 600 }}>{g.name}</span>
            </button>))}
            {games.length === 0 && <p className="muted">No trending games yet. Connect Twitch in Settings › Connections, or search above.</p>}
          </div>
        </section>
      )}

      {step === 2 && game && (
        <section className="stack tight">
          <header className="page-head">
            <h1>{game.name}: pick streamers</h1>
            <div className="row" style={{ gap: 8 }}>
              <button onClick={() => refresh(game.id)} disabled={loading}>{loading ? "Looking…" : "Refresh"}</button>
              <button className="btn-quiet" onClick={() => setStep(1)}>Change game</button>
            </div>
          </header>
          <p className="muted">Click a streamer to see their campaign. Earnings are estimates in Australian dollars; hover for the range.</p>
          {warn && <p className="warn-text">{warn}</p>}
          {loading && !groups && <div className="row"><div className="spinner small" /><span className="muted">Looking for live streamers…</span></div>}
          {groups && !allDetails && <StreamerSections gameId={game.id} gameName={game.name} groups={groups} selected={selected} onSelect={setSelected}
            reload={async () => { await loadGroups(game.id); }} />}
          {allDetails && <StreamerTable gameId={game.id} streamers={streamers} selected={selected} onSelect={setSelected}
            reload={() => api.get(`/games/${game.id}/streamers`).then(setStreamers)} />}
          <button className="link-btn" onClick={() => setAllDetails(!allDetails)}>{allDetails ? "Back to simple view" : "Show details (all streamers, permissions, scores)"}</button>
          <div className="footer-bar">
            <span className="muted">{selected.length ? `${selected.length} selected: ${selectedNames.join(", ")}` : "Tick at least one streamer"}</span>
            <button className="btn-primary" disabled={!selected.length} onClick={() => setStep(3)}>Next: review</button>
          </div>
        </section>
      )}

      {step === 3 && opts && (
        <section className="stack">
          <h1>Review and start</h1>
          <div className="summary-line">{fill ? fill.summary : "Filling in your accounts and campaign rules…"}</div>

          <div className="card stack">
            <div className="field">Run for
              <div className="row" style={{ gap: 8 }}>{DURATIONS.map(([l, s]) => (
                <button key={s} className={opts.duration_sec === s ? "pill-btn on" : "pill-btn"} onClick={() => set("duration_sec", s)}>{l}</button>))}</div>
            </div>
            <label className="field">Clips per day
              <input type="number" min={1} max={100} style={{ width: 90 }} value={opts.clips_per_day} onChange={(e) => set("clips_per_day", Math.max(1, +e.target.value))} />
            </label>
            <div className="field">Post to
              <div className="row" style={{ gap: 8 }}>{PLATFORMS.map(([k, l]) => (
                <button key={k} className={opts.platforms.includes(k) ? "pill-btn on" : "pill-btn"} onClick={() => set("platforms", toggleIn(opts.platforms, k))}>{l}</button>))}</div>
              <span className="muted small-text">Each streamer only posts where their campaign allows.</span>
            </div>
          </div>

          <button className="link-btn" onClick={() => setDetails(!details)}>{details ? "Hide details" : "Show details (accounts, campaign rules, clip style)"}</button>
          {details && fill && (
            <div className="stack tight">
              <div className="card table-box" style={{ padding: 0 }}>
                <table>
                  <thead><tr><th>Streamer</th><th>Campaign</th><th>Posting accounts</th><th>Rules applied</th></tr></thead>
                  <tbody>{fill.streamers.map((s: any) => (
                    <tr key={s.streamer_id}>
                      <td><b>{s.name}</b></td>
                      <td>{s.campaign ? s.campaign.name : <span className="muted">No campaign (not paid)</span>}</td>
                      <td>{s.accounts.length ? s.accounts.map((a: any) => `${PLATFORM_LABEL[a.platform] ?? a.platform} ${a.handle}${a.connected ? "" : " (not connected)"}`).join(", ") : <span className="muted">none</span>}
                        {s.skipped_platforms.length > 0 && <div className="muted small-text">Not {s.skipped_platforms.map((p: string) => PLATFORM_LABEL[p] ?? p).join(", ")}: campaign doesn't allow it</div>}</td>
                      <td className="small-text">{s.rules ? [s.rules.hashtags?.join(" "), s.rules.credit && `credit: ${s.rules.credit.replace("{url}", "campaign link").replace("{name}", s.name)}`, s.rules.disclosure,
                        ...(s.rules.on_screen || []).map((x: string) => x.replace("streamer_", "keep ").replace("_", " "))].filter(Boolean).join(" · ") || "—" : "—"}</td>
                    </tr>))}</tbody>
                </table>
              </div>
              <div className="card options">
                <div className="field">Clip length
                  <div className="row" style={{ gap: 8 }}>
                    <input aria-label="Minimum seconds" type="number" min={10} max={opts.clip.max_sec} value={opts.clip.min_sec} onChange={(e) => set("clip", { ...opts.clip, min_sec: +e.target.value })} />
                    <span className="soft">to</span>
                    <input aria-label="Maximum seconds" type="number" min={opts.clip.min_sec} max={90} value={opts.clip.max_sec} onChange={(e) => set("clip", { ...opts.clip, max_sec: +e.target.value })} />
                    <span>seconds</span>
                  </div>
                </div>
                <div className="field">Caption style
                  <div className="row" style={{ gap: 8 }}>{CAPTIONS.map(([k, l]) => (
                    <button key={k} className={opts.captions.style === k ? "pill-btn on" : "pill-btn"} onClick={() => set("captions", { style: k })}>{l}</button>))}</div>
                </div>
                <div className="field">Layout
                  <div className="row" style={{ gap: 8 }}>{LAYOUTS.map(([k, l]) => (
                    <button key={k} className={opts.layout === k ? "pill-btn on" : "pill-btn"} onClick={() => set("layout", k)}>{l}</button>))}</div>
                  <span className="muted small-text">Campaigns that need the streamer's overlay or face on screen override this.</span>
                </div>
                <div className="field">Extras
                  <div className="row" style={{ gap: 8 }}>
                    <button className={opts.zoom ? "pill-btn on" : "pill-btn"} onClick={() => set("zoom", !opts.zoom)}>Punch-in zoom</button>
                    <button className={opts.progress_bar ? "pill-btn on" : "pill-btn"} onClick={() => set("progress_bar", !opts.progress_bar)}>Progress bar</button>
                    <button className={opts.auto_approve ? "pill-btn on" : "pill-btn"} onClick={() => set("auto_approve", !opts.auto_approve)}>Skip review (fully automatic)</button>
                  </div>
                </div>
              </div>
              <button className="link-btn" onClick={() => go("settings", "Accounts")}>Add or change posting accounts</button>
            </div>
          )}
          {err && <p className="bad-text">{err}</p>}
          {opts.clip.min_sec > opts.clip.max_sec && <p className="bad-text">Minimum clip length is longer than the maximum.</p>}
          <div className="footer-bar">
            <button className="btn-quiet" onClick={() => setStep(2)}>Back</button>
            <button className="btn-primary big" disabled={busy || !opts.platforms.length || opts.clip.min_sec > opts.clip.max_sec} onClick={() => create()}>{busy ? "Starting…" : "Start project"}</button>
          </div>
        </section>
      )}
    </div>
  );
}
