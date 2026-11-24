"""Variations (v0.8.8): N slightly different edits of one clip in a preset, previewed cheaply, kept on demand.

Every variation gets its own hook (from the clip's own transcript, never repeated among the variations or your
last 50 clips) plus one or two of: a different trim, a different layout option within the preset, a different
caption/highlight/title colour, punch-in zoom on/off (an instant cut). Each starts from the preset, so the preset's
rules hold: fixed crops, hard cuts, static captions, no music.

Previews are low-res proxy renders (540x960, fast). Only "Keep" / "Export" makes a real clip (linked to the
original, scored, full render) in My Clips; "Discard the rest" deletes the other previews.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

from .. import db
from . import project, styles
from . import timeline as T

HIGHLIGHTS = [("3DFF5A", "green highlight"), ("FFE600", "yellow highlight"), ("FF4FD8", "pink highlight"),
              ("33D6FF", "blue highlight"), ("FF8A00", "orange highlight")]
TITLE_COLOURS = [("E53935", "red title"), ("7C4DFF", "purple title"), ("1E88E5", "blue title"),
                 ("00A86B", "green title"), ("111111", "black title")]
KNOBS = ["trim", "layout", "colour", "zoom"]
MAX = 10


def _hooks(edl: dict, clip: dict, n: int) -> list[str]:
    """n different hooks from this clip's own words (and moment), none in your last 50 clips."""
    from ..editing import hooks as H
    words = edl["captions"].get("words") or []
    a, b = edl["range"]
    ws = [w for w in words if a - 0.5 <= float(w["start"]) <= b + 0.5]
    transcript = " ".join(w["word"] for w in ws)
    streamer = (db.q1("SELECT display_name FROM streamers WHERE id=?", (clip.get("streamer_id"),)) or {}).get("display_name") \
        if clip.get("streamer_id") else None
    recent = H.recent_hooks()
    used = set(recent) | {H.norm(edl["hook"].get("text") or "")}
    cands = list(edl["hook"].get("options") or [])
    cands += H.transcript_hooks(transcript, ws, streamer or "they", edl["moment"].get("type"), edl["moment"].get("reason"))
    out = []
    for h in cands:
        if h and H.norm(h) and H.norm(h) not in used:
            out.append(h)
            used.add(H.norm(h))
        if len(out) == n:
            return out
    while len(out) < n:                                   # the clip has fewer distinct lines than variations
        h = H.pick_unique([(out or cands or ["Moment"])[0]], used)
        out.append(h)
        used.add(H.norm(h))
    return out


def _trim(e: dict, rng: random.Random) -> str | None:
    a, b = e["range"]
    src = e.get("source") or {}
    lo, hi = float(src.get("start", 0) or 0), float(src.get("end", b + 30) or b + 30)
    peak = e["moment"].get("peak")
    cut = min(2.0, (b - a) * 0.2)                         # never more than a fifth of the clip
    opts = []
    if b - a >= 5 and (peak is None or peak - (a + cut) > 1.5):
        opts.append(("tight", "Tighter start"))
    if a - lo >= 0.5:
        opts.append(("setup", "More setup"))
    if b - a >= 5 and (peak is None or (b - cut) - peak > 2.0):
        opts.append(("end", "Earlier end"))
    if not opts:
        return None
    kind, label = rng.choice(opts)
    if kind == "tight":
        e["range"] = [round(a + cut, 3), b]
    elif kind == "setup":
        e["range"] = [round(max(lo, a - 3.0), 3), b]
    else:
        e["range"] = [a, round(min(hi, b - cut), 3)]
    kfs = e["layout"].get("keyframes") or []
    if kfs:
        kfs[0]["t"] = min(kfs[0]["t"], e["range"][0])
    e["cuts"] = [c for c in e.get("cuts", []) if c[1] > e["range"][0] and c[0] < e["range"][1]]
    return label


def _layout(e: dict, style: dict, rng: random.Random) -> str | None:
    mode = style["layout"]["mode"]
    cam = e["layout"].get("facecam")
    if not cam:
        return None
    if mode == "corner":
        pip = dict(e["layout"].get("pip") or {})
        if rng.random() < 0.6:
            pip["corner"] = "tl" if pip.get("corner", "tr") == "tr" else "tr"
            pip["source"] = "variation"
            e["layout"]["pip"] = pip
            return f"facecam {'left' if pip['corner'] == 'tl' else 'right'}"
        s2 = copy.deepcopy(style)
        s2["layout"]["mode"] = "split"
        e.update(styles.apply(e, s2))
        e["style"] = {"key": style["key"], "name": style["name"]}
        return "split layout"
    if mode == "split":
        s2 = copy.deepcopy(style)
        s2["layout"]["mode"] = "corner"
        e.update(styles.apply(e, s2))
        e["style"] = {"key": style["key"], "name": style["name"]}
        return "corner layout"
    if mode == "full_reaction":
        pip = dict(e["layout"].get("pip") or {})
        pip["corner"] = "tl" if pip.get("corner", "tr") == "tr" else "tr"
        e["layout"]["pip"] = pip
        return f"gameplay {'left' if pip['corner'] == 'tl' else 'right'}"
    return None


def _colour(e: dict, style: dict, rng: random.Random) -> str:
    ov = e["captions"].setdefault("style_overrides", {})
    if style["title"]["box"] == "color_box" and rng.random() < 0.5:
        col, label = rng.choice([c for c in TITLE_COLOURS if c[0] != style["title"].get("color")])
        e["hook"]["color"] = col
        return label
    if not ov.get("highlight_on", True):
        ov["highlight_on"] = True
    col, label = rng.choice([c for c in HIGHLIGHTS if c[0] != (ov.get("highlight") or "").upper()])
    ov["highlight"] = col
    return label


def _zoom(e: dict) -> str | None:
    peak = e["moment"].get("peak")
    if e.get("zooms"):
        e["zooms"] = [z for z in e["zooms"] if not (z.get("auto") or z.get("from_style") or z.get("variation"))]
        return "no punch-in"
    if peak is None:
        return None
    e["zooms"] = [{"t": round(float(peak) - 0.1, 3), "kind": "punch", "amount": 0.12, "base_amount": 0.12,
                   "dur": 0.8, "variation": True}]            # instant cut in and back (render draws no animation)
    return "punch-in"


def plan(edl: dict, clip: dict, n: int, preset: str | None, seed: int = 0) -> list[dict]:
    """[{style_key, edl, label, changes}] for n variations."""
    n = max(1, min(MAX, int(n)))
    keys = styles.PRESET_KEYS
    hooks = _hooks(edl, clip, n)
    out = []
    for i in range(n):
        key = preset if preset and preset != "mix" else keys[i % len(keys)]
        st = styles.get(key)
        rng = random.Random(f"{clip['id']}:{seed}:{i}")
        e = styles.apply(copy.deepcopy(edl), st)
        e["music"]["enabled"] = False if not e["music"].get("manual") else e["music"]["enabled"]
        changes = []
        if not preset or preset == "mix":
            changes.append(st["name"])
        knobs = KNOBS[:]
        rng.shuffle(knobs)
        want, done = 1 + (i % 2), 0                       # one or two tweaks on top of the new hook
        for knob in knobs:
            if done >= want:
                break
            got = {"trim": lambda: _trim(e, rng), "layout": lambda: _layout(e, st, rng),
                   "colour": lambda: _colour(e, st, rng), "zoom": lambda: _zoom(e)}[knob]()
            if got:
                changes.append(got)
                done += 1
        e["hook"]["text"] = hooks[i]
        if st["title"]["box"] != "none":
            e["hook"].pop("hidden", None)
        changes.append("new hook")
        label = " · ".join(dict.fromkeys(c[0].upper() + c[1:] if j == 0 else c for j, c in enumerate(changes)))
        out.append({"style_key": key, "edl": e, "label": label, "changes": changes})
    return out


def make(clip_id: int, n: int = 4, preset: str | None = None) -> list[dict]:
    """Create the variations (previews queued). Earlier unkept previews of this clip are replaced."""
    from ..jobs import storage
    from ..jobs.engine import enqueue
    p = project.get(clip_id)
    if not p:
        raise KeyError(clip_id)
    clip = db.q1("SELECT * FROM clips WHERE id=?", (clip_id,))
    discard(clip_id)
    batch = (db.q1("SELECT COALESCE(MAX(batch), 0) + 1 b FROM variations WHERE clip_id=?", (clip_id,)) or {"b": 1})["b"]
    made = []
    for i, v in enumerate(plan(p["edl"], clip, n, preset, seed=batch)):
        vid = db.ex("""INSERT INTO variations(clip_id, batch, n, style_key, edl, label, changes, hook, status)
                       VALUES (?,?,?,?,?,?,?,?, 'preview')""",
                    (clip_id, batch, i + 1, v["style_key"], json.dumps(v["edl"]), v["label"], json.dumps(v["changes"]),
                     v["edl"]["hook"]["text"]))
        out = storage.media_dir() / "edit_cache" / f"c{clip_id}" / "variations" / f"v{vid}.mp4"
        jid = enqueue("render_variation", {"variation_id": vid, "out": str(out)}, clip.get("project_id"), priority=0)
        db.ex("UPDATE variations SET preview_job=? WHERE id=?", (jid, vid))
        made.append(get(vid))
    return made


def render_preview(vid: int, out: Path) -> str:
    """The cheap preview: low-res proxy render of the variation (no full render until it's kept)."""
    from ..jobs import storage
    from . import compile as ec
    v = db.q1("SELECT * FROM variations WHERE id=?", (vid,))
    if not v or v["status"] != "preview":
        return ""
    res = ec.render(json.loads(v["edl"]), out, storage.media_dir() / "edit_cache" / f"c{v['clip_id']}", "proxy",
                    music=False)
    db.ex("UPDATE variations SET preview_path=?, thumb_path=? WHERE id=?", (res["path"], res.get("thumb"), vid))
    return res["path"]


def get(vid: int) -> dict:
    v = db.q1("SELECT * FROM variations WHERE id=?", (vid,))
    if not v:
        raise KeyError(vid)
    v["changes"] = json.loads(v["changes"] or "[]")
    v.pop("edl", None)
    return v


def list_for(clip_id: int) -> list[dict]:
    rows = db.q("SELECT id FROM variations WHERE clip_id=? AND status IN ('preview','kept') ORDER BY batch DESC, n",
                (clip_id,))
    return [get(r["id"]) for r in rows]


def keep(vids: list[int], export: bool = False) -> list[dict]:
    """Make real clips (linked to the original, scored) and queue their full renders."""
    from ..jobs.engine import enqueue
    kept = []
    for vid in vids:
        v = db.q1("SELECT * FROM variations WHERE id=?", (vid,))
        if not v or v["status"] != "preview":
            continue
        c = db.q1("SELECT * FROM clips WHERE id=?", (v["clip_id"],))
        e = json.loads(v["edl"])
        root = c.get("variant_of") or c["id"]
        cols = ["project_id", "moment_id", "streamer_id", "game_id", "program_id", "description", "hashtags",
                "layout", "caption_style", "words", "caption_preset", "moment_type", "moment_reason", "source_path",
                "rules_applied", "caption_text", "needs_payout_confirm"]
        vals = [c.get(k) for k in cols]
        title = f"{(c['title'] or 'Clip').split(' [')[0]} [V{v['n']}]"
        cid = db.ex(f"""INSERT INTO clips({', '.join(cols)}, title, hook_text, review_status, variant_of, variant_label,
                        duration_sec) VALUES ({', '.join('?' * len(cols))}, ?, ?, 'approved', ?, ?, ?)""",
                    (*vals, title, e["hook"].get("text"), root, v["label"][:120], round(T.duration(e), 2)))
        project.create(cid, e, f"Variation: {v['label']}")          # also scores it (viral score, projected A$)
        jid = enqueue("export_edit", {"clip_id": cid, "preset": e["export"].get("preset") or "tiktok"},
                      c.get("project_id"), priority=1)
        db.ex("UPDATE variations SET status='kept', kept_clip_id=? WHERE id=?", (cid, vid))
        if v.get("preview_path"):
            Path(v["preview_path"]).unlink(missing_ok=True)
        sc = db.q1("SELECT virality, profit_likely FROM clips WHERE id=?", (cid,))
        kept.append({"variation_id": vid, "clip_id": cid, "job_id": jid, "virality": sc["virality"],
                     "projected_aud": sc["profit_likely"], "label": v["label"]})
    return kept


def discard(clip_id: int, keep_ids: list[int] | None = None) -> int:
    """Delete the previews that weren't kept (all of them, or all but keep_ids)."""
    n = 0
    for v in db.q("SELECT * FROM variations WHERE clip_id=? AND status='preview'", (clip_id,)):
        if keep_ids and v["id"] in keep_ids:
            continue
        for k in ("preview_path", "thumb_path"):
            if v.get(k):
                Path(v[k]).unlink(missing_ok=True)
        if v.get("preview_job"):
            db.ex("UPDATE jobs SET status='cancelled' WHERE id=? AND status IN ('queued','paused')", (v["preview_job"],))
        db.ex("UPDATE variations SET status='discarded' WHERE id=?", (v["id"],))
        n += 1
    return n
