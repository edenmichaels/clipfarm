"""Soak test: keep editing + rendering for hours and check nothing leaks or slows down.

    python scripts/soak.py --minutes 600 --out soak.json      # the 6-12 h run, on your PC
    python scripts/soak.py --minutes 1                        # smoke (CI)

Each round, on a synthetic 1080p clip with a facecam:
  apply a random style -> random edits (cuts, speed, freeze, text, sticker, transition, colour, zoom, audio keys)
  -> preview still -> proxy render -> every 5th round a full export.
Recorded per round: wall time, process memory (RSS), open files/handles, child processes, ClipFarm temp folders.
Fails (exit 1) if memory grows by more than 150 MB after warm-up, handles or temp folders keep growing, a child
process is left behind, or renders get more than 2x slower than the first rounds.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def _rss_and_handles() -> tuple[float, int]:
    try:
        import psutil
        p = psutil.Process()
        h = p.num_handles() if os.name == "nt" else p.num_fds()
        return p.memory_info().rss / 2 ** 20, h
    except Exception:                       # noqa: BLE001
        return 0.0, 0


def _temp_dirs() -> int:
    from clipfarm.jobs.storage import TEMP_PREFIXES
    return sum(1 for d in Path(tempfile.gettempdir()).iterdir() if d.name.startswith(TEMP_PREFIXES))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=1.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    work = Path(tempfile.mkdtemp(prefix="clipfarm_soak_"))
    os.environ["CLIPFARM_DATA_DIR"] = str(work)
    from clipfarm import db, proc
    from clipfarm.edit import compile as ec, project, styles
    db.init_db(work / "t.db")
    db.set_setting("media_dir", str(work / "media"))
    src = work / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=40",
                    "-f", "lavfi", "-i", "color=c=0x303848:s=480x320:r=30:d=40", "-f", "lavfi", "-i", "sine=f=330:d=40",
                    "-filter_complex", "[0:v][1:v]overlay=1400:40[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    toks = "no way bro he actually did it what a legend that was insane".split()
    words = [{"word": toks[i % len(toks)], "start": 1 + i * 0.45, "end": 1.3 + i * 0.45} for i in range(70)]
    rounds, t_end, n = [], time.time() + a.minutes * 60, 0
    while time.time() < t_end or n < 3:
        n += 1
        st = rnd.choice(list(styles.BUILTINS))
        r0 = rnd.uniform(0, 10)
        e = project.blank({"path": str(src), "url": None, "start": 0, "end": 40, "width": 1920, "height": 1080,
                           "fps": 30, "has_audio": True}, words, r0, r0 + rnd.uniform(10, 25))
        cam = {"x": 0.729, "y": 0.037, "w": 0.25, "h": 0.296}
        e["layout"].update(facecam=cam, keyframes=[{"t": r0, "layout": "split", "cam": cam, "path": [[0, 0.5]]}])
        e["moment"]["peak"] = r0 + 5
        e["hook"]["text"] = "He did NOT see this"
        e["music"]["enabled"] = False
        e = styles.apply(e, st)
        e["cuts"] = e.get("cuts", []) + [[r0 + 2, r0 + 2.5]]
        e["speed"] = [{"a": r0 + 6, "b": r0 + 8, "speed": rnd.choice([0.5, 2.0])}]
        e["freezes"] = [{"t": r0 + 3, "dur": 0.5}]
        e["text_layers"] = [{"id": "x", "text": "SOAK", "start": 0.5, "end": 3, "x": 0.5, "y": 0.8, "size": 80, "box": "white_box"}]
        e["stickers"] = [{"id": "s", "emoji": "🔥", "start": 1, "end": 2, "x": 0.3, "y": 0.3, "size": 200, "rotate": 10}]
        e["transitions"] = [{"t": 4, "kind": rnd.choice(["fade", "flash", "zoom", "whip"]), "dur": 0.4}]
        e["color"] = {"saturation": rnd.uniform(0.6, 1.4), "warmth": rnd.uniform(-0.5, 0.5)}
        e["audio_keys"] = {"speech": [{"t": 0, "db": 0}, {"t": 2, "db": -6}]}
        cache = work / "cache" / str(n % 4)
        t0 = time.time()
        ec.render_frame(e, 2.0, work / f"f{n % 4}.png", cache)
        ec.render(e, work / f"p{n % 4}.mp4", cache, "proxy")
        if n % 5 == 0:
            ec.render(e, work / "export.mp4", cache, "export")
        dt = time.time() - t0
        rss, handles = _rss_and_handles()
        rounds.append({"round": n, "style": st, "sec": round(dt, 2), "rss_mb": round(rss, 1), "handles": handles,
                       "children": proc.running(), "temp_dirs": _temp_dirs()})
        print(json.dumps(rounds[-1]), flush=True)
    warm = rounds[min(2, len(rounds) - 1)]
    last = rounds[-1]
    first_secs = sorted(r["sec"] for r in rounds[:3])[len(rounds[:3]) // 2]
    tail_secs = sorted(r["sec"] for r in rounds[-3:])[len(rounds[-3:]) // 2]
    problems = []
    if warm["rss_mb"] and last["rss_mb"] - warm["rss_mb"] > 150:
        problems.append(f"memory grew {last['rss_mb'] - warm['rss_mb']:.0f} MB")
    if warm["handles"] and last["handles"] - warm["handles"] > 50:
        problems.append(f"open handles grew by {last['handles'] - warm['handles']}")
    if last["temp_dirs"] > warm["temp_dirs"] + 1:
        problems.append(f"{last['temp_dirs'] - warm['temp_dirs']} render temp folders left behind")
    if last["children"]:
        problems.append(f"{last['children']} child processes still running")
    if len(rounds) >= 6 and tail_secs > 2 * first_secs + 1:
        problems.append(f"renders slowed down: {first_secs:.1f}s -> {tail_secs:.1f}s")
    summary = {"rounds": len(rounds), "minutes": round(a.minutes, 1), "first_round_sec": first_secs,
               "last_round_sec": tail_secs, "rss_mb": [warm["rss_mb"], last["rss_mb"]],
               "handles": [warm["handles"], last["handles"]], "problems": problems, "log": rounds}
    if a.out:
        Path(a.out).write_text(json.dumps(summary, indent=1))
    print("SOAK", "OK" if not problems else "FAILED: " + "; ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
