// My Clips: clips waiting for review, and Post & Submit (posting + the "submit to campaign" checklist).
import { useEffect, useState } from "react";
import Review from "./Review";
import Submissions from "./Submissions";

export const CLIP_TABS = ["To review", "Post & Submit"];

export default function MyClips({ initialTab }: { initialTab?: string }) {
  const [tab, setTab] = useState(initialTab && CLIP_TABS.includes(initialTab) ? initialTab : "To review");
  useEffect(() => { if (initialTab && CLIP_TABS.includes(initialTab)) setTab(initialTab); }, [initialTab]);
  return (
    <div className="stack">
      <header className="page-head"><h1>My Clips</h1></header>
      <div className="tabs" role="tablist">
        {CLIP_TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "tab on" : "tab"} onClick={() => setTab(t)}>{t}</button>)}
      </div>
      <div className="embedded">{tab === "To review" ? <Review /> : <Submissions />}</div>
    </div>
  );
}
