"""Trending Styles: whole-video looks (layout + captions + title + pacing + effects + audio) in one preset.

v0.8.5 rules for every style: nothing pans, slides, shakes or animates; each shot is one fixed crop and layout
changes are instant hard cuts; captions are the standard static bottom captions (Montserrat, uppercase, white
with a black outline, the spoken word coloured); the title sits at the top, fixed, no animation; automatic zooms
are off (switch on "zoom cuts" and they're instant cuts to a centred zoom and back).

  Streamer Corner  gameplay fills the frame, the facecam in a top corner; cuts to the streamer when they're the
                   moment                                                   <- the default when there's a facecam
  Streamer Split   facecam on top (35-45 % of the height), gameplay below
  Full Reaction    the streamer fills the screen (IRL / Just Chatting / big reactions)
  Gameplay Only    no facecam
  Meme / High Energy  one word at a time, flash and sound effects on the payoff, reaction emojis
  Clean            no highlight, no title, no effects

Which style a clip gets: the clip's own choice -> campaign default -> account default -> streamer default
-> automatic (facecam -> Streamer Corner; IRL / Just Chatting -> Full Reaction; else Gameplay Only).
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

from .. import db

H = 1920
TITLE_BOXES = ["white_box", "black_box", "outline", "none"]
LAYOUTS = ["corner", "split", "full_reaction", "gameplay", "auto"]

# Every field the editor can tweak, with its allowed range. `clean()` enforces these.
FIELDS = {
    "layout.mode": LAYOUTS,
    "layout.cam_ratio": (0.35, 0.45),
    "layout.pip_w": (0.30, 0.38),
    "layout.focus": bool,
    "layout.seam_line": bool,
    "captions.template": str,
    "captions.words": (1, 6),
    "captions.highlight": bool,
    "captions.highlight_color": str,
    "captions.uppercase": bool,
    "title.box": TITLE_BOXES,
    "title.whole": bool,
    "title.seconds": (1.0, 8.0),
    "pacing.zoom_cuts": bool,
    "pacing.zoom": (0.0, 1.0),
    "pacing.cut_silence": bool,
    "pacing.max_gap": (0.2, 1.5),
    "effects.flash": bool,
    "effects.sfx": bool,
    "effects.emoji": bool,
    "audio.music": bool,
    "audio.music_db": (-12.0, 6.0),
}


def _style(key, name, desc, layout, captions, title, pacing, effects, audio) -> dict:
    return {"key": key, "name": name, "builtin": True, "description": desc, "layout": layout,
            "captions": captions, "title": title, "pacing": pacing, "effects": effects, "audio": audio}


_CAP = {"template": "bold_pop", "words": 3, "highlight": True, "highlight_color": "FFE600", "uppercase": True}
_PACE = {"zoom_cuts": False, "zoom": 0.4, "cut_silence": True, "max_gap": 0.45}
_FX = {"flash": False, "sfx": False, "emoji": False}

BUILTINS: dict[str, dict] = {s["key"]: s for s in [
    _style("streamer_corner", "Streamer Corner",
           "Gameplay fills the frame with the facecam in a top corner; cuts to the streamer when they're the moment.",
           {"mode": "corner", "cam_ratio": 0.40, "pip_w": 0.34, "focus": True, "seam_line": False},
           dict(_CAP), {"box": "white_box", "whole": True, "seconds": 4.0}, dict(_PACE), dict(_FX),
           {"music": True, "music_db": -3.0}),
    _style("streamer_split", "Streamer Split",
           "Facecam on top, gameplay below, static captions at the bottom, white-box title.",
           {"mode": "split", "cam_ratio": 0.40, "pip_w": 0.34, "focus": False, "seam_line": True},
           dict(_CAP), {"box": "white_box", "whole": True, "seconds": 4.0}, dict(_PACE), dict(_FX),
           {"music": True, "music_db": -3.0}),
    _style("full_reaction", "Full Reaction",
           "The streamer fills the screen: IRL, Just Chatting and big reactions.",
           {"mode": "full_reaction", "cam_ratio": 0.40, "pip_w": 0.34, "focus": True, "seam_line": False},
           dict(_CAP, words=2), {"box": "white_box", "whole": False, "seconds": 4.0}, dict(_PACE),
           dict(_FX, emoji=True), {"music": True, "music_db": -3.0}),
    _style("gameplay_only", "Gameplay Only",
           "No facecam: one fixed crop on the action, captions at the bottom, outline title.",
           {"mode": "gameplay", "cam_ratio": 0.40, "pip_w": 0.34, "focus": False, "seam_line": False},
           dict(_CAP), {"box": "outline", "whole": False, "seconds": 4.0}, dict(_PACE, max_gap=0.6), dict(_FX),
           {"music": True, "music_db": 0.0}),
    _style("meme_high_energy", "Meme / High Energy",
           "One word at a time, a flash and a sound effect on the payoff, reaction emojis. For chaos and fails.",
           {"mode": "auto", "cam_ratio": 0.42, "pip_w": 0.36, "focus": True, "seam_line": True},
           dict(_CAP, template="hype_yellow", words=1, highlight_color="3DFF5A"),
           {"box": "black_box", "whole": False, "seconds": 3.0}, dict(_PACE, max_gap=0.3),
           {"flash": True, "sfx": True, "emoji": True}, {"music": True, "music_db": 0.0}),
    _style("clean", "Clean",
           "Captions without a highlight, no title box, no effects.",
           {"mode": "auto", "cam_ratio": 0.38, "pip_w": 0.32, "focus": False, "seam_line": False},
           dict(_CAP, template="minimal", highlight=False, highlight_color="FFFFFF"),
           {"box": "none", "whole": False, "seconds": 3.0}, dict(_PACE, cut_silence=False, max_gap=0.8), dict(_FX),
           {"music": True, "music_db": -6.0}),
]}
DEFAULT = "streamer_corner"


# ---- validation --------------------------------------------------------------------------------------
def legacy_changes(style: dict) -> list[str]:
    """What a style saved before v0.8.5 used that's gone now (moving captions, pans, shakes, animated zooms)."""
    out = []
    if (style.get("captions") or {}).get("position") not in (None, "lower", "bottom"):
        out.append(f"captions {style['captions']['position']} -> static at the bottom")
    if (style.get("title") or {}).get("position") == "seam":
        out.append("title on the seam -> fixed at the top")
    if (style.get("effects") or {}).get("shake"):
        out.append("screen shake -> off")
    p = style.get("pacing") or {}
    if "zoom_cuts" not in p and float(p.get("zoom", 0) or 0) > 0.05:
        out.append("animated zooms -> off (zoom cuts can be switched on)")
    return out


def clean(style: dict, base: str | None = None) -> dict:
    """Any (partial, user-edited, possibly pre-v0.8.5) style -> a complete valid style. Out-of-range values fall
    back; fields that made things move (caption position, shake, animated zoom) are dropped."""
    start = copy.deepcopy(BUILTINS.get(base or style.get("base") or style.get("key") or "", BUILTINS[DEFAULT]))
    out = {"key": style.get("key") or start["key"], "name": str(style.get("name") or start["name"])[:60],
           "builtin": False, "description": str(style.get("description") or start.get("description", ""))[:300],
           "base": base or style.get("base") or start["key"]}
    for path, rule in FIELDS.items():
        grp, k = path.split(".")
        val = (style.get(grp) or {}).get(k, start[grp][k])
        default = start[grp][k]
        if rule is bool:
            val = bool(val)
        elif rule is str:
            val = str(val) if val else default
            if k.endswith("color"):
                val = val.lstrip("#").upper()
                val = val if len(val) == 6 and all(c in "0123456789ABCDEF" for c in val) else default
        elif isinstance(rule, list):
            val = val if val in rule else default
        else:
            lo, hi = rule
            try:
                val = type(lo)(val)
            except (TypeError, ValueError):
                val = default
            val = max(lo, min(hi, val))
        out.setdefault(grp, {})[k] = val
    return out


# ---- storage -----------------------------------------------------------------------------------------
def builtins() -> list[dict]:
    return [copy.deepcopy(s) for s in BUILTINS.values()]


def user() -> list[dict]:
    out = []
    for r in db.q("SELECT * FROM styles ORDER BY name"):
        s = clean(json.loads(r["data"]))
        s.update({"key": f"user:{r['id']}", "id": r["id"], "name": r["name"], "builtin": False,
                  "ref_id": r.get("ref_id"), "updated_at": r.get("updated_at")})
        out.append(s)
    return out


def all_styles() -> list[dict]:
    return builtins() + user()


def get(key: str | None) -> dict:
    if key and key.startswith("user:"):
        r = db.q1("SELECT * FROM styles WHERE id=?", (int(key.split(":")[1]),))
        if r:
            s = clean(json.loads(r["data"]))
            s.update({"key": key, "id": r["id"], "name": r["name"]})
            return s
    return copy.deepcopy(BUILTINS.get(key or "", BUILTINS[DEFAULT]))


def convert_saved(data: dict) -> tuple[dict, list[str]]:
    """A style saved before v0.8.5 -> the static version, and what changed (for the one-time notice)."""
    return clean(data), legacy_changes(data)


def save(name: str, style: dict, style_id: int | None = None, ref_id: int | None = None) -> dict:
    s = clean(style)
    s["name"] = name = (name or s["name"]).strip()[:60] or "My style"
    data = json.dumps(s)
    if style_id:
        db.ex("UPDATE styles SET name=?, data=?, updated_at=datetime('now') WHERE id=?", (name, data, style_id))
        sid = style_id
    else:
        db.ex("INSERT INTO styles(name, data, ref_id) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
              "data=excluded.data, updated_at=datetime('now')", (name, data, ref_id))
        sid = db.q1("SELECT id FROM styles WHERE name=?", (name,))["id"]
    touch()
    return get(f"user:{sid}")


def delete(style_id: int) -> None:
    db.ex("DELETE FROM styles WHERE id=?", (style_id,))
    d = defaults()
    for scope, key in list(d.items()):
        if key == f"user:{style_id}":
            d.pop(scope)
    db.set_setting("style_defaults", d)


def touch() -> None:
    """'Style last updated': shown in the Styles tab so you know how fresh your references are."""
    db.set_setting("style_last_updated", datetime.now(timezone.utc).isoformat(timespec="seconds"))


def last_updated() -> str | None:
    return db.get_setting("style_last_updated")


# ---- defaults ------------------------------------------------------------------------------------------
def defaults() -> dict:
    return db.get_setting("style_defaults", {}) or {}


def set_default(scope: str, key: str | None) -> dict:
    """scope: 'global', 'streamer:<id>', 'account:<id>' or 'program:<id>'."""
    if not (scope == "global" or scope.split(":")[0] in ("streamer", "account", "program")):
        raise ValueError("scope must be global, streamer:<id>, account:<id> or program:<id>")
    d = defaults()
    if key:
        d[scope] = key
    else:
        d.pop(scope, None)
    db.set_setting("style_defaults", d)
    return d


def auto_key(has_facecam: bool, category: str | None = None) -> str:
    cat = (category or "").lower()
    if any(k in cat for k in ("just chatting", "irl", "talk shows", "asmr", "travel", "food")):
        return "full_reaction"
    return "streamer_corner" if has_facecam else "gameplay_only"


def default_for(*, program_id: int | None = None, account_ids: list[int] | None = None,
                streamer_id: int | None = None, has_facecam: bool = False, category: str | None = None) -> tuple[str, str]:
    """(style key, where it came from)."""
    d = defaults()
    if program_id and d.get(f"program:{program_id}"):
        return d[f"program:{program_id}"], "campaign"
    for a in account_ids or []:
        if d.get(f"account:{a}"):
            return d[f"account:{a}"], "account"
    if streamer_id and d.get(f"streamer:{streamer_id}"):
        return d[f"streamer:{streamer_id}"], "streamer"
    if d.get("global"):
        return d["global"], "global"
    return auto_key(has_facecam, category), "automatic"


# ---- applying a style to an edit project ------------------------------------------------------------
def cam_px(ratio: float) -> int:
    return int(round(max(0.35, min(0.45, ratio)) * H / 2)) * 2


def apply(edl: dict, style: dict | str) -> dict:
    """Return a new EDL with the style applied. Campaign rules (overlay / face on screen, no music) still win."""
    from . import reframe
    s = get(style) if isinstance(style, str) else clean(style)
    e = copy.deepcopy(edl)
    lay, cap, ttl, pace, fx, au = s["layout"], s["captions"], s["title"], s["pacing"], s["effects"], s["audio"]
    e["style"] = {"key": s["key"], "name": s["name"]}

    # layout: each shot keeps ONE fixed crop; only which layout is used changes (hard cuts)
    cam = e["layout"].get("facecam")
    e["layout"]["cam_h"] = cam_px(lay["cam_ratio"])
    e["layout"]["seam_line"] = bool(lay["seam_line"])
    pip = dict(e["layout"].get("pip") or {})
    if pip.get("source") != "yours":
        pip["w"] = float(lay["pip_w"])
    pip.setdefault("corner", "tr")
    e["layout"]["pip"] = pip
    kfs = e["layout"].get("keyframes") or [{"t": e["range"][0], "layout": "blur_fill", "cam": None, "path": [[0, 0.5]]}]
    cam_lays = ("split", "pip", "cam_full", "corner", "focus")
    if lay["mode"] != "auto" and not e["layout"].get("locked"):
        new = []
        for k in kfs:
            k = dict(k)
            if lay["mode"] == "corner":
                if cam and k["layout"] in ("split", "pip", "cam_full", "crop", "corner"):
                    k.update(layout="corner", cam=cam)
                elif cam and k["layout"] == "focus":
                    k.update(layout="focus" if lay["focus"] else "corner", cam=cam)
            elif lay["mode"] == "split":
                if cam and k["layout"] in cam_lays + ("crop",):
                    k.update(layout="split", cam=cam)
            elif lay["mode"] == "full_reaction":
                k["layout"] = "focus" if cam else "crop"
                k["cam"] = cam
            elif lay["mode"] == "gameplay":
                if k["layout"] in cam_lays:
                    k["layout"] = "crop"
            new.append(k)
        out = []
        for k in new:                          # neighbours with the same layout and crop: one shot
            if out and out[-1]["layout"] == k["layout"] and out[-1].get("path") == k.get("path"):
                continue
            out.append(k)
        kfs = out
    kfs, _ = reframe.enforce_rules(kfs, e["rules"].get("onscreen") or [], cam)
    e["layout"]["keyframes"] = kfs

    # captions
    c = e["captions"]
    c["template"] = cap["template"]
    c.pop("position", None)                   # captions have one fixed place (v0.8.5)
    c["words_per_chunk"] = int(cap["words"])
    c["style_overrides"] = {"highlight_on": cap["highlight"], "highlight": cap["highlight_color"],
                            "uppercase": cap["uppercase"], "max_words": int(cap["words"])}

    # title / hook
    h = e["hook"]
    h["box"] = ttl["box"]
    h["whole"] = bool(ttl["whole"])
    h["sec"] = float(ttl["seconds"])
    h.pop("position", None)                   # the title is fixed at the top
    if ttl["box"] == "none":
        h["hidden"] = True
    else:
        h.pop("hidden", None)

    peak = e["moment"].get("peak")
    # pacing: zoom cuts are OFF unless the style switches them on; then they're instant cuts (see render) and
    # the strength sets how far they go
    strength = float(pace["zoom"])
    # Settings › Editing › "Zoom cuts" switches them on for every style
    pace = {**pace, "zoom_cuts": bool(pace["zoom_cuts"]) or bool((db.get_setting("editing", {}) or {}).get("zoom_cuts"))}
    zs = []
    for z in e.get("zooms", []):
        if not pace["zoom_cuts"] and (z.get("auto") or z.get("from_style")):
            continue                          # automatic zooms go; ones you added by hand stay
        base_amt = float(z.get("base_amount", z.get("amount", 0.12)))
        if base_amt >= 1.0:                 # older projects stored a scale factor (1.16 = +16 %)
            base_amt -= 1.0
        amt = round(max(0.03, base_amt * (0.5 + strength)), 3) if pace["zoom_cuts"] else base_amt
        zs.append({**z, "base_amount": base_amt, "amount": amt})
    if pace["zoom_cuts"] and peak is not None and not any(abs(float(z["t"]) - peak) < 1 for z in zs):
        zs.append({"t": round(peak, 3), "kind": "punch", "amount": round(0.12 * (0.5 + strength), 3),
                   "base_amount": 0.12, "dur": 0.6, "from_style": True})
    e["zooms"] = sorted(zs, key=lambda z: z["t"])
    e.setdefault("pacing", {}).update({"cut_silence": bool(pace["cut_silence"]), "max_gap": float(pace["max_gap"]),
                                       "zoom_cuts": bool(pace["zoom_cuts"])})
    # pacing: tighten dead air between words -- but never the payoff or a tagged event (action has no words)
    from . import transcript
    old = [list(c) for c in e.get("style_cuts", [])]
    e["cuts"] = [c for c in e.get("cuts", []) if list(c) not in old]
    e["style_cuts"] = []
    if pace["cut_silence"]:
        keep = []
        if peak is not None:
            keep.append((peak - 2.0, peak + 2.5))
        for tg in e["moment"].get("tags") or []:
            if isinstance(tg, dict) and tg.get("t") is not None:
                keep.append((float(tg["t"]) - 1.0, float(tg["t"]) + 1.5))
        gaps = [g for g in transcript.silences(e, float(pace["max_gap"]))
                if not any(g[0] < b and g[1] > a for a, b in keep)]
        e["style_cuts"] = gaps
        e["cuts"] = sorted([*e["cuts"], *gaps])

    # effects: a flash on the payoff (removed again when the style turns it off). No style ever shakes.
    e["overlays"] = [o for o in e.get("overlays", []) if not o.get("from_style")
                     and not (o.get("kind") == "shake" and o.get("auto"))]
    if peak is not None and fx["flash"]:
        e["overlays"].append({"kind": "flash", "t": round(peak, 3), "dur": 0.12, "from_style": True})
    e.setdefault("style_fx", {}).update({"sfx": bool(fx["sfx"]), "emoji": bool(fx["emoji"])})
    c["emoji"] = bool(fx["emoji"])

    # audio
    m = e["music"]
    if not au["music"]:
        m["enabled"] = False
    elif m.get("path") and not e["rules"].get("no_music"):
        m["enabled"] = True
    m["volume_db"] = float(au["music_db"])
    return e
