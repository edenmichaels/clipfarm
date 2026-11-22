"""Top-10 precision of highlight picks, before (v0.3 loudness + chat) vs after (v0.4), on the labelled set.

Usage: python scripts/eval_highlights.py <eval dir from build_eval_set.py> <report.md>
A pick is correct if its peak falls inside a labelled funny/action/reaction moment (+-2 s) or its window
overlaps one by 8 s or more. Each labelled moment counts once (a second pick of it isn't a new hit).
The LLM rating isn't used (no model in CI), so v0.4 runs on sound events, chat, visuals only.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from clipfarm.highlights import audio_events, run, scorer, signals, visual  # noqa: E402

d, report = Path(sys.argv[1]), Path(sys.argv[2])
labels = json.loads((d / "labels.json").read_text())


def chat(i):
    return [(m["t"], m["m"]) for m in map(json.loads, (d / f"chat{i}.jsonl").read_text().splitlines() if
                                          (d / f"chat{i}.jsonl").read_text().strip() else [])]


def hit(m, labs):
    for j, l in enumerate(labs):
        overlap = min(m["end"], l["end"]) - max(m["start"], l["start"])
        if l["start"] - 2 <= m["peak"] <= l["end"] + 2 or overlap >= 8:
            return j
    return None


def score(picks):
    used, rows = set(), []
    for p in picks:
        j = hit(p, labels[p["vod"]])
        ok = j is not None and (p["vod"], j) not in used
        if ok:
            used.add((p["vod"], j))
        rows.append({**p, "correct": ok, "label": labels[p["vod"]][j]["type"] if j is not None else None})
    return sum(r["correct"] for r in rows) / max(1, len(rows)), rows


before, after = [], []
for i in (1, 2, 3):
    vod = d / f"vod{i}.mp4"
    msgs = chat(i)
    # --- before: v0.3 detector (loudness + chat rate + emote bursts)
    audio = signals.audio_rms(vod)
    rate, burst = signals.chat_series(msgs, len(audio))
    curve, normed = scorer.composite({"audio": audio, "chat": rate, "emote": burst})
    for m in scorer.pick_moments(curve, normed, min_len=15, max_len=60, threshold=0.0, top_n_per_hour=60, min_gap=30):
        before.append({"vod": f"vod{i}", "start": m.start, "end": m.end, "peak": m.peak, "score": m.score})
    # --- after: v0.4 detector
    vis = visual.analyse(vod)
    ms, _ = run.detect_source(vod, chat=msgs, budget=10, min_len=15, max_len=60, threshold=0.0,
                              visual_fn=lambda a, b, v=vis: {k: x[int(a):int(b)] for k, x in v.items()})
    for m in ms:
        after.append({"vod": f"vod{i}", "start": m.start, "end": m.end, "peak": m.peak, "score": m.score,
                      "type": m.type, "reason": m.reason})

top_b = sorted(before, key=lambda r: -r["score"])[:10]
top_a = sorted(after, key=lambda r: -r["score"])[:10]
pb, rb = score(top_b)
pa, ra = score(top_a)
typed = [r for r in ra if r["correct"]]
type_acc = sum(r["type"] == r["label"] for r in typed) / max(1, len(typed))
n_pos = sum(len(v) for k, v in labels.items() if not k.endswith("_plan"))
lines = [
    "# Highlight detection: evaluation report", "",
    f"Labelled set: 3 synthetic VODs (~8 min each), {n_pos} labelled funny/action/reaction moments. Real CC0 "
    "recordings for laughter, explosions, gunfire, cheering and screaming (see SOURCES.md); speech from "
    "espeak-ng; video and chat generated.", "",
    "Traps (the mundane moments that fooled loudness-based picking): menu/inventory screens with loud music; "
    "loading and BRB screens; calm talking; the streamer shouting at chat with chat spamming caps; loud "
    "intermission music with chat vibing to it (catJAM, 🔥); and sub trains with loud thank-yous and a flood "
    "of PogChamp / W / gifted-sub messages. Positive moments get realistic chat (about 2 messages a second).", "",
    f"Sound-event model: {'YAMNet (ONNX)' if audio_events.available() else 'NOT AVAILABLE'}. LLM rating: not used.", "",
    "| | Top-10 precision |", "|---|---|",
    f"| Before (v0.3: loudness + chat rate) | **{pb:.0%}** |", f"| After (v0.4) | **{pa:.0%}** |", "",
    f"Moment type correct on the v0.4 hits: {type_acc:.0%}", "",
    "## Before: top 10", "", "| VOD | Window | Score | Correct | What it was |", "|---|---|---|---|---|"]


def what(r):
    if r["label"]:
        return r["label"]
    plan = labels[r["vod"] + "_plan"]
    t = 0
    for k, dur, lab in plan:
        if t <= r["peak"] < t + dur:
            return f"{k} ({lab})"
        t += dur
    return "?"


for r in rb:
    lines.append(f"| {r['vod']} | {r['start']:.0f}-{r['end']:.0f}s | {r['score']:.2f} | {'yes' if r['correct'] else 'no'} | {what(r)} |")
lines += ["", "## After: top 10", "", "| VOD | Window | Score | Type | Correct | What it was |", "|---|---|---|---|---|---|"]
for r in ra:
    lines.append(f"| {r['vod']} | {r['start']:.0f}-{r['end']:.0f}s | {r['score']:.2f} | {r['type']} | "
                 f"{'yes' if r['correct'] else 'no'} | {what(r)} |")
report.write_text("\n".join(lines) + "\n")
(report.with_suffix(".json")).write_text(json.dumps({"before": pb, "after": pa, "type_accuracy": type_acc,
                                                     "before_rows": rb, "after_rows": ra}, indent=1, default=float))
print("\n".join(lines[:16]))
