// Thin client for the local Python API. The Tauri shell tells us the port + per-launch token.
import { invoke } from "@tauri-apps/api/core";

let base = "http://127.0.0.1:47821"; // replaced by the real port from the shell
let token = "";

export async function initApi(): Promise<void> {
  try {
    const info = await invoke<{ port: number; token: string }>("backend_info");
    base = `http://127.0.0.1:${info.port}`;
    token = info.token;
  } catch {
    /* running in a plain browser during dev: use defaults, backend in dev mode (no token) */
  }
  // Wait for the sidecar to come up (first launch can take a few seconds).
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`${base}/health`);
      if (r.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("The ClipFarm engine didn't start. Check Logs or reinstall.");
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(base + path, {
    method,
    headers: { "Content-Type": "application/json", "X-ClipFarm-Token": token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    const d = detail.detail;
    throw new Error(typeof d === "string" ? d : d?.message ? `${d.message} ${(d.reasons || []).join(" ")}` : r.statusText);
  }
  return r.json();
}

export const api = {
  get: <T = any>(p: string) => req<T>("GET", p),
  post: <T = any>(p: string, b?: unknown) => req<T>("POST", p, b ?? {}),
  put: <T = any>(p: string, b: unknown) => req<T>("PUT", p, b),
  patch: <T = any>(p: string, b: unknown) => req<T>("PATCH", p, b),
  del: <T = any>(p: string) => req<T>("DELETE", p),
  mediaUrl: (path: string) => `${base}/media?path=${encodeURIComponent(path)}&token=${token}`,
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
