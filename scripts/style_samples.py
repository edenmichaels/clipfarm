"""Before/after sample sheet for the Trending Styles: the v0.8.1 default look vs each built-in style.

    python scripts/style_samples.py out.png
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main(out: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="styles_"))
    os.environ["CLIPFARM_DATA_DIR"] = str(tmp)
    from PIL import Image, ImageDraw, ImageFont
    from clipfarm import db
    from clipfarm.edit import compile as ec, project, styles
    db.init_db(tmp / "t.db")
    db.set_setting("media_dir", str(tmp / "media"))
    src = tmp / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=10",
                    "-f", "lavfi", "-i", "mandelbrot=s=480x320:r=30", "-f", "lavfi", "-i", "sine=f=330:d=10",
                    "-filter_complex", "[0:v][1:v]overlay=1400:40:shortest=1[v]", "-map", "[v]", "-map", "2:a",
                    "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    toks = "no way bro he actually did it what a legend".split()
    words = [{"word": toks[i % len(toks)], "start": 1.3 + i * 0.42, "end": 1.6 + i * 0.42} for i in range(16)]
    e = project.blank({"path": str(src), "url": None, "start": 0, "end": 10, "width": 1920, "height": 1080, "fps": 30,
                       "has_audio": True}, words, 1.0, 8.0)
    cam = {"x": 0.729, "y": 0.037, "w": 0.25, "h": 0.296}
    e["layout"].update(facecam=cam, keyframes=[{"t": 1.0, "layout": "split", "cam": cam, "path": [[0, 0.5]]}])
    e["hook"]["text"] = "He did NOT see this coming"
    e["music"]["enabled"] = False
    e["moment"]["peak"] = 4.0
    shots = [("Before (v0.8.1 default)", e)] + [(s["name"], styles.apply(e, k)) for k, s in styles.BUILTINS.items()]
    tiles = []
    for i, (name, edl) in enumerate(shots):
        p = tmp / f"s{i}.png"
        ec.render_frame(edl, 2.2, p, tmp / "cache")
        im = Image.open(p).convert("RGB").resize((360, 640))
        canvas = Image.new("RGB", (360, 690), (24, 26, 32))
        canvas.paste(im, (0, 50))
        d = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        except OSError:
            font = ImageFont.load_default()
        d.text((12, 12), name, fill=(240, 240, 240), font=font)
        tiles.append(canvas)
    sheet = Image.new("RGB", (len(tiles) * 370 - 10, 690), (12, 13, 16))
    for i, t in enumerate(tiles):
        sheet.paste(t, (i * 370, 0))
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "styles-before-after.png"))
