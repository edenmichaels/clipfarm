// The streamer list in three sections, each sorted by projected earnings (A$ per clip, likely value):
//   Joined          campaigns you're in. Ready to clip.
//   Worth joining   campaigns you haven't joined that project above your minimum. "Open to join" opens the page
//                   in ClipFarm's campaign browser; you press Join yourself, then tick "I've joined".
//   No campaign     nobody's paying for these. Collapsed; clipping them needs your OK.
// Clicking a streamer matches their campaign straight away (or asks one question, once).
import { useEffect, useState } from "react";
import { api, aud, PLATFORM_LABEL } from "../api";
import { openCampaignBrowser } from "../lib/campaignBrowser";
import { WhyRanked } from "./StreamerTable";

export const clippable = (s: any) => s.permission_status === "allowed" || s.permission_status === "program_member";

type Props = {
  gameId: number; groups: any; selected: number[]; onSelect: (ids: number[]) => void; reload: () => Promise<void> | void;
};

function Money({ pj }: { pj: any }) {
  if (!pj) return <span className="muted">—</span>;
  const [lo, li, hi] = pj.per_clip;
  return (
    <span className="money" title={`Likely ${aud(li)} per clip · range ${aud(lo)}–${aud(hi)} · best on ${PLATFORM_LABEL[pj.platform] ?? pj.platform}`}>
      {aud(li)}<small>/clip</small>
    </span>
  );
}

export default function StreamerSections({ gameId, groups, selected, onSelect, reload }: Props) {
  const [open, setOpen] = useState<any>(null);
  const [why, setWhy] = useState<any>(null);
  const [showNone, setShowNone] = useState(false);
  const [showLeftOut, setShowLeftOut] = useState(false);
  const [msg, setMsg] = useState("");

  const toggle = (s: any) => {
    setMsg("");
    if (selected.includes(s.id)) return onSelect(selected.filter((x) => x !== s.id));
    if (!clippable(s)) {
      setOpen(s);
      return;
    }
    onSelect([...selected, s.id]);
  };

  const row = (s: any, kind: "joined" | "worth" | "left" | "none") => {
    const on = selected.includes(s.id);
    const canTick = kind === "joined" || kind === "none";
    return (
      <div key={`${kind}-${s.id}`} className={`srow ${on ? "on" : ""}`}>
        {canTick
          ? <button className={on ? "tick on" : "tick"} aria-label={`Select ${s.display_name}`} onClick={() => toggle(s)}>{on ? "✓" : ""}</button>
          : <span className="tick ghost" aria-hidden="true" />}
        <button className="srow-main" onClick={() => setOpen(s)}>
          <span className="sname">{s.display_name}</span>
          <span className="muted small-text">{PLATFORM_LABEL[s.platform] ?? s.platform}{s.is_live ? ` · live · ${(s.current_viewers ?? 0).toLocaleString()} watching` : ""}</span>
          <span className="sreason">{kind === "left" ? s.excluded_reason : kind === "none" ? "Not paying clippers" : s.campaign ? `${s.campaign.name} — ${s.reason}` : s.reason}</span>
        </button>
        <Money pj={s.projection} />
        <div className="srow-actions">
          {kind === "worth" && <button onClick={() => openCampaignBrowser(s.campaign.url).catch((e) => setMsg(e.message))}>Open to join</button>}
          <button className="btn-quiet" onClick={() => setWhy(s)}>Why here?</button>
        </div>
      </div>
    );
  };

  const joined = groups?.joined ?? [], worth = groups?.worth_joining ?? [], left = groups?.worth_joining_left_out ?? [],
    none = groups?.no_campaign ?? [];

  return (
    <div className="stack tight">
      {msg && <p className="warn-text">{msg}</p>}
      <section className="ssection">
        <h3>Joined <span className="muted">· ready to clip</span></h3>
        {joined.length === 0
          ? <p className="muted empty-line">None of your campaigns' streamers are live in this game right now. Check "Worth joining" below.</p>
          : joined.map((s: any) => row(s, "joined"))}
      </section>

      <section className="ssection">
        <h3>Worth joining <span className="muted">· pays above your A${(groups?.min_aud_per_clip ?? 0).toFixed(2)}/clip minimum</span></h3>
        {worth.length === 0
          ? <p className="muted empty-line">No new campaigns worth joining for this game. Import more from the campaign browser (Settings › Campaigns).</p>
          : worth.map((s: any) => row(s, "worth"))}
        {left.length > 0 && (
          <>
            <button className="link-btn" onClick={() => setShowLeftOut(!showLeftOut)}>
              {showLeftOut ? "Hide" : "Show"} {left.length} not worth joining right now
            </button>
            {showLeftOut && left.map((s: any) => row(s, "left"))}
          </>
        )}
      </section>

      <section className="ssection">
        <button className="section-toggle" onClick={() => setShowNone(!showNone)} aria-expanded={showNone}>
          <h3>{showNone ? "▾" : "▸"} No campaign <span className="muted">· {none.length} streamer{none.length === 1 ? "" : "s"} · not paid</span></h3>
        </button>
        {showNone && none.map((s: any) => row(s, "none"))}
      </section>

      {open && <StreamerPanel s={open} onClose={async (changed) => {
        setOpen(null);
        if (changed) await reload();
      }} onSelect={(id) => onSelect(selected.includes(id) ? selected : [...selected, id])} />}
      {why && <WhyRanked s={why} gameId={gameId} onClose={() => setWhy(null)} />}
    </div>
  );
}

/** Opens when you click a streamer: matches their campaign at once, or asks one question (remembered). */
function StreamerPanel({ s, onClose, onSelect }: { s: any; onClose: (changed: boolean) => void; onSelect: (id: number) => void }) {
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState("");
  const [changed, setChanged] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.post(`/streamers/${s.id}/match`).then((r) => { setRes(r); if (r.auto) setChanged(true); }).catch((e) => setErr(e.message));
  }, [s.id]);

  const answer = async (programId: number | null) => {
    setBusy(true);
    try { setRes(await api.post(`/streamers/${s.id}/match/answer`, { program_id: programId })); setChanged(true); }
    catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const markJoined = async (pid: number) => {
    setBusy(true);
    try {
      await api.post(`/campaigns/${pid}/joined`, { joined: true });
      setRes(await api.post(`/streamers/${s.id}/match`));
      setChanged(true);
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };

  const c = res?.campaign;
  const pj = s.projection;
  return (
    <div className="modal" onClick={() => onClose(changed)}>
      <div className="modal-body stack" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={s.display_name}>
        <div className="row between">
          <h2>{s.display_name} <span className="muted small-text">{PLATFORM_LABEL[s.platform] ?? s.platform}</span></h2>
          <button className="btn-quiet" onClick={() => onClose(changed)} aria-label="Close">✕</button>
        </div>
        {err && <p className="bad-text">{err}</p>}
        {!res && !err && <p className="muted">Finding their campaign…</p>}

        {res?.state === "ask" && (
          <div className="stack tight">
            <p><b>{res.question}</b></p>
            {res.options.map((o: any) => (
              <button key={o.program_id} className="choice" disabled={busy} onClick={() => answer(o.program_id)}>
                <b>{o.name}</b><span className="muted small-text">{o.reason}{o.joined ? " · joined" : ""}</span>
              </button>))}
            <button className="choice" disabled={busy} onClick={() => answer(null)}><b>None of these</b></button>
            <span className="muted small-text">ClipFarm remembers your answer and won't ask again.</span>
          </div>
        )}

        {res?.state === "none" && <p className="muted">No campaign is paying for {s.display_name}'s clips. You can still clip them, but you won't be paid.</p>}

        {c && (
          <div className="stack tight">
            <p className="note-ok">✓ {res.note}</p>
            <div className="facts">
              <span>Pays</span><b>{c.campaign_type === "prize_pool" ? `$${(c.pool_size ?? 0).toLocaleString()} prize pool` : c.payout_per_1k != null ? `$${c.payout_per_1k}/1K views` : "?"}</b>
              {c.max_payout != null && <><span>Per clip</span><b>{c.min_payout ? `$${c.min_payout} min · ` : ""}${c.max_payout} max</b></>}
              <span>Projected</span><b>{pj ? `${aud(pj.per_clip[1])} per clip (${aud(pj.per_clip[0])}–${aud(pj.per_clip[2])})` : "—"}</b>
              <span>Post to</span><b>{(c.allowed_platforms || []).map((p: string) => PLATFORM_LABEL[p] ?? p).join(", ") || "Not set yet (check the campaign page)"}</b>
              {c.required_tags?.length > 0 && <><span>Hashtags</span><b>{c.required_tags.join(" ")}</b></>}
              {c.required_onscreen?.length > 0 && <><span>On screen</span><b>{c.required_onscreen.map((x: string) => x.replace("streamer_", "keep ").replace("_", " ")).join(", ")}</b></>}
              {c.disclosure && <><span>Disclosure</span><b>{c.disclosure}</b></>}
              <span>Joined</span><b>{c.joined ? "Yes" : "Not yet"}</b>
            </div>
            {c.needs_confirming?.length > 0 && <span className="muted small-text">Some details need confirming in Settings › Campaigns.</span>}
            <div className="row" style={{ gap: 8 }}>
              {c.joined ? <button className="btn-primary" onClick={() => { onSelect(s.id); onClose(true); }}>Clip {s.display_name}</button>
                : <>
                    <button className="btn-primary" onClick={() => openCampaignBrowser(c.url).catch((e) => setErr(e.message))}>Open to join</button>
                    <button disabled={busy} onClick={() => markJoined(c.id)}>I've joined</button>
                  </>}
            </div>
            {!c.joined && <span className="muted small-text">Press Join on the campaign page yourself. ClipFarm notices next time you import that page, or tick "I've joined".</span>}
          </div>
        )}
      </div>
    </div>
  );
}
