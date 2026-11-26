"""Custom vocabulary for captions: streamer names, game terms and slang Whisper gets wrong.

Settings › Captions › Vocabulary (you can edit it):
  terms  words/names spelled the way you want them ("Kai Cenat", "Vice City", "Leonida")
  fixes  explicit corrections, "what Whisper wrote" -> "what it should say" ("vice sitty" -> "Vice City")

Used twice: as a hint to Whisper while transcribing (initial prompt), and to correct the caption text when it's
drawn (preview and export alike), so editing the list fixes existing clips too. It starts with the names of the
streamers you're allowed to clip and common GTA 6 terms.
"""
from __future__ import annotations

import difflib
import re

from .. import db

GTA6_TERMS = ["GTA", "GTA 6", "Grand Theft Auto", "Rockstar", "Vice City", "Leonida", "Leonida Keys", "Lucia",
              "Jason", "Port Gellhorn", "Ambrosia", "Grassrivers", "Mount Kalaga", "wanted level", "five-star",
              "heist", "NPC", "NPCs", "cops", "GG", "W", "L", "bro", "no way"]
MAX_TERMS = 400


def _seed() -> dict:
    names = []
    try:
        names = [r["n"] for r in db.q("SELECT COALESCE(display_name, login) n FROM streamers "
                                      "WHERE permission_status IN ('allowed','program_member') ORDER BY n LIMIT 200")]
    except Exception:                       # noqa: BLE001  (no DB yet)
        pass
    return {"terms": sorted(set(names)) + GTA6_TERMS, "fixes": []}


def get() -> dict:
    v = db.get_setting("vocabulary")
    if not isinstance(v, dict) or "terms" not in v:
        v = _seed()
        db.set_setting("vocabulary", v)
    return v


def set_(terms: list[str], fixes: list[dict]) -> dict:
    t = [str(x).strip()[:60] for x in terms if str(x).strip()][:MAX_TERMS]
    f = [{"from": str(x.get("from", "")).strip()[:60], "to": str(x.get("to", "")).strip()[:60]}
         for x in fixes if str(x.get("from", "")).strip() and str(x.get("to", "")).strip()][:MAX_TERMS]
    v = {"terms": list(dict.fromkeys(t)), "fixes": f}
    db.set_setting("vocabulary", v)
    return v


def prompt() -> str | None:
    """A short hint for Whisper: the names and terms it should expect."""
    terms = [t for t in get()["terms"] if len(t) > 2][:80]
    return ("Gaming stream. Names and terms: " + ", ".join(terms) + ".") if terms else None


def _bare(s: str) -> str:
    return re.sub(r"[^\w']", "", s).lower()


def fix_words(words: list[dict], vocab: dict | None = None) -> list[dict]:
    """Correct the caption words (timings unchanged). Multi-word fixes/terms can merge words into one."""
    v = vocab or get()
    fixes = [(f["from"].lower().split(), f["to"]) for f in v.get("fixes", [])]
    terms = v.get("terms", [])
    multi = [(t.lower().split(), t) for t in terms if " " in t]
    single = {t.lower(): t for t in terms if " " not in t}
    long_single = [t for t in single if len(t) >= 5]
    out: list[dict] = []
    i, ws = 0, list(words)
    while i < len(ws):
        done = False
        for src, dst in sorted(fixes + multi, key=lambda x: -len(x[0])):        # longest phrase first
            n = len(src)
            if n and i + n <= len(ws) and [_bare(w["word"]) for w in ws[i:i + n]] == [re.sub(r"[^\w']", "", s) for s in src]:
                punct = re.search(r"[^\w']+$", ws[i + n - 1]["word"])
                out.append({**ws[i], "word": dst + (punct.group(0) if punct else ""), "end": ws[i + n - 1]["end"]})
                i += n
                done = True
                break
        if done:
            continue
        w = ws[i]
        b = _bare(w["word"])
        punct = re.search(r"[^\w']+$", w["word"])
        p = punct.group(0) if punct else ""
        if b in single:
            out.append({**w, "word": single[b] + p})
        elif len(b) >= 5 and (m := difflib.get_close_matches(b, long_single, n=1, cutoff=0.84)):
            out.append({**w, "word": single[m[0]] + p})
        else:
            out.append(w)
        i += 1
    return out
