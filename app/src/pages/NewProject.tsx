// New Project: 1 Game -> 2 Streamers -> 3 Duration -> 4 Output (mockup flow).
import { useEffect, useState } from "react";
import { api, DURATIONS } from "../api";
import StreamerTable from "../components/StreamerTable";

const PLATFORMS: [string, string][] = [["youtube_shorts", "YouTube Shorts"], ["tiktok", "TikTok"], ["instagram_reels", "Instagram Reels"], ["x", "X"]];
const CAPTIONS: [string, string][] = [["bold_pop", "Bold pop"], ["clean", "Clean"], ["neon", "Neon"]];
const LAYOUTS: [string, string][] = [["auto", "Auto"], ["split", "Facecam + gameplay"], ["crop", "Action crop"], ["blur_fill", "Full frame on blur"]];

export default function NewProject({ done }: { done: () => void }) {
  const [step, setStep] = useState(1);
  const [games, setGames] = useState<any[]>([]);
  const [query, setQuery] = useState("");
  const [game, setGame] = useState<{ id: number; name: string } | null>(null);
  const [streamers, setStreamers] = useState<any[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [warn, setWarn] = useState("");
  const [duration, setDuration] = useState(3600);
  const [custom, setCustom] = useState({ value: 4, unit: 3600 });
  const [out, setOut] = useState({
    platforms: ["youtube_shorts", "tiktok"], clip: { min_sec: 20, max_sec: 45 }, captions: { style: "bold_pop" },
    layout: "auto", zoom: true, progress_bar: true, auto_approve: false, posting: "manual",
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.get("/games/trending").then(setGames).catch((e) => setWarn(e.message)); }, []);
  const search = () => { if (query.trim()) api.get(`/games/search?q=${encodeURIComponent(query)}`).then(setGames).catch((e) => setWarn(e.message)); };

  const refresh = async (id: number) => {
    setLoading(true); setWarn("");
    try {
      const r = await api.post(`/games/${id}/discover`);
      setStreamers(r.streamers);
      if (r.errors.length) setWarn(`Some platforms didn't answer: ${r.errors.join("; ")}`);
    } catch (e: any) { setWarn(e.message); }
    setLoading(false);
  };
  const pickGame = async (g: any) => {
    const { id } = await api.post("/games", {
      name: g.name, twitch_id: g.platform === "twitch" ? g.platform_game_id : null,
      kick_slug: g.platform === "kick" ? g.platform_game_id : null, box_art_url: g.box_art_url,
    });
    setGame({ id, name: g.name }); setSelected([]); setStep(2); refresh(id);
  };
  const create = async () => {
    setErr(""); setBusy(true);
    try {
      await api.post("/projects", {
        name: `${game!.name} · ${new Date().toLocaleDateString()}`, game_id: game!.id,
        streamer_ids: selected, duration_sec: duration, output_settings: out,
      });
      done();
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const toggleIn = (arr: string[], v: string) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const canGo = (n: number) => n === 1 || (n === 2 && !!game) || (n >= 3 && selected.length > 0);

  return (
    <div className="stack">
      <ol className="steps">{["Game", "Streamers", "Duration", "Output"].map((s, i) => (
        <li key={s}><button className={`step ${i + 1 === step ? "on" : i + 1 < step ? "done" : ""}`}
          disabled={!canGo(i + 1)} onClick={() => setStep(i + 1)}>{i + 1}  {s}</button></li>))}</ol>

      {step === 1 && (
        <section className="stack">
          <h1>Pick a game</h1>
          <div className="row" style={{ maxWidth: 620, flexWrap: "nowrap" }}>
            <label htmlFor="gamesearch" className="sr-only">Search games</label>
            <input id="gamesearch" className="grow" placeholder="Search any game…" value={query}
              onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} />
            <button className="btn-ghost" onClick={search}>Search</button>
          </div>
          {warn && <p className="warn-text">{warn}</p>}
          <span className="muted">Trending now</span>
          <div className="games">{games.map((g) => (
            <button key={`${g.platform}-${g.platform_game_id ?? g.name}`} className="game" onClick={() => pickGame(g)}>
              <div className="art">{g.box_art_url && <img src={g.box_art_url.replace("{width}x{height}", "285x380")} alt="" />}</div>
              <span style={{ fontWeight: 600 }}>{g.name}</span>
            </button>))}</div>
        </section>
      )}

      {step === 2 && game && (
        <section className="stack tight">
          <div className="row between">
            <h1>{game.name}: streamers</h1>
            <div className="row" style={{ gap: 8 }}>
              <button onClick={() => refresh(game.id)} disabled={loading}>{loading ? "Searching…" : "Refresh"}</button>
              <button onClick={() => setStep(1)}>Change game</button>
            </div>
          </div>
          <p className="muted">Pick the streamers to clip. Only <b className="soft">Allowed</b> and <b className="soft">Program member</b> streamers can be picked; click Edit to record permission or link a rewards program. Viewer counts and sizes are estimates from public data.</p>
          {warn && <p className="warn-text">{warn}</p>}
          {loading && streamers.length === 0 && <div className="row"><div className="spinner" style={{ width: 22, height: 22, borderWidth: 3 }} /><span className="muted">Looking for live streamers…</span></div>}
          {!loading && streamers.length === 0 && <p className="muted">No live streamers found. Check Twitch is connected in Settings, or try again when more people are live.</p>}
          {streamers.length > 0 && <StreamerTable gameId={game.id} streamers={streamers} selected={selected} onSelect={setSelected}
            reload={() => api.get(`/games/${game.id}/streamers`).then(setStreamers)} />}
          <div className="row">
            <button className="btn-primary" disabled={!selected.length} onClick={() => setStep(3)}>Next: run duration</button>
            <span className="muted">{selected.length} selected</span>
          </div>
        </section>
      )}

      {step === 3 && (
        <section className="stack">
          <h1>How long should it run?</h1>
          <div className="row">{DURATIONS.map(([l, s]) => (
            <button key={s} className={duration === s ? "pill-btn on" : "pill-btn"} onClick={() => setDuration(s)}>{l}</button>))}</div>
          <div className="row">
            <label htmlFor="customdur" className="muted">Custom</label>
            <input id="customdur" type="number" min={1} value={custom.value} onChange={(e) => setCustom({ ...custom, value: Math.max(1, +e.target.value) })} />
            <select aria-label="Unit" value={custom.unit} onChange={(e) => setCustom({ ...custom, unit: +e.target.value })}>
              <option value={60}>minutes</option><option value={3600}>hours</option><option value={86400}>days</option></select>
            <button className={duration === custom.value * custom.unit && !DURATIONS.some(([, s]) => s === duration) ? "pill-btn on" : "pill-btn"}
              onClick={() => setDuration(custom.value * custom.unit)}>Use custom</button>
          </div>
          <p className="muted">You can pause, resume or stop anytime from Home. Closing the window keeps it running in the tray.</p>
          <div className="row"><button className="btn-ghost" onClick={() => setStep(2)}>Back</button>
            <button className="btn-primary" onClick={() => setStep(4)}>Next: output</button></div>
        </section>
      )}

      {step === 4 && (
        <section className="stack">
          <h1>Output settings</h1>
          <div className="card options" style={{ padding: 20 }}>
            <div className="field">Post to
              <div className="row" style={{ gap: 8 }}>{PLATFORMS.map(([k, l]) => (
                <button key={k} className={out.platforms.includes(k) ? "pill-btn on" : "pill-btn"} onClick={() => setOut({ ...out, platforms: toggleIn(out.platforms, k) })}>{l}</button>))}</div>
            </div>
            <div className="field">Clip length
              <div className="row" style={{ gap: 8 }}>
                <input aria-label="Minimum seconds" type="number" min={10} max={out.clip.max_sec} value={out.clip.min_sec} onChange={(e) => setOut({ ...out, clip: { ...out.clip, min_sec: +e.target.value } })} />
                <span className="soft">to</span>
                <input aria-label="Maximum seconds" type="number" min={out.clip.min_sec} max={90} value={out.clip.max_sec} onChange={(e) => setOut({ ...out, clip: { ...out.clip, max_sec: +e.target.value } })} />
                <span>seconds</span>
              </div>
            </div>
            <div className="field">Caption style
              <div className="row" style={{ gap: 8 }}>{CAPTIONS.map(([k, l]) => (
                <button key={k} className={out.captions.style === k ? "pill-btn on" : "pill-btn"} onClick={() => setOut({ ...out, captions: { style: k } })}>{l}</button>))}</div>
            </div>
            <div className="field">Layout
              <div className="row" style={{ gap: 8 }}>{LAYOUTS.map(([k, l]) => (
                <button key={k} className={out.layout === k ? "pill-btn on" : "pill-btn"} onClick={() => setOut({ ...out, layout: k })}>{l}</button>))}</div>
            </div>
            <div className="field">Extras
              <div className="row" style={{ gap: 8 }}>
                <button className={out.zoom ? "pill-btn on" : "pill-btn"} onClick={() => setOut({ ...out, zoom: !out.zoom })}>Punch-in zoom</button>
                <button className={out.progress_bar ? "pill-btn on" : "pill-btn"} onClick={() => setOut({ ...out, progress_bar: !out.progress_bar })}>Progress bar</button>
                <button className={out.auto_approve ? "pill-btn on" : "pill-btn"} onClick={() => setOut({ ...out, auto_approve: !out.auto_approve })}>Skip review (fully automatic)</button>
              </div>
            </div>
            <label className="field">Posting
              <select value={out.posting} onChange={(e) => setOut({ ...out, posting: e.target.value })}>
                <option value="manual">Manual export</option><option value="scheduled" disabled>Auto-schedule (coming soon)</option></select></label>
          </div>
          {err && <p className="bad-text">{err}</p>}
          {out.clip.min_sec > out.clip.max_sec && <p className="bad-text">Minimum length is longer than maximum.</p>}
          <div className="row"><button className="btn-ghost" onClick={() => setStep(3)}>Back</button>
            <button className="btn-primary big" disabled={busy || out.clip.min_sec > out.clip.max_sec} onClick={create}>{busy ? "Starting…" : "Start project"}</button></div>
        </section>
      )}
    </div>
  );
}
