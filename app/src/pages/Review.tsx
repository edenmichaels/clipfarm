// Review screen: clip list on the left, preview + editable details on the right (mockup layout).
import { useEffect, useState } from "react";
import { api } from "../api";
import { Thumb } from "../components/ClipGrid";

export default function Review() {
  const [status, setStatus] = useState("pending");
  const [clips, setClips] = useState<any[]>([]);
  const [selId, setSelId] = useState<number | null>(null);

  const load = () => api.get(`/clips?review_status=${status}`).then((c: any[]) => {
    setClips(c);
    setSelId((cur) => (c.some((x) => x.id === cur) ? cur : c[0]?.id ?? null));
  }).catch(() => {});
  useEffect(() => { load(); }, [status]);

  const sel = clips.find((c) => c.id === selId) || null;
  const next = () => {
    const i = clips.findIndex((c) => c.id === selId);
    load().then(() => setSelId(clips[i + 1]?.id ?? clips[i - 1]?.id ?? null));
  };

  return (
    <div className="stack">
      <div className="row between">
        <div className="stack tight" style={{ gap: 4 }}>
          <h1>Review clips</h1>
          <span className="muted">Approve the ones you want to post, reject the rest.</span>
        </div>
        <div className="row" style={{ gap: 8 }}>
          {["pending", "approved", "rejected"].map((s) => (
            <button key={s} className={status === s ? "pill-btn on" : "pill-btn"} onClick={() => setStatus(s)}>{s[0].toUpperCase() + s.slice(1)}</button>
          ))}
        </div>
      </div>

      {clips.length === 0 ? <p className="muted">No {status} clips. New clips show up here as projects run.</p> : (
        <div className="split">
          <section className="grid small" style={{ flex: "1 1 300px", minWidth: 0 }}>
            {clips.map((c) => (
              <button key={c.id} className={c.id === selId ? "clip on" : "clip"} onClick={() => setSelId(c.id)}>
                <Thumb clip={c} />
                <div className="meta" style={{ padding: 8 }}>
                  <b style={{ fontSize: 12 }}>{c.title}</b>
                  <span className={`pill ${c.review_status}`} style={{ alignSelf: "flex-start" }}>{c.review_status}</span>
                </div>
              </button>
            ))}
          </section>
          {sel && <ClipEditor key={sel.id} clip={sel} onDone={next} />}
        </div>
      )}
    </div>
  );
}

function ClipEditor({ clip, onDone }: { clip: any; onDone: () => void }) {
  const [c, setC] = useState({ ...clip, tags: JSON.parse(clip.hashtags || "[]").map((t: string) => "#" + t).join(" ") });
  const [checklist, setChecklist] = useState<any[]>([]);
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get(`/clips/${clip.id}/checklist`).then(setChecklist).catch(() => {}); }, [clip.id]);

  const save = (review_status?: string) => api.patch(`/clips/${clip.id}`, {
    title: c.title, description: c.description, hook_text: c.hook_text,
    hashtags: c.tags.split(/[\s,]+/).filter(Boolean).map((t: string) => t.replace(/^#/, "")),
    ...(review_status ? { review_status } : {}),
  });
  const doExport = async () => {
    try {
      await save();
      const r = await api.post(`/clips/${clip.id}/export`, {});
      setMsg(`Saved to ${r.video}`);
    } catch (e: any) { setMsg(e.message); }
  };

  return (
    <section className="card" style={{ flex: "2 1 560px", minWidth: 0, display: "flex", flexWrap: "wrap", gap: 22, borderRadius: 14 }}>
      <div className="preview">
        {clip.file_path ? <video src={api.mediaUrl(clip.file_path)} controls /> : <div className="center muted" style={{ height: "100%" }}>File was cleaned up</div>}
      </div>
      <div className="stack tight grow" style={{ flexBasis: 260 }}>
        <div className="row" style={{ gap: 8 }}>
          <h2 style={{ fontSize: 18 }}>{clip.streamer}</h2>
          <span className="muted">· {clip.program || "no program"}{clip.score != null ? ` · highlight score ${clip.score.toFixed(2)}` : ""}</span>
        </div>
        <label className="field">Hook (first 2 seconds)<input value={c.hook_text || ""} onChange={(e) => setC({ ...c, hook_text: e.target.value })} /></label>
        <label className="field">Title<input value={c.title || ""} onChange={(e) => setC({ ...c, title: e.target.value })} /></label>
        <label className="field">Description<textarea rows={3} value={c.description || ""} onChange={(e) => setC({ ...c, description: e.target.value })} /></label>
        <label className="field">Hashtags<input value={c.tags} onChange={(e) => setC({ ...c, tags: e.target.value })} /></label>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn-primary" onClick={() => save("approved").then(onDone)}>Approve</button>
          <button className="btn-ghost btn-danger" onClick={() => save("rejected").then(onDone)}>Reject</button>
          <button className="btn-ghost" onClick={doExport} disabled={!clip.file_path}>Export MP4 + caption</button>
        </div>
        {msg && <span className="ok-text">{msg}</span>}
        <div className="checklist" style={{ marginTop: 4 }}>
          <h3>Rewards checklist</h3>
          {checklist.map((i, k) => <span key={k} className={i.done ? "ok-text" : "muted"}>{i.done ? "✓" : "○"} {i.item}</span>)}
        </div>
        <span className="muted" style={{ fontSize: 12 }}>Editing the hook here updates the post text. The text burned into the video stays as rendered.</span>
      </div>
    </section>
  );
}
