"""Style references: your own screenshots of clips that are working, with notes and tags.

Nothing here is downloaded or scraped: you add the screenshots yourself (paste, drag or pick a file). Each one
keeps the date you added it, so the Styles tab can show how fresh your references are.

"Make a style from this" reads a screenshot and fills in a Style:
  * image analysis (always): where the facecam/gameplay seam is, where the captions sit, the caption highlight
    colour, how many words are on screen, whether the title sits in a white or black box;
  * a local vision model (optional, Settings › AI › vision model) adds what pixels can't tell, e.g. "meme".
The result is a normal Style you can tweak and save.
"""
from __future__ import annotations

import base64
import colorsys
import io
import json
import re
from pathlib import Path

import numpy as np

from .. import db
from . import styles

W, H = 270, 480          # analysis size (9:16)


def _dir() -> Path:
    from ..jobs import storage
    d = storage.media_dir() / "style_refs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- library -----------------------------------------------------------------------------------------
def add(image: bytes, notes: str = "", tags: list[str] | None = None, source_note: str = "") -> dict:
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(image))
        im.load()
    except Exception as e:                       # noqa: BLE001
        raise ValueError("That file isn't an image (PNG, JPG or WebP screenshots work).") from e
    if im.width * im.height > 40_000_000:
        raise ValueError("That image is too large.")
    rid = db.ex("INSERT INTO style_refs(notes, tags, source_note) VALUES (?,?,?)",
                (notes[:2000], json.dumps([t.strip()[:40] for t in (tags or []) if t.strip()][:20]), source_note[:300]))
    path = _dir() / f"ref_{rid}.png"
    im.convert("RGB").save(path, "PNG")
    db.ex("UPDATE style_refs SET image_path=? WHERE id=?", (str(path), rid))
    styles.touch()
    return get(rid)


def get(rid: int) -> dict | None:
    r = db.q1("SELECT * FROM style_refs WHERE id=?", (rid,))
    if not r:
        return None
    r["tags"] = json.loads(r.get("tags") or "[]")
    r["analysis"] = json.loads(r["analysis"]) if r.get("analysis") else None
    return r


def all_refs() -> list[dict]:
    return [get(r["id"]) for r in db.q("SELECT id FROM style_refs ORDER BY created_at DESC, id DESC")]


def update(rid: int, notes: str | None = None, tags: list[str] | None = None) -> dict | None:
    if notes is not None:
        db.ex("UPDATE style_refs SET notes=? WHERE id=?", (notes[:2000], rid))
    if tags is not None:
        db.ex("UPDATE style_refs SET tags=? WHERE id=?", (json.dumps([t.strip()[:40] for t in tags if t.strip()][:20]), rid))
    return get(rid)


def delete(rid: int) -> None:
    r = get(rid)
    if r and r.get("image_path"):
        Path(r["image_path"]).unlink(missing_ok=True)
    db.ex("DELETE FROM style_refs WHERE id=?", (rid,))


def tag_counts() -> dict[str, int]:
    """Tags across your references (the viral model reads these too: what you collect is what you like)."""
    out: dict[str, int] = {}
    for r in db.q("SELECT tags FROM style_refs"):
        for t in json.loads(r["tags"] or "[]"):
            out[t.lower()] = out.get(t.lower(), 0) + 1
    return out


# ---- image analysis --------------------------------------------------------------------------------------
def _load(path_or_bytes) -> np.ndarray:
    from PIL import Image
    im = Image.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes)
    im = im.convert("RGB")
    # phone screenshots can have status bars; analyse the central 9:16 area
    w, h = im.size
    if w / h > 9 / 16 + 0.02:
        nw = int(h * 9 / 16)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    elif w / h < 9 / 16 - 0.02:
        nh = int(w * 16 / 9)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return np.asarray(im.resize((W, H)), dtype=np.float32)


def _seam(rgb: np.ndarray) -> tuple[float | None, float]:
    """(seam y as a fraction of the height, confidence). Looks for a horizontal edge across the whole width
    between 28 % and 52 % of the height: a dark separator line or a hard change of picture."""
    lum = rgb.mean(axis=2)
    best, conf = None, 0.0
    lo, hi = int(H * 0.28), int(H * 0.52)
    for y in range(lo, hi):
        row = lum[y]
        dark = float((row < 40).mean())
        a, b = rgb[max(0, y - 6):y - 1], rgb[y + 2:y + 7]
        if a.size == 0 or b.size == 0:
            continue
        diff = np.abs(a.mean(axis=0) - b.mean(axis=0)).mean(axis=1)          # per column
        across = float((diff > 22).mean())
        # inside a picture the row above/below look alike; at a seam most columns change at once
        score = max(dark if dark > 0.9 else 0.0, across)
        if score > conf:
            best, conf = y / H, score
    return (best, conf) if conf >= 0.6 else (None, conf)


def _text_mask(rgb: np.ndarray) -> np.ndarray:
    """Outlined caption text: bright (or saturated) pixels with a dark pixel within 3 px."""
    lum = rgb.mean(axis=2)
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    bright = (lum > 200) | ((mx > 200) & (mx - mn > 120))
    dark = lum < 45
    near = np.zeros_like(dark)
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            near |= np.roll(np.roll(dark, dy, 0), dx, 1)
    return bright & near


def _bands(mask: np.ndarray, min_rows: int = 5, min_px: int = 6) -> list[tuple[int, int]]:
    rows = mask.sum(axis=1) >= min_px
    out, start = [], None
    for y, on in enumerate(list(rows) + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start >= min_rows:
                out.append((start, y))
            start = None
    # join bands split by a thin gap (two caption lines)
    joined: list[list[int]] = []
    for a, b in out:
        if joined and a - joined[-1][1] <= 6:
            joined[-1][1] = b
        else:
            joined.append([a, b])
    return [(a, b) for a, b in joined]


def _boxes(rgb: np.ndarray) -> list[dict]:
    """Solid white or black rectangles that aren't the full width (title boxes)."""
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    out = []
    for kind, m in (("white_box", (lum > 235) & (sat < 25)), ("black_box", (lum < 25))):
        rows = []
        for y in range(H):
            r = m[y]
            # longest run of box pixels in the row
            best = run = 0
            for v in r:
                run = run + 1 if v else 0
                best = max(best, run)
            rows.append(0.25 * W <= best <= 0.97 * W)
        # runs of box rows; the text inside a box breaks the rows up, so runs a few rows apart are one box
        runs, y = [], 0
        while y < H:
            if rows[y]:
                s0 = y
                while y < H and rows[y]:
                    y += 1
                if runs and s0 - runs[-1][1] <= H * 0.06:
                    runs[-1][1] = y
                else:
                    runs.append([s0, y])
            y += 1
        for s0, y1 in runs:
            if 8 <= y1 - s0 <= H * 0.25:
                inner = rgb[s0:y1]
                ink = (inner.mean(axis=2) < 90) if kind == "white_box" else (inner.mean(axis=2) > 200)
                if ink.mean() > 0.01:              # there's text in it
                    out.append({"box": kind, "y": (s0 + y1) / 2 / H, "h": (y1 - s0) / H})
    return out


COLOURS = [("FFE600", 50), ("3DFF5A", 125), ("5CE1FF", 190), ("FF3B3B", 0), ("FF4FD8", 310), ("FF9A1F", 30)]


def _highlight(rgb: np.ndarray, band: tuple[int, int]) -> str | None:
    seg = rgb[band[0]:band[1]].reshape(-1, 3) / 255.0
    mx, mn = seg.max(axis=1), seg.min(axis=1)
    pick = seg[(mx > 0.7) & (mx - mn > 0.45)]
    if len(pick) < 15:
        return None
    hues = np.array([colorsys.rgb_to_hsv(*p)[0] * 360 for p in pick[:4000]])
    h = float(np.median(hues))
    return min(COLOURS, key=lambda c: min(abs(c[1] - h), 360 - abs(c[1] - h)))[0]


def _word_count(mask: np.ndarray, band: tuple[int, int]) -> int:
    cols = mask[band[0]:band[1]].sum(axis=0) > 0
    gap_min = max(4, int((band[1] - band[0]) * 0.35))
    words, run_gap, inside = 0, 0, False
    for on in cols:
        if on:
            if not inside and (words == 0 or run_gap >= gap_min):
                words += 1
            inside, run_gap = True, 0
        else:
            run_gap += 1
            inside = False if run_gap >= gap_min else inside
    return max(1, min(3, words))


def _skin(rgb: np.ndarray) -> float:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    m = (r > 95) & (g > 40) & (b > 20) & (r > g) & (r > b) & (r - np.minimum(g, b) > 15) & (np.abs(r - g) > 15)
    return float(m[int(H * 0.15):int(H * 0.75), int(W * 0.2):int(W * 0.8)].mean())


def analyse_image(image) -> dict:
    """Detect the style elements in a 9:16 screenshot. Returns {"found": {...}, "confidence": {...}}."""
    rgb = _load(image)
    seam, seam_conf = _seam(rgb)
    boxes = _boxes(rgb)
    mask = _text_mask(rgb)
    # captions: outlined-text bands that aren't inside a title box
    bands = [b for b in _bands(mask) if not any(abs((b[0] + b[1]) / 2 / H - x["y"]) < x["h"] / 2 + 0.01 for x in boxes)]
    bands = [b for b in bands if (b[1] - b[0]) >= 8 and 0.08 < (b[0] + b[1]) / 2 / H < 0.92]
    found: dict = {"layout": "split" if seam else None, "seam_y": round(seam, 3) if seam else None}
    conf = {"layout": round(seam_conf, 2)}
    if not seam:
        skin = _skin(rgb)
        found["layout"] = "full_reaction" if skin > 0.18 else "gameplay"
        conf["layout"] = round(min(1.0, abs(skin - 0.18) * 4 + 0.3), 2)
    if bands:
        cap = max(bands, key=lambda b: mask[b[0]:b[1]].sum())
        cy = (cap[0] + cap[1]) / 2 / H
        if seam and abs(cy - seam) < 0.09:
            pos = "seam"
        elif cy < 0.40:
            pos = "upper"
        elif cy < 0.57:
            pos = "centre"
        else:
            pos = "lower"
        found.update(caption_y=round(cy, 3), caption_position=pos, words=_word_count(mask, cap),
                     highlight_color=_highlight(rgb, cap))
        conf["captions"] = round(min(1.0, mask[cap[0]:cap[1]].mean() * 12), 2)
    if boxes:
        top = min(boxes, key=lambda x: x["y"])
        found.update(title_box=top["box"], title_y=round(top["y"], 3),
                     title_position="seam" if seam and abs(top["y"] - seam) < 0.06 else "top")
        conf["title"] = 0.8
    else:
        found["title_box"] = None
    return {"found": found, "confidence": conf}


VISION_PROMPT = """This is a screenshot of a vertical short-form video (TikTok / YouTube Shorts) of a streamer clip.
Describe its editing style as JSON with exactly these keys:
{"layout": "split" | "full_reaction" | "gameplay",
 "caption_position": "seam" | "upper" | "centre" | "lower",
 "words_on_screen": 1 | 2 | 3,
 "highlight_color": "#RRGGBB" or null,
 "uppercase": true | false,
 "title_box": "white_box" | "black_box" | "outline" | "none",
 "energy": "meme" | "normal" | "clean"}
Only describe what you see. Answer with the JSON only."""


def vision(image_bytes: bytes, timeout: float = 90) -> dict | None:
    cfg = db.get_setting("llm", {}) or {}
    model = cfg.get("vision_model")
    if not model:
        return None
    import requests
    try:
        r = requests.post(f"{cfg.get('base_url', 'http://127.0.0.1:11434')}/api/chat", timeout=timeout, json={
            "model": model, "stream": False, "format": "json",
            "messages": [{"role": "user", "content": VISION_PROMPT, "images": [base64.b64encode(image_bytes).decode()]}]})
        r.raise_for_status()
        return json.loads(re.search(r"\{.*\}", r.json()["message"]["content"], re.S).group(0))
    except Exception:                        # noqa: BLE001  (optional; no model, timeout, bad JSON)
        return None


def style_from(found: dict, llm: dict | None = None, name: str = "From screenshot") -> dict:
    """Turn detections into a Style (starting from the closest built-in)."""
    llm = llm or {}
    lay = found.get("layout") or llm.get("layout") or "gameplay"
    if llm.get("layout") in ("full_reaction", "gameplay") and not found.get("seam_y"):
        lay = llm["layout"]
    energy = llm.get("energy")
    base = {"split": "streamer_split", "full_reaction": "full_reaction", "gameplay": "gameplay_only"}[lay]
    if energy == "meme" or found.get("words") == 1 and found.get("caption_position") == "centre":
        base = "meme_high_energy"
    elif energy == "clean":
        base = "clean"
    s = styles.get(base)
    s["base"] = base
    s["name"] = name
    if lay == "split":
        s["layout"]["mode"] = "split"
        if found.get("seam_y"):
            s["layout"]["cam_ratio"] = round(found["seam_y"], 3)
    elif lay in ("full_reaction", "gameplay"):
        s["layout"]["mode"] = lay
    # (where captions sit isn't copied: every style uses the one fixed bottom place since v0.8.5)
    words = found.get("words") or llm.get("words_on_screen")
    if words in (1, 2, 3):
        s["captions"]["words"] = int(words)
    col = found.get("highlight_color") or (llm.get("highlight_color") or "").lstrip("#").upper() or None
    if "highlight_color" in found or llm.get("highlight_color", "x") is None:
        s["captions"]["highlight"] = bool(col)
    if col:
        s["captions"]["highlight_color"] = col
    if isinstance(llm.get("uppercase"), bool):
        s["captions"]["uppercase"] = llm["uppercase"]
    box = found.get("title_box") or (llm.get("title_box") if llm.get("title_box") in styles.TITLE_BOXES else None)
    if box:
        s["title"]["box"] = box
    elif "title_box" in found and not llm:
        s["title"]["box"] = "outline"
    return styles.clean(s, base)


def make_style(image: bytes, name: str = "From screenshot") -> dict:
    """'Make a style from this'."""
    det = analyse_image(image)
    llm = vision(image)
    style = style_from(det["found"], llm, name)
    return {"style": style, "found": det["found"], "confidence": det["confidence"],
            "method": "image + vision model" if llm else "image analysis", "vision": llm}


def make_style_for_ref(rid: int, name: str | None = None) -> dict:
    r = get(rid)
    if not r or not r.get("image_path") or not Path(r["image_path"]).exists():
        raise KeyError(rid)
    res = make_style(Path(r["image_path"]).read_bytes(), name or f"Style from reference {rid}")
    db.ex("UPDATE style_refs SET analysis=? WHERE id=?", (json.dumps({k: res[k] for k in ("found", "confidence", "method")}), rid))
    return res
