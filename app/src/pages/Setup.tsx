// First-run wizard (mockup layout): Welcome -> Storage -> Connections -> Hardware.
import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api } from "../api";
import { Brand } from "../components/Icons";
import Connections from "../components/Connections";
import Disclaimer from "../components/Disclaimer";

const STEPS = ["Welcome", "Storage", "Connections", "Hardware"];

export default function Setup({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(1);
  const [agreed, setAgreed] = useState(false);
  const [mediaDir, setMediaDir] = useState("");
  const [capGb, setCapGb] = useState(50);
  const [system, setSystem] = useState<any>(null);
  const [err, setErr] = useState("");

  useEffect(() => { api.get("/system").then((s) => { setSystem(s); setMediaDir(s.disk.media_dir); }).catch((e) => setErr(e.message)); }, []);

  const pick = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Where should ClipFarm store videos?" });
    if (typeof dir === "string") setMediaDir(dir);
  };
  const finish = async () => {
    try {
      await api.put("/settings", { media_dir: mediaDir, storage_cap_gb: capGb, disclaimer_accepted: true, setup_complete: true });
      onDone();
    } catch (e: any) { setErr(e.message); }
  };
  const gpu = system?.encoders?.find((e: any) => e.available && e.codec !== "libx264");

  return (
    <div className="wizard-wrap">
      <div className="wizard">
        <Brand big />
        <ol className="steps">{STEPS.map((s, i) => (
          <li key={s}><span className={`step ${i + 1 === step ? "on" : i + 1 < step ? "done" : ""}`} style={{ display: "inline-block" }}>{i + 1}  {s}</span></li>))}</ol>

        <section className="card">
          {step === 1 && <>
            <h1>Welcome to ClipFarm</h1>
            <p className="soft" style={{ lineHeight: 1.5 }}>ClipFarm finds highlight moments from streamers who allow clipping, edits them into vertical shorts, and helps you post them to your own accounts.</p>
            <Disclaimer />
            <button className="check" style={{ background: "none", border: "none", padding: "4px 0", alignSelf: "flex-start" }} onClick={() => setAgreed(!agreed)}>
              <span className={agreed ? "tick on" : "tick"} aria-hidden="true">{agreed ? "✓" : ""}</span>I understand and agree</button>
          </>}

          {step === 2 && <>
            <h1>Where should videos go?</h1>
            <p className="muted">Downloads and finished clips are saved here. Source videos are deleted after processing.</p>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input aria-label="Media folder" className="grow" value={mediaDir} readOnly />
              <button className="btn-ghost" onClick={pick}>Choose folder…</button>
            </div>
            <label className="row">Max space to use <input type="number" min={5} value={capGb} onChange={(e) => setCapGb(Math.max(5, +e.target.value))} /> GB</label>
            <span className="muted">Free on this drive: {system ? (system.disk.disk_free / 1e9).toFixed(0) : "…"} GB</span>
          </>}

          {step === 3 && <>
            <h1>Connect platforms</h1>
            <p className="muted">You need at least Twitch to find streamers. Keys are kept in your Windows keychain. You can skip this and do it later in Settings.</p>
            <Connections />
          </>}

          {step === 4 && <>
            <h1>Hardware check</h1>
            {gpu ? <span className="ok-text" style={{ fontWeight: 600 }}>✓ {gpu.label} found. Clips will render on your graphics card.</span>
                 : <span className="warn-text">No supported graphics encoder found. Clips will render on your CPU (slower, but it works).</span>}
            <div className="notice" style={{ gap: 6 }}>
              {system?.encoders?.map((e: any) => <span key={e.codec} className={e.available ? "" : "muted"}>{e.label}: {e.available ? "available" : "not available"}</span>)}
            </div>
          </>}

          {err && <span className="bad-text">{err}</span>}
          <div className="row between" style={{ marginTop: 6 }}>
            <button className="btn-ghost" style={{ visibility: step === 1 ? "hidden" : "visible" }} onClick={() => setStep(step - 1)}>Back</button>
            {step < 4
              ? <button className="btn-primary" disabled={step === 1 && !agreed} onClick={() => setStep(step + 1)}>Next</button>
              : <button className="btn-primary" onClick={finish}>Finish setup</button>}
          </div>
        </section>
      </div>
    </div>
  );
}
