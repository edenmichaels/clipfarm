"""Trending Styles: whole-video looks (layout + captions + title + pacing + effects + audio) in one preset.

A style is a plain dict. The five built-ins follow what's working in short-form gaming/streamer clips right
now (from Eden's own style references, see `style_refs`; nothing is scraped):

  Streamer Split   facecam on top (35-45 % of the height), gameplay below, a clean seam, captions ON the
                   seam (1-3 words, active word yellow), a white-box title from the first frame.
  Full Reaction    the streamer fills the screen (IRL / Just Chatting / big reactions).
  Gameplay Only    no facecam; captions in the lower third, outline title.
  Meme / High Energy  big 1-word captions, hard punch zooms, shakes and flashes, SFX hits.
  Clean            minimal captions, no title box, gentle zoom, nothing flashy.

Which style a clip gets: the clip's own choice -> campaign default -> account default -> streamer default
-> automatic (facecam found -> Streamer Split; IRL / Just Chatting -> Full Reaction; else Gameplay Only).
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

from .. import db

H = 1920
CAPTION_POSITIONS = ["seam", "upper", "centre", "lower"]
TITLE_BOXES = ["white_box", "black_box", "outline", "none"]
LAYOUTS = ["split", "full_reaction", "gameplay", "auto"]

# Every field the editor can tweak, with its allowed range. `clean()` enforces these.
FIELDS = {
    "layout.mode": LAYOUTS,
    "layout.cam_ratio": (0.35, 0.45),
    "layout.seam_line": bool,
    "captions.template": str,
    "captions.position": CAPTION_POSITIONS,
    "captions.words": (1, 3),
    "captions.highlight": bool,
    "captions.highlight_color": str,
    "captions.uppercase": bool,
    "title.box": TITLE_BOXES,
    "title.whole": bool,
    "title.seconds": (1.0, 8.0),
    "title.position": ["top", "seam"],
    "pacing.zoom": (0.0, 1.0),
    "pacing.cut_silence": bool,
    "pacing.max_gap": (0.2, 1.5),
    "effects.flash": bool,
    "effects.shake": bool,
    "effects.sfx": bool,
    "effects.emoji": bool,
    "audio.music": bool,
    "audio.music_db": (-12.0, 6.0),
}


def _style(key, name, desc, layout, captions, title, pacing, effects, audio) -> dict:
    return {"key": key, "name": name, "builtin": True, "description": desc, "layout": layout,
            "captions": captions, "title": title, "pacing": pacing, "effects": effects, "audio": audio}


BUILTINS: dict[str, dict] = {s["key"]: s for s in [
    _style("streamer_split", "Streamer Split",
           "Facecam on top, gameplay below, captions on the seam with the spoken word in yellow, white-box title.",
           {"mode": "split", "cam_ratio": 0.40, "seam_line": True},
           {"template": "bold_pop", "position": "seam", "words": 2, "highlight": True, "highlight_color": "FFE600",
            "uppercase": True},
           {"box": "white_box", "whole": True, "seconds": 4.0, "position": "top"},
           {"zoom": 0.5, "cut_silence": True, "max_gap": 0.45},
           {"flash": False, "shake": False, "sfx": False, "emoji": False},
           {"music": True, "music_db": -3.0}),
    _style("full_reaction", "Full Reaction",
           "The streamer fills the screen: IRL, Just Chatting and big reactions. Captions in the lower third.",
           {"mode": "full_reaction", "cam_ratio": 0.40, "seam_line": False},
           {"template": "bold_pop", "position": "lower", "words": 2, "highlight": True, "highlight_color": "FFE600",
            "uppercase": True},
           {"box": "white_box", "whole": False, "seconds": 4.0, "position": "top"},
           {"zoom": 0.6, "cut_silence": True, "max_gap": 0.45},
           {"flash": False, "shake": False, "sfx": False, "emoji": True},
           {"music": True, "music_db": -3.0}),
    _style("gameplay_only", "Gameplay Only",
           "No facecam: gameplay fills the screen, captions in the lower third, outline title.",
           {"mode": "gameplay", "cam_ratio": 0.40, "seam_line": False},
           {"template": "bold_pop", "position": "lower", "words": 3, "highlight": True, "highlight_color": "FFE600",
            "uppercase": True},
           {"box": "outline", "whole": False, "seconds": 4.0, "position": "top"},
           {"zoom": 0.4, "cut_silence": True, "max_gap": 0.6},
           {"flash": False, "shake": False, "sfx": False, "emoji": False},
           {"music": True, "music_db": 0.0}),
    _style("meme_high_energy", "Meme / High Energy",
           "One word at a time, hard punch zooms, shakes, flashes and SFX hits. For chaos and fails.",
           {"mode": "auto", "cam_ratio": 0.42, "seam_line": True},
           {"template": "hype_yellow", "position": "centre", "words": 1, "highlight": True, "highlight_color": "3DFF5A",
            "uppercase": True},
           {"box": "black_box", "whole": False, "seconds": 3.0, "position": "top"},
           {"zoom": 1.0, "cut_silence": True, "max_gap": 0.3},
           {"flash": True, "shake": True, "sfx": True, "emoji": True},
           {"music": True, "music_db": 0.0}),
    _style("clean", "Clean",
           "Minimal captions without a highlight, no title box, gentle zoom, nothing flashy.",
           {"mode": "auto", "cam_ratio": 0.38, "seam_line": False},
           {"template": "minimal", "position": "lower", "words": 3, "highlight": False, "highlight_color": "FFFFFF",
            "uppercase": False},
           {"box": "none", "whole": False, "seconds": 3.0, "position": "top"},
           {"zoom": 0.2, "cut_silence": False, "max_gap": 0.8},
           {"flash": False, "shake": False, "sfx": False, "emoji": False},
           {"music": True, "music_db": -6.0}),
]}
DEFAULT = "streamer_split"


# ---- validation --------------------------------------------------------------------------------------
def clean(style: dict, base: str | None = None) -> dict:
    """Any (partial, user-edited) style -> a complete valid style. Unknown / out-of-range values fall back."""
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
    return "streamer_split" if has_facecam else "gameplay_only"


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

    # layout
    cam = e["layout"].get("facecam")
    e["layout"]["cam_h"] = cam_px(lay["cam_ratio"])
    e["layout"]["seam_line"] = bool(lay["seam_line"])
    kfs = e["layout"].get("keyframes") or [{"t": e["range"][0], "layout": "blur_fill", "cam": None, "path": [[0, 0.5]]}]
    if lay["mode"] != "auto" and not e["layout"].get("locked"):
        new = []
        for k in kfs:
            k = dict(k)
            if lay["mode"] == "split":
                if cam:
                    k.update(layout="split", cam=cam)
            elif lay["mode"] == "full_reaction":
                k["layout"] = "cam_full" if cam else "crop"
                k["cam"] = cam
            elif lay["mode"] == "gameplay":
                if k["layout"] in ("split", "pip", "cam_full"):
                    k["layout"] = "crop"
            new.append(k)
        kfs = new
    kfs, _ = reframe.enforce_rules(kfs, e["rules"].get("onscreen") or [], cam)
    e["layout"]["keyframes"] = kfs

    # captions
    c = e["captions"]
    c["template"] = cap["template"]
    c["position"] = cap["position"]
    c["words_per_chunk"] = int(cap["words"])
    c["style_overrides"] = {"highlight_on": cap["highlight"], "highlight": cap["highlight_color"],
                            "uppercase": cap["uppercase"], "max_words": int(cap["words"])}

    # title / hook
    h = e["hook"]
    h["box"] = ttl["box"]
    h["whole"] = bool(ttl["whole"])
    h["sec"] = float(ttl["seconds"])
    h["position"] = ttl["position"]
    if ttl["box"] == "none":
        h["hidden"] = True
    else:
        h.pop("hidden", None)

    peak = e["moment"].get("peak")
    # pacing: zoom strength scales the punch-ins; meme style adds a punch on every big tag
    strength = float(pace["zoom"])
    zs = []
    for z in e.get("zooms", []):
        base_amt = float(z.get("base_amount", z.get("amount", 0.12)))
        if strength <= 0.01:
            continue
        zs.append({**z, "base_amount": base_amt, "amount": round(max(0.03, base_amt * (0.5 + strength)), 3)})
    if strength >= 0.9:
        have = {round(float(z["t"]), 1) for z in zs}
        for tg in (e["moment"].get("tags") or [])[:6]:
            t = float(tg.get("t", -1)) if isinstance(tg, dict) else -1
            if t >= 0 and round(t, 1) not in have and e["range"][0] <= t <= e["range"][1]:
                zs.append({"t": round(t - 0.1, 3), "kind": "punch", "amount": 0.2, "base_amount": 0.14, "dur": 0.5})
    e["zooms"] = sorted(zs, key=lambda z: z["t"])
    e.setdefault("pacing", {}).update({"cut_silence": bool(pace["cut_silence"]), "max_gap": float(pace["max_gap"])})
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

    # effects: flashes / shakes on the payoff (removed again when the style turns them off)
    e["overlays"] = [o for o in e.get("overlays", []) if not o.get("from_style")]
    if peak is not None:
        if fx["flash"]:
            e["overlays"].append({"kind": "flash", "t": round(peak, 3), "dur": 0.12, "from_style": True})
        if fx["shake"]:
            e["overlays"].append({"kind": "shake", "t": round(peak, 3), "dur": 0.4, "from_style": True})
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
