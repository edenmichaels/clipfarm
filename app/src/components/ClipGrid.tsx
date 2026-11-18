// Clip library: filter pills + 9:16 thumbnails, as in the mockup's Home screen.
import { useEffect, useState } from "react";
import { api } from "../api";

const FILTERS: [string, string][] = [["All", ""], ["Pending", "review_status=pending"], ["Approved", "review_status=approved"], ["Posted", "posted=true"]];

export const fmtLen = (s?: number) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "");

export function Thumb({ clip }: { clip: any }) {
  return (
    <div className="thumb">
      {clip.thumb_path && <img src={api.mediaUrl(clip.thumb_path)} alt="" />}
      {!clip.thumb_path && clip.hook_text && <span className="hook">{clip.hook_text}</span>}
      <span className="len">{fmtLen(clip.duration_sec)}</span>
    </div>
  );
}

export default function ClipGrid({ title, onOpen }: { title: string; onOpen: (c: any) => void }) {
  const [filter, setFilter] = useState("All");
  const [clips, setClips] = useState<any[]>([]);

  useEffect(() => {
    const q = FILTERS.find(([l]) => l === filter)![1];
    const load = () => api.get(`/clips?${q}`).then(setClips).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [filter]);

  return (
    <>
      <div className="row between">
        <h2>{title}</h2>
        <div className="row" style={{ gap: 8 }}>
          {FILTERS.map(([l]) => (
            <button key={l} className={filter === l ? "pill-btn on" : "pill-btn"} style={{ padding: "6px 14px" }} onClick={() => setFilter(l)}>{l}</button>
          ))}
        </div>
      </div>
      {clips.length === 0 && <p className="muted">No clips here yet.</p>}
      <div className="grid">
        {clips.map((c) => (
          <button key={c.id} className="clip" onClick={() => onOpen(c)}>
            <Thumb clip={c} />
            <div className="meta">
              <b title={c.title}>{c.title}</b>
              <span className="muted" style={{ fontSize: 12 }}>{c.streamer} · {c.game}</span>
              <span style={{ fontSize: 12 }}>
                <span className={`pill ${c.review_status}`}>{c.review_status}</span>{" "}
                {c.views > 0 && <span className="muted">{c.views.toLocaleString()} views</span>}
              </span>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}
