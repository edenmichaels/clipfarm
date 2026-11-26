"""Fixed framing (v0.8.5): no panning, drifting, tracking, animated zooms or shakes -- ever.

Projects made before v0.8.5 are converted the first time they're opened (and by migration v12):
  * pan / tracking keyframes -> one fixed crop per shot (the average framing),
  * zoom pulses on beats -> removed; punch-ins stay but render as instant zoom cuts,
  * screen shakes -> removed; "whip" transitions -> instant zoom cuts.
"""
from __future__ import annotations

import copy

MARK = "fixed"          # edl["framing"]


def _fix_keyframes(kfs: list[dict]) -> int:
    n = 0
    for k in kfs or []:
        path = k.get("path") or [[0, 0.5]]
        xs = [float(p[1]) for p in path]
        if len(path) > 1:
            n += 1 if max(xs) - min(xs) > 1e-3 else 0
            k["path"] = [[0.0, round(sum(xs) / len(xs), 4)]]
    return n


def convert(edl: dict) -> tuple[dict, list[str]]:
    """-> (converted edl, what changed in plain words). Running it on a converted edl changes nothing."""
    e = copy.deepcopy(edl)
    changes = []
    pans = _fix_keyframes(e.get("layout", {}).get("keyframes"))
    for it in e.get("sequence") or []:
        pans += _fix_keyframes((it.get("layout") or {}).get("keyframes"))
    if pans:
        changes.append(f"{pans} camera pan{'s' if pans != 1 else ''}")
    zs = e.get("zooms") or []
    pulses = [z for z in zs if z.get("kind") == "pulse"]
    if pulses:
        e["zooms"] = [z for z in zs if z.get("kind") != "pulse"]
        changes.append(f"{len(pulses)} zoom pulse{'s' if len(pulses) != 1 else ''}")
    punches = [z for z in zs if z.get("kind") != "pulse"]
    if punches and e.get("framing") != MARK:
        changes.append(f"{len(punches)} animated zoom{'s' if len(punches) != 1 else ''} (now instant cuts)")
    shakes = [o for o in e.get("overlays") or [] if o.get("kind") == "shake"]
    if shakes:
        e["overlays"] = [o for o in e["overlays"] if o.get("kind") != "shake"]
        changes.append("screen shake")
    whips = [x for x in e.get("transitions") or [] if x.get("kind") == "whip"]
    if whips:
        for x in whips:
            x["kind"] = "zoom"
        changes.append("whip transitions (now zoom cuts)")
    m = e.get("music") or {}
    if m.get("zoom_beats"):
        m["zoom_beats"] = []
    e["framing"] = MARK
    return e, changes


def notice(changes: list[str]) -> str | None:
    if not changes:
        return None
    return "Fixed framing: converted " + ", ".join(changes) + " to one steady crop per shot."
