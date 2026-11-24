// Live indicator for streamer pickers (v0.8.8): green = live now, grey = offline, hollow = status unavailable.
// useLiveStatus polls /streamers/live every 60 s while the picker is on screen and stops when it closes.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";

export type LiveState = { state: "live" | "offline" | "unknown"; viewers?: number | null; since?: number | null; game?: string | null; title?: string | null; note?: string };

export const POLL_MS = 60_000;

export function useLiveStatus(ids: number[], open = true): Record<string, LiveState> {
  const [st, setSt] = useState<Record<string, LiveState>>({});
  const key = [...ids].sort((a, b) => a - b).join(",");
  const timer = useRef<any>(null);
  useEffect(() => {
    if (!open || !key) return;
    let alive = true;
    const load = () => api.get(`/streamers/live?ids=${key}`).then((r: any) => { if (alive) setSt(r); })
      .catch(() => { if (alive) setSt(Object.fromEntries(key.split(",").map((id) => [id, { state: "unknown", note: "status unavailable" }]))); });
    load();
    timer.current = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(timer.current); };   // picker closed: stop polling
  }, [key, open]);
  return st;
}

export const liveRank = (st?: LiveState) => (st?.state === "live" ? 0 : st?.state === "offline" ? 1 : 2);

const short = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : `${n}`);
const age = (since: number) => {
  const m = Math.max(0, Math.round((Date.now() / 1000 - since) / 60));
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
};

export function LiveDot({ st, projectGame }: { st?: LiveState; projectGame?: string | null }) {
  if (!st) return null;
  if (st.state === "unknown") return <span className="live-dot unknown" title={st.note || "status unavailable"}><i />{st.note === "status unavailable" || !st.note ? "status unavailable" : st.note}</span>;
  if (st.state === "offline") return <span className="live-dot off" title="Offline"><i /></span>;
  const other = !!(projectGame && st.game && !st.game.toLowerCase().includes(projectGame.toLowerCase()) && !projectGame.toLowerCase().includes(st.game.toLowerCase()));
  return (
    <span className="live-dot on" title={st.title || "Live now"}>
      <i />{st.viewers != null ? short(st.viewers) : "live"}{st.since ? ` · ${age(st.since)}` : ""}
      {st.game && <span className={other ? "live-game other" : "live-game"}>{other ? `playing ${st.game}` : st.game}</span>}
    </span>
  );
}
