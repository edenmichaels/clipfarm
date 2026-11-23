"""Before/after sample clips for v0.8.5 (framing, facecam, captions), from the same synthetic sources.

    python scripts/v085_samples.py <backend dir> <out dir>       # run once with v0.8.4's backend, once with this one

Three 12-second sources (made here, nothing downloaded):
  a  facecam + talking: calm gameplay, a webcam box top-right, the streamer talking most of the time
  b  gameplay action:   a bright object racing across busy gameplay, a webcam box, short shouts
  c  no facecam:        the same action, no webcam
Each goes through the app's own auto-edit (reframe, style, captions, title) and export render.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def make_sources(d: Path) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    game_calm = "testsrc2=s=1920x1080:r=30:d=12"
    game_busy = ("testsrc2=s=1920x1080:r=30:d=12[g0];color=c=white:s=160x160:r=30:d=12[o];"
                 "[g0][o]overlay=x='200+t*130':y='420+80*sin(t*2)'")
    cam = "mandelbrot=s=480x320:r=30"
    for key, game, with_cam in (("a", game_calm, True), ("b", game_busy, True), ("c", game_busy, False)):
        p = d / f"{key}.mp4"
        if p.exists():
            out[key] = p
            continue
        fg = f"{game}[g]"
        graph = (fg + (f";{cam}[c];[g][c]overlay=1400:40:shortest=1[v]" if with_cam else ";[g]null[v]"))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=12",
                        "-filter_complex", graph, "-map", "[v]", "-map", "0:a", "-t", "12",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", str(p)], check=True)
        out[key] = p
    return out


TALK = ("so I was telling chat about this guy yesterday and he just walked straight off the bridge "
        "and the cops did not even notice anyway look at this").split()
SHOUT = "no way no way bro what was that he actually did it".split()


def words_for(key: str) -> list[dict]:
    if key == "a":
        out, t = [], 1.0
        for i, w in enumerate(TALK):
            out.append({"word": w + ("." if i in (14, 23) else ""), "start": round(t, 2), "end": round(t + 0.28, 2)})
            t += 0.36 + (0.5 if i in (14, 23) else 0)
        return out
    out, t = [], 3.0
    for w in SHOUT:
        out.append({"word": w, "start": round(t, 2), "end": round(t + 0.25, 2)})
        t += 0.42
    return out


def main(backend: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="v085s_"))
    os.environ["CLIPFARM_DATA_DIR"] = str(work)
    sys.path.insert(0, str(backend))
    from clipfarm import db
    from clipfarm.edit import build, compile as ec
    db.init_db(work / "t.db")
    db.set_setting("media_dir", str(work / "media"))
    db.set_setting("transcription", {"enabled": False})
    new = hasattr(ec, "_has_title")
    srcs = make_sources(out.parent / "_sources")
    cam = {"x": 1400 / 1920, "y": 40 / 1080, "w": 480 / 1920, "h": 320 / 1080}
    for key, src in srcs.items():
        sid = db.ex("INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status) "
                    "VALUES ('twitch', ?, ?, 'Sample', 'allowed')", (key, f"s{key}"))
        if key != "c":
            db.set_setting(f"facecam:{sid}", {**cam, "by": "you"} if new else cam)
        e = build.auto_edit(media=src, start=0.0, end=12.0, words=words_for(key),
                            streamer={"id": sid, "display_name": "Sample", "login": f"s{key}"}, program=None,
                            req={"tags": [], "onscreen": []}, settings={}, moment={"type": "Funny", "viral": 80},
                            hook="He did NOT see this coming", credit=None, account_ids=[], payoff=6.0,
                            category="Grand Theft Auto VI")
        e["music"]["enabled"] = False
        res = ec.render(e, out / f"{key}.mp4", work / "cache" / key, "export")
        (out / f"{key}.json").write_text(json.dumps({"layouts": [(k["t"], k["layout"], k["path"]) for k in
                                                                  e["layout"]["keyframes"]],
                                                     "zooms": e.get("zooms"), "style": e.get("style")}, indent=1))
        print(key, res["path"])


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
