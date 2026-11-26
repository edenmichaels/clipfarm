// My Clips: every clip you've made (sort, select, delete), Post & Submit (posting + the "submit to campaign"
// checklist), and Recently deleted (7 days, Restore, Empty now).
import { useEffect, useState } from "react";
import ClipLibrary, { RecentlyDeleted } from "../components/ClipLibrary";
import type { Page } from "../App";
import Submissions from "./Submissions";

export const CLIP_TABS = ["All clips", "Post & Submit", "Recently deleted"];

export default function MyClips({ initialTab, go }: { initialTab?: string; go: (p: Page, t?: string) => void }) {
  const [tab, setTab] = useState(initialTab && CLIP_TABS.includes(initialTab) ? initialTab : "All clips");
  useEffect(() => { if (initialTab && CLIP_TABS.includes(initialTab)) setTab(initialTab); }, [initialTab]);
  return (
    <div className="stack">
      <header className="page-head"><h1>My Clips</h1></header>
      <div className="tabs" role="tablist">
        {CLIP_TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "tab on" : "tab"} onClick={() => setTab(t)}>{t}</button>)}
      </div>
      <div className="embedded">
        {tab === "All clips" && <ClipLibrary onOpen={(c) => go("editor", `clip:${c.id}`)} onSendToPost={() => setTab("Post & Submit")} />}
        {tab === "Post & Submit" && <Submissions />}
        {tab === "Recently deleted" && <RecentlyDeleted />}
      </div>
    </div>
  );
}
