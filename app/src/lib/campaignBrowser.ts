// In-app campaign browser (a separate Tauri window on contentrewards.com).
// The window's toolbar sends the page on screen ONLY when you press "Import this page";
// this file receives it, sends it to the engine, and shows the summary in both windows.
import { invoke } from "@tauri-apps/api/core";
import { api } from "../api";

export type ImportSummary = { summary: string; new: any[]; updated: any[]; unchanged: any[]; ended: any[]; total: number };

export async function openCampaignBrowser(url?: string | null): Promise<void> {
  try {
    await invoke("open_campaign_browser", { url: url ?? null });
  } catch (e: any) {
    // Not running inside the desktop app (dev in a plain browser): open a normal tab instead.
    if (typeof e === "string") throw new Error(e);
    window.open(url || "https://contentrewards.com/c/discover", "_blank", "noopener");
  }
}

function decode(b64url: string): any {
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64url.length + 3) % 4);
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return JSON.parse(new TextDecoder().decode(bytes));
}

const subscribers = new Set<(s: ImportSummary) => void>();
/** Pages that show campaigns subscribe so they refresh after an import. */
export function onCampaignImport(cb: (s: ImportSummary) => void): () => void {
  subscribers.add(cb);
  return () => { subscribers.delete(cb); };
}

let started = false;
/** Started once by App: listens for "Import this page" presses in the campaign browser. */
export async function listenForImports(toast: (msg: string) => void): Promise<void> {
  if (started) return;
  started = true;
  try {
    const { listen } = await import("@tauri-apps/api/event");
    await listen<string>("campaign-page", async (e) => {
      let msg: string;
      try {
        const page = decode(e.payload);
        const s = await api.post<ImportSummary>("/campaigns/import-page", page);
        msg = s.summary;
        const changes = s.updated.flatMap((u: any) => u.changes.map((c: string) => `${u.name}: ${c}`)).slice(0, 3);
        if (changes.length) msg += " " + changes.join("; ");
        subscribers.forEach((cb) => cb(s));
      } catch (err: any) {
        msg = `Couldn't import this page: ${err?.message || err}`;
      }
      toast(msg);
      invoke("campaign_browser_status", { message: msg }).catch(() => {});
    });
  } catch { /* not in Tauri */ }
}
