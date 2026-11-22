"""Campaign rule checks on an edit project. Each flag carries a one-click fix the Editor can apply.

flag = {"id", "severity": "error"|"warn", "message", "fix": label or None}
"""
from __future__ import annotations

import copy

from ..edit import timeline as T
from ..edit.compile import PRESETS
from ..edit.reframe import enforce_rules

PRESET_PLATFORM = {"tiktok": "tiktok", "shorts": "youtube_shorts", "reels": "instagram_reels", "x": "x", "srt": "tiktok"}
PLATFORM_PRESET = {"tiktok": "tiktok", "youtube_shorts": "shorts", "instagram_reels": "reels", "x": "x"}


def _texts(edl: dict) -> list[tuple[str, dict]]:
    return [(k, v) for k, v in (edl.get("texts") or {}).items() if isinstance(v, dict)]


def check(edl: dict) -> list[dict]:
    r = edl["rules"]
    flags: list[dict] = []
    # overlay / streamer must stay in frame
    _, fixed = enforce_rules(edl["layout"].get("keyframes", []), r.get("onscreen") or [], edl["layout"].get("facecam"))
    if fixed:
        what = "streamer's overlay" if "streamer_overlay" in (r.get("onscreen") or []) else "streamer"
        flags.append({"id": "overlay", "severity": "error", "fix": "Use a layout that keeps it in frame",
                      "message": f"The campaign needs the {what} in frame, but the layout crops it out."})
    # no music / original audio only
    m = edl["music"]
    if r.get("no_music") and m.get("enabled") and m.get("path"):
        flags.append({"id": "music", "severity": "error", "fix": "Remove the music",
                      "message": "This campaign is original audio only, but the clip has music."})
    # allowed platforms
    preset = edl["export"].get("preset") or "tiktok"
    plats = r.get("platforms") or []
    if plats and PRESET_PLATFORM.get(preset) not in plats:
        flags.append({"id": "platform", "severity": "error",
                      "fix": f"Export for {PRESETS[PLATFORM_PRESET[plats[0]]]['label']}" if plats[0] in PLATFORM_PRESET else None,
                      "message": f"{PRESETS.get(preset, {}).get('label', preset)} isn't allowed for this campaign."})
    # length limit for the export preset
    dur = T.duration(edl)
    lim = PRESETS.get(preset, PRESETS["tiktok"])["max_sec"]
    if dur > lim:
        flags.append({"id": "length", "severity": "error", "fix": f"Trim to {lim} s",
                      "message": f"{dur:.0f} s is over the {lim} s limit for this platform."})
    # required hashtags and disclosure in post texts
    tags = [t.lower() for t in r.get("tags") or []]
    for plat, tx in _texts(edl):
        have = {("#" + h.lstrip("#")).lower() for h in tx.get("hashtags") or []}
        missing = [t for t in tags if t not in have]
        if missing:
            flags.append({"id": f"hashtags:{plat}", "severity": "error", "fix": "Add the campaign hashtags",
                          "message": f"{plat} post is missing {', '.join(missing)}."})
        if r.get("disclosure") and r["disclosure"].lower() not in (tx.get("description") or "").lower():
            flags.append({"id": f"disclosure:{plat}", "severity": "error", "fix": "Add the disclosure",
                          "message": f"{plat} post is missing the disclosure \"{r['disclosure']}\"."})
    # credit
    if r.get("credit") and edl.get("credit_hidden"):
        flags.append({"id": "credit", "severity": "error", "fix": "Show the credit",
                      "message": f"The campaign requires the credit \"{r['credit']}\" on screen."})
    if "watermark" in (r.get("onscreen") or []) and not (edl.get("brand") or {}).get("logo"):
        flags.append({"id": "watermark", "severity": "warn", "fix": None,
                      "message": "This campaign needs its watermark: add it in Brand templates."})
    return flags


def fix(edl: dict, flag_id: str) -> dict:
    """Apply one fix; returns a new EDL. Unknown/no-fix flags return the EDL unchanged."""
    e = copy.deepcopy(edl)
    r = e["rules"]
    if flag_id == "overlay":
        e["layout"]["keyframes"], _ = enforce_rules(e["layout"].get("keyframes", []), r.get("onscreen") or [],
                                                    e["layout"].get("facecam"))
    elif flag_id == "music":
        e["music"]["enabled"] = False
    elif flag_id == "platform":
        p = next((PLATFORM_PRESET[x] for x in r.get("platforms") or [] if x in PLATFORM_PRESET), None)
        if p:
            e["export"]["preset"] = p
    elif flag_id == "length":
        lim = PRESETS.get(e["export"].get("preset") or "tiktok", PRESETS["tiktok"])["max_sec"]
        a = e["range"][0]
        # keep the payoff: end the clip at most `lim` output seconds after the start, shifted to include the peak
        peak = e["moment"].get("peak")
        new_end = T.to_src(e, lim) if T.duration(e) > lim else e["range"][1]
        if peak is not None and peak > new_end - 1:
            a = T.to_src(e, T.duration(e) - lim)
            new_end = e["range"][1]
        e["range"] = [round(a, 3), round(new_end, 3)]
    elif flag_id.startswith("hashtags:"):
        plat = flag_id.split(":", 1)[1]
        tx = e["texts"][plat]
        have = [("#" + h.lstrip("#")) for h in tx.get("hashtags") or []]
        for t in r.get("tags") or []:
            if t.lower() not in (h.lower() for h in have):
                have.insert(0, t)
        tx["hashtags"] = have
    elif flag_id.startswith("disclosure:"):
        tx = e["texts"][flag_id.split(":", 1)[1]]
        tx["description"] = ((tx.get("description") or "").rstrip() + "\n\n" + r["disclosure"]).strip()
    elif flag_id == "credit":
        e["credit_hidden"] = False
    return e


def fix_all(edl: dict) -> tuple[dict, list[str]]:
    done = []
    for _ in range(3):
        fl = [f for f in check(edl) if f["fix"]]
        if not fl:
            break
        for f in fl:
            edl = fix(edl, f["id"])
            done.append(f["id"])
    return edl, done
