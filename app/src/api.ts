// Thin client for the local Python API. The Tauri shell tells us the port + per-launch token.
// If the engine crashes, the shell restarts it on the same port/token; meanwhile requests wait and
// retry, and the UI shows "Reconnecting" (see onConnection) instead of a blank screen.
import { invoke } from "@tauri-apps/api/core";

let base = "http://127.0.0.1:47821"; // replaced by the real port from the shell
let token = "";

export type ConnState = "connecting" | "connected" | "reconnecting" | "failed";
let state: ConnState = "connecting";
const listeners = new Set<(s: ConnState, detail?: string) => void>();

function setState(s: ConnState, detail?: string) {
  if (s === state && !detail) return;
  state = s;
  listeners.forEach((l) => l(s, detail));
}
export function onConnection(cb: (s: ConnState, detail?: string) => void): () => void {
  listeners.add(cb);
  cb(state);
  return () => { listeners.delete(cb); };
}
export const connectionState = () => state;

async function healthy(): Promise<boolean> {
  try {
    const r = await fetch(`${base}/health`);
    return r.ok;
  } catch { return false; }
}

async function waitForEngine(tries: number): Promise<boolean> {
  for (let i = 0; i < tries; i++) {
    if (await healthy()) { setState("connected"); return true; }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

export async function initApi(): Promise<void> {
  try {
    const info = await invoke<{ port: number; token: string }>("backend_info");
    base = `http://127.0.0.1:${info.port}`;
    token = info.token;
  } catch {
    /* running in a plain browser during dev: use defaults, backend in dev mode (no token) */
  }
  // The shell reports engine restarts so the UI can show "Reconnecting" straight away.
  try {
    const { listen } = await import("@tauri-apps/api/event");
    await listen<{ state: string; message?: string }>("engine-status", (e) => {
      if (e.payload.state === "restarting") setState("reconnecting", e.payload.message);
      else if (e.payload.state === "failed") setState("failed", e.payload.message);
      else if (e.payload.state === "running") waitForEngine(60);
    });
  } catch { /* not in Tauri */ }
  // Wait for the sidecar to come up (first launch can take a few seconds).
  if (await waitForEngine(60)) return;
  setState("failed");
  throw new Error("The ClipFarm engine didn't start. Open Logs, or reinstall ClipFarm.");
}

class ApiError extends Error {
  detail: any;
  status: number;
  constructor(msg: string, status: number, detail: any) { super(msg); this.status = status; this.detail = detail; }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  // Retry while the engine is restarting (network error), up to ~60 s.
  for (let attempt = 0; ; attempt++) {
    let r: Response;
    try {
      r = await fetch(base + path, {
        method,
        headers: { "Content-Type": "application/json", "X-ClipFarm-Token": token },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      // A request that fails inside the engine can come back without CORS headers and look like a network
      // error. If the engine itself answers, it's that one request: report it instead of "Reconnecting".
      if (attempt === 0 && (await healthy())) {
        if (state !== "connected") setState("connected");
        throw new ApiError("ClipFarm couldn't do that. Details are in Settings › Logs.", 500, null);
      }
      if (attempt >= 120) { setState("failed"); throw new Error("Lost connection to the ClipFarm engine."); }
      setState("reconnecting");
      await new Promise((res) => setTimeout(res, 500));
      continue;
    }
    if (state !== "connected") setState("connected");
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      const d = detail.detail;
      const msg = typeof d === "string" ? d : d?.message ? `${d.message} ${(d.reasons || []).join(" ")}`.trim() : r.statusText;
      throw new ApiError(msg, r.status, d);
    }
    return r.json();
  }
}

export const api = {
  get: <T = any>(p: string) => req<T>("GET", p),
  post: <T = any>(p: string, b?: unknown) => req<T>("POST", p, b ?? {}),
  put: <T = any>(p: string, b: unknown) => req<T>("PUT", p, b),
  patch: <T = any>(p: string, b: unknown) => req<T>("PATCH", p, b),
  del: <T = any>(p: string) => req<T>("DELETE", p),
  frameUrl: (cid: number, t: number, head: number) => `${base}/edit/${cid}/frame?t=${t.toFixed(2)}&h=${head}&token=${token}`,
  styleRefUrl: (rid: number) => `${base}/style-refs/${rid}/image?token=${token}`,
  mediaUrl: (path: string) => `${base}/media?path=${encodeURIComponent(path)}&token=${token}`,
  fontUrl: (file: string) => `${base}/fonts/${encodeURIComponent(file)}`,
  events: () => new EventSource(`${base}/events?token=${token}`),
};

export const DURATIONS: [string, number][] = [
  ["30 min", 1800], ["1 hr", 3600], ["3 hr", 10800], ["6 hr", 21600], ["12 hr", 43200],
  ["24 hr", 86400], ["2 days", 172800], ["3 days", 259200], ["5 days", 432000], ["7 days", 604800],
];

export const PERMISSIONS = {
  allowed: { label: "Allowed", cls: "ok" },
  program_member: { label: "Program member", cls: "ok" },
  unknown: { label: "Unknown", cls: "warn" },
  prohibited: { label: "Prohibited", cls: "bad" },
} as const;

export const PLATFORM_LABEL: Record<string, string> = {
  youtube_shorts: "Shorts", tiktok: "TikTok", instagram_reels: "Reels", x: "X", youtube: "YouTube", instagram: "Instagram",
  twitch: "Twitch", kick: "Kick",
};
export const aud = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `A$${v.toFixed(digits)}`);
export const band = (lo?: number | null, li?: number | null, hi?: number | null) =>
  li == null ? "—" : `${aud(li)} (${aud(lo ?? 0, 0)}–${aud(hi ?? 0, 0)})`;

/** Wait for a background job (re-render, export) to finish. */
export async function waitForJob(id: number, onProgress?: (p: number) => void): Promise<any> {
  for (let i = 0; i < 600; i++) {
    const jobs = await api.get<any[]>("/jobs?active=false&limit=200");
    const j = jobs.find((x) => x.id === id);
    if (j && ["done", "failed", "cancelled"].includes(j.status)) {
      if (j.status !== "done") throw new Error(j.error || `Job ${j.status}`);
      return j;
    }
    if (j && onProgress) onProgress(j.progress || 0);
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("Timed out waiting for the job");
}

export const MOMENT_EMOJI: Record<string, string> = {
  "Funny": "😂", "Action/Explosion": "💥", "Rage/Reaction": "😱", "Clutch/Skill": "🏆", "Fail": "💀", "Wholesome": "🙂",
  "Chaos/Fail": "💀", "Action + Reaction": "💥", "Rage": "😱",
};
