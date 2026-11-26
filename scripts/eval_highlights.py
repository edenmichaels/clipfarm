"""Highlight evaluation: top-10 precision and recall, before vs after, on the labelled set.

Usage:
  python scripts/eval_highlights.py <eval dir> <report.md> [--before-backend <path to v0.7.1 backend>]
  python scripts/eval_highlights.py --picks <eval dir> <backend dir> <out.json> <ranking: score|viral>

Detectors compared on the same VODs:
- v0.3: loudness + chat rate (scorer.py, still in the tree)
- v0.7.1: the previous detector, run from its own checkout (CI: git worktree of e8aada9), ranked by its score
- v0.8: this build, ranked by the viral model score
A pick is correct if its peak falls inside a labelled funny/action/reaction moment (+-2 s) or its window overlaps
one by 8 s or more; each labelled moment counts once. "Traps" are the mundane/sad segments that must never be
picked: sad talk, calm driving, cutscenes, menus, loading, BRB, calm talking.
The LLM rating isn't used (no model in CI).
"""
import json
import subprocess
import sys
from pathlib import Path

TRAPS = {"sad", "driving", "cutscene", "menu", "loading", "brb", "talk"}


def picks_mode(d: Path, backend: Path, out: Path, ranking: str) -> None:
    sys.path.insert(0, str(backend))
    from clipfarm.highlights import run
    try:                                   # older versions still had video analysis (removed in 0.8.9)
        from clipfarm.highlights import visual
    except ImportError:
        visual = None
    res = []
    for i in (1, 2, 3):
        vod = d / f"vod{i}.mp4"
        lines = (d / f"chat{i}.jsonl").read_text().splitlines()
        msgs = [(m["t"], m["m"]) for m in map(json.loads, lines)] if lines else []
        kw = {"viral_threshold": 0} if ranking == "viral" else {}
        if visual is not None:
            vis = visual.analyse(vod)
            kw["visual_fn"] = lambda a, b, v=vis: {k: x[int(a):int(b)] for k, x in v.items()}
        ms, _ = run.detect_source(vod, chat=msgs, budget=12, min_len=15, max_len=60, threshold=0.0, **kw)
        for m in ms:
            res.append({"vod": f"vod{i}", "start": m.start, "end": m.end, "peak": m.peak,
                        "score": float(getattr(m, "viral", None) if ranking == "viral" and getattr(m, "viral", None)
                                       is not None else m.score),
                        "type": m.type, "reason": m.reason, "tags": [t["tag"] for t in getattr(m, "tags", [])]})
    out.write_text(json.dumps(res, default=float))


if sys.argv[1] == "--picks":
    picks_mode(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5])
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[1]
d, report = Path(sys.argv[1]), Path(sys.argv[2])
before_backend = Path(sys.argv[sys.argv.index("--before-backend") + 1]) if "--before-backend" in sys.argv else None
labels = json.loads((d / "labels.json").read_text())
n_pos = sum(len(v) for k, v in labels.items() if not k.endswith("_plan"))


def segment(vod: str, t: float) -> tuple[str, str]:
    acc = 0.0
    for k, dur, lab in labels[vod + "_plan"]:
        if acc <= t < acc + dur:
            return k, lab
        acc += dur
    return "?", "?"


def hit(m, labs):
    for j, lab in enumerate(labs):
        overlap = min(m["end"], lab["end"]) - max(m["start"], lab["start"])
        if lab["start"] - 2 <= m["peak"] <= lab["end"] + 2 or overlap >= 8:
            return j
    return None


def evaluate(picks):
    top = sorted(picks, key=lambda r: -r["score"])[:10]
    used, rows = set(), []
    for p in top:
        j = hit(p, labels.get(p["vod"], []))
        ok = j is not None and (p["vod"], j) not in used
        if ok:
            used.add((p["vod"], j))
        kind, lab = segment(p["vod"], (p["start"] + p["end"]) / 2)
        rows.append({**p, "correct": ok, "label": labels[p["vod"]][j]["type"] if j is not None else None,
                     "segment": kind, "trap": kind in TRAPS and not ok})
    prec = sum(r["correct"] for r in rows) / max(1, len(rows))
    return {"precision": prec, "recall": len(used) / max(1, n_pos), "traps": [r for r in rows if r["trap"]],
            "rows": rows}


# v0.3
sys.path.insert(0, str(ROOT / "backend"))
from clipfarm.highlights import audio_events, scorer, signals  # noqa: E402

v03 = []
for i in (1, 2, 3):
    vod = d / f"vod{i}.mp4"
    lines = (d / f"chat{i}.jsonl").read_text().splitlines()
    msgs = [(m["t"], m["m"]) for m in map(json.loads, lines)] if lines else []
    audio = signals.audio_rms(vod)
    rate, burst = signals.chat_series(msgs, len(audio))
    curve, normed = scorer.composite({"audio": audio, "chat": rate, "emote": burst})
    for m in scorer.pick_moments(curve, normed, min_len=15, max_len=60, threshold=0.0, top_n_per_hour=60, min_gap=30):
        v03.append({"vod": f"vod{i}", "start": m.start, "end": m.end, "peak": m.peak, "score": m.score, "type": "",
                    "reason": "", "tags": []})


def run_picks(backend: Path, ranking: str, name: str):
    out = report.parent / f"_picks_{name}.json"
    subprocess.run([sys.executable, __file__, "--picks", str(d), str(backend), str(out), ranking], check=True)
    return json.loads(out.read_text())


results = {"v0.3": evaluate(v03)}
if before_backend:
    results["v0.7.1"] = evaluate(run_picks(before_backend, "score", "v071"))
results["v0.8"] = evaluate(run_picks(ROOT / "backend", "viral", "v08"))
before_key = "v0.7.1" if "v0.7.1" in results else "v0.3"
a = results["v0.8"]
typed = [r for r in a["rows"] if r["correct"]]
type_acc = sum(r["type"] == r["label"] for r in typed) / max(1, len(typed))

lines = [
    "# Highlight detection: evaluation report", "",
    f"Labelled set: 3 synthetic VODs (~9 min each), {n_pos} labelled moments (funny, action **with a streamer "
    "reaction**, reactions). Real CC0 recordings for laughter, explosions, gunfire, cheering and screaming (see "
    "SOURCES.md); speech from espeak-ng; video, engine noise and chat generated.", "",
    "Traps that must never be picked: **sad talk with chat sending F / o7 / Sadge**, **calm driving**, "
    "**story cutscenes** (two voices + music, no reaction), menus, loading/BRB screens, calm talking. Also hard: "
    "the streamer shouting at chat, loud music with chat vibing, sub trains, and **explosions nobody reacts to** "
    "(not labelled as hits: spectacle goes viral mainly with a reaction).", "",
    f"Sound-event model: {'YAMNet (ONNX)' if audio_events.available() else 'NOT AVAILABLE'}. LLM rating: not used "
    "(no model in CI). Viral model: the default (starting) weights; no real-data training in CI.", "",
    "| Detector | Top-10 precision | Moments found in top 10 (recall) | Traps in top 10 |", "|---|---|---|---|"]
for k, r in results.items():
    lines.append(f"| {k} | **{r['precision']:.0%}** | {r['recall']:.0%} | {len(r['traps'])} |")
lines += ["", f"Moment type correct on the v0.8 hits: {type_acc:.0%}", ""]
for k, r in results.items():
    lines += [f"## {k}: top 10", "", "| VOD | Window | Score | Type | Correct | What it was | Reason |",
              "|---|---|---|---|---|---|---|"]
    for row in r["rows"]:
        lines.append(f"| {row['vod']} | {row['start']:.0f}-{row['end']:.0f}s | {row['score']:.2f} | {row['type']} | "
                     f"{'yes' if row['correct'] else 'no'} | {row['label'] or row['segment']} | "
                     f"{(row.get('reason') or '').replace('|', '/')[:110]} |")
    lines.append("")
report.write_text("\n".join(lines) + "\n")
report.with_suffix(".json").write_text(json.dumps({
    "before": results[before_key]["precision"], "before_name": before_key, "after": a["precision"],
    "before_recall": results[before_key]["recall"], "after_recall": a["recall"],
    "after_traps": [{"vod": t["vod"], "segment": t["segment"], "start": t["start"]} for t in a["traps"]],
    "v03": results["v0.3"]["precision"], "type_accuracy": type_acc,
    "rows": {k: r["rows"] for k, r in results.items()}}, indent=1, default=float))
print("\n".join(lines))
