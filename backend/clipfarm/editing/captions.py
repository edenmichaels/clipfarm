"""Captions (v0.8.5 rebuild): ONE static look, ONE fixed place, ONE renderer.

The standard streamer-clip look: Montserrat Black, UPPERCASE, white with a thick black outline and a soft
shadow, 1-3 words at a time, the word being spoken turns yellow (colour only: never size, weight or position).

Position: horizontally centred, the last line's baseline at a fixed height (77 % down the 1080x1920 frame), inside
the TikTok / Reels / Shorts safe zone, max 2 lines stacked upwards, a fixed 840 px text box. Nothing depends on
the layout, the camera, zooms or layout switches, and nothing animates: a caption appears and disappears whole,
in place. A phrase that doesn't fit 2 lines goes to the next caption -- the text is never shrunk.

Timing: Whisper word timestamps, phrases cut at pauses and punctuation, no caption in silence, no gaps or overlaps
between captions while someone talks, nothing shorter than 0.3 s.

Drawn by libass from an ASS file -- the same file for the Editor preview frames, the proxy and the export.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

W, H = 1080, 1920

# Union of the three apps' UI at 1080x1920: keep text inside this box.
SAFE = {"left": 140, "right": 940, "top": 210, "bottom": 1420}
SAFE_WIDTH = SAFE["right"] - SAFE["left"]          # 800 px, centred on x=540


# ---- fonts (bundled, SIL Open Font License 1.1; licences in assets/fonts) ---------------------------
FONTS = {
    "Montserrat Black": "Montserrat-Black.ttf",
    "Montserrat ExtraBold": "Montserrat-ExtraBold.ttf",
    "Anton": "Anton-Regular.ttf",
    "Archivo Black": "ArchivoBlack-Regular.ttf",
}
DEFAULT_FONT = "Montserrat Black"
# Fonts you add yourself (e.g. Komika Axis, The Bold Font, Bebas Neue) go in Settings › Captions › your fonts
# folder, with their licence. They're never bundled.
OPTIONAL_FONTS = ("Komika Axis", "The Bold Font", "Anton", "Bebas Neue")


def user_fonts() -> dict[str, Path]:
    """{family: file} for the licensed fonts in your fonts folder."""
    try:
        from .. import db
        d = (db.get_setting("captions", {}) or {}).get("fonts_dir")
    except Exception:                       # noqa: BLE001
        d = None
    out: dict[str, Path] = {}
    if not d or not Path(d).is_dir():
        return out
    from fontTools.ttLib import TTFont
    for f in sorted(Path(d).iterdir()):
        if f.suffix.lower() in (".ttf", ".otf"):
            try:
                fam = TTFont(str(f), lazy=True)["name"].getDebugName(1)
            except Exception:               # noqa: BLE001
                continue
            if fam and fam not in FONTS:
                out[fam] = f
    return out


def all_fonts() -> dict[str, Path]:
    d = fonts_dir()
    return {**({k: d / v for k, v in FONTS.items()} if d else {}), **user_fonts()}


def fonts_dir() -> Path | None:
    from .ffmpeg_util import _ROOTS
    for root in _ROOTS:
        d = root / "assets" / "fonts"
        if (d / FONTS["Montserrat Black"]).exists():
            return d
    return None


@lru_cache(maxsize=32)
def _font_info(family: str) -> tuple[int, int, int, int, str] | None:
    """(unitsPerEm, winAscent, winDescent, capHeight, path)."""
    p = all_fonts().get(family)
    if not p:
        return None
    from fontTools.ttLib import TTFont
    t = TTFont(str(p), lazy=True)
    o = t["OS/2"]
    upm = t["head"].unitsPerEm
    return upm, o.usWinAscent, o.usWinDescent, getattr(o, "sCapHeight", 0) or int(upm * 0.7), str(p)


def _metrics(family: str) -> tuple[int, int, str] | None:
    """(unitsPerEm, winAscent+winDescent, path)."""
    i = _font_info(family)
    return (i[0], i[1] + i[2], i[4]) if i else None


def ass_size(family: str, em_px: float) -> float:
    """ASS \\fs value that gives an em of em_px pixels (libass sizes by the Windows line height)."""
    m = _metrics(family)
    return em_px * m[1] / m[0] if m else em_px * 1.2


@lru_cache(maxsize=256)
def _pil_font(family: str, em_q: int):
    from PIL import ImageFont
    m = _metrics(family)
    return ImageFont.truetype(m[2], em_q) if m else None


def text_width(text: str, family: str, em_px: float, outline: float = 0.0) -> float:
    """Rendered width in pixels, including the outline on both sides."""
    f = _pil_font(family, max(1, int(round(em_px * 4))))
    w = f.getlength(text) / 4 if f else len(text) * em_px * 0.62
    return w + 2 * outline


# ---- the fixed geometry (1080x1920) ------------------------------------------------------------------------
# Union of the three apps' UI: keep text inside this box (right-hand buttons, bottom username/description area).
SAFE = {"left": 120, "right": 960, "top": 200, "bottom": 1520}
CAP_WIDTH = 840                 # the fixed text box: ~78 % of the width, centred on x=540
CAP_X = 540
BASELINE = 1480                 # last line's baseline: 77 % of the way down
LINE_GAP = 1.16                 # baseline to baseline, in em
CAP_TOP_LIMIT = 1180            # nothing else (e.g. a bottom-corner facecam) comes below this
TITLE_TOP = 245                 # top of the title (same font, no animation), below the credit
CREDIT_Y = 205
CREDIT_BOTTOM_Y = 1508          # under the captions, above the apps' bottom bar
MIN_CAPTION = 0.3               # seconds
PAUSE = 0.35                    # a gap this long ends a phrase (and no caption is shown in it)
HOLD = 0.2                      # a caption stays this long after its last word, unless the next one starts


# ---- templates (colours, font, case; everything else is fixed) ------------------------------------------------
@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    family: str
    em: float              # font size in px (em) at 1080x1920; cap height = 0.70 em for Montserrat
    uppercase: bool
    text: str              # RGB hex
    highlight: str
    outline: str
    outline_px: float
    shadow_px: float
    shadow_alpha: int      # 0 = solid, 255 = invisible
    max_words: int
    highlight_on: bool = True        # False = "no highlight": every word stays the text colour
    emoji: bool = False
    box: bool = False                # v0.8.8: a solid rounded box behind the caption (static, like the text)
    box_color: str = "000000"
    anchor_y: float | None = None    # v0.8.8: a fixed baseline other than the bottom (Split Screen: on the seam)

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


TEMPLATE_FIELDS = ["family", "em", "uppercase", "text", "highlight", "outline", "outline_px", "shadow_px",
                   "shadow_alpha", "max_words", "emoji", "highlight_on", "box", "box_color", "anchor_y"]
EM_RANGE = (104, 140)            # cap height ~73-98 px with Montserrat


def template_from(t: dict | str | None) -> Preset:
    """A template dict (user-made, possibly partial, possibly from before v0.8.5) or a key -> Preset.
    Old animation / pop / position fields are ignored: captions never animate or move now."""
    if t is None or isinstance(t, str):
        return preset(t)
    base = preset(t.get("base") or t.get("key"))
    vals = base.as_dict()
    for k in TEMPLATE_FIELDS:
        if k in t and t[k] is not None:
            vals[k] = t[k]
    if vals["family"] not in all_fonts():
        vals["family"] = base.family if base.family in all_fonts() else DEFAULT_FONT
    for c in ("text", "highlight", "outline", "box_color"):
        v = str(vals[c]).lstrip("#").upper()
        vals[c] = v if re.fullmatch(r"[0-9A-F]{6}", v) else getattr(base, c)
    vals["em"] = float(min(EM_RANGE[1], max(EM_RANGE[0], float(vals["em"]))))
    vals["highlight_on"] = bool(vals.get("highlight_on", True))
    vals["uppercase"] = bool(vals["uppercase"])
    vals["max_words"] = int(min(6, max(1, int(vals["max_words"]))))
    vals["outline_px"] = float(min(14, max(0 if vals.get("box") else 6, float(vals["outline_px"]))))
    vals["shadow_px"] = float(min(8, max(0, float(vals["shadow_px"]))))
    vals["shadow_alpha"] = int(min(255, max(0, int(vals["shadow_alpha"]))))
    vals["box"] = bool(vals.get("box"))
    ay = vals.get("anchor_y")
    vals["anchor_y"] = float(min(SAFE["bottom"] - 40, max(700, float(ay)))) if ay not in (None, "") else None
    vals["key"] = t.get("key") or base.key
    vals["label"] = t.get("label") or t.get("name") or base.label
    return Preset(**{k: vals[k] for k in Preset.__dataclass_fields__})


PRESETS: dict[str, Preset] = {   # all the standard look; they differ only in colours (and Clean in case)
    "bold_pop": Preset("bold_pop", "Clipper (yellow word)", "Montserrat Black", 124, True, "FFFFFF", "FFE600", "000000", 9, 4, 110, 3),
    "hype_yellow": Preset("hype_yellow", "Clipper (green word)", "Montserrat Black", 124, True, "FFFFFF", "3DFF5A", "000000", 9, 4, 110, 3),
    "clean_white": Preset("clean_white", "Clipper ExtraBold", "Montserrat ExtraBold", 120, True, "FFFFFF", "FFE600", "000000", 8, 4, 110, 3),
    "minimal": Preset("minimal", "Clipper (no highlight)", "Montserrat Black", 120, True, "FFFFFF", "FFFFFF", "000000", 8, 4, 110, 3,
                      highlight_on=False),
}
LEGACY = {"clean": "clean_white", "neon": "hype_yellow"}
DEFAULT = "bold_pop"


def preset(key: str | None) -> Preset:
    key = LEGACY.get(key or "", key or DEFAULT)
    return PRESETS.get(key, PRESETS[DEFAULT])


def presets_public() -> list[dict]:
    return [{"key": p.key, "label": p.label, "font": p.family, "font_file": FONTS.get(p.family, ""),
             "outline_px": p.outline_px, "template": p.as_dict(), "size_px": p.em, "uppercase": p.uppercase,
             "text": "#" + p.text, "highlight": "#" + p.highlight, "outline": "#" + p.outline,
             "max_words": p.max_words, "highlight_on": p.highlight_on} for p in PRESETS.values()]


def _c(rgb: str, alpha: int = 0) -> str:
    """RGB hex -> ASS &HAABBGGRR."""
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{alpha:02X}{b}{g}{r}&".upper()


# ---- clean-up: filler words and profanity ---------------------------------------------------------
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm", "mm", "mhm", "ah", "eh"}
_PROFANE = re.compile(r"^(fuck\w*|motherfuck\w*|shit\w*|bullshit|bitch\w*|cunt\w*|dick\w*|pussy|pussies|"
                      r"cock\w*|bastard\w*|asshole\w*|twat\w*|wank\w*|slut\w*|whore\w*|fag\w*|nigg\w*|retard\w*)$",
                      re.I)


def is_profane(word: str) -> bool:
    return bool(_PROFANE.match(re.sub(r"[^\w']", "", word)))


def _mask(word: str) -> str:
    core = re.sub(r"[^\w']", "", word)
    return core[0] + "*" * (len(core) - 1) if core else word


def clean_words(words: list[dict], profanity: str = "keep", fillers: str = "remove") -> tuple[list[dict], list[tuple]]:
    """Returns (words to show, bleep intervals). profanity: keep | mask | bleep. fillers: keep | remove."""
    out, bleeps = [], []
    for w in words:
        txt = str(w.get("word", "")).strip()
        if not txt:
            continue
        bare = re.sub(r"[^\w']", "", txt).lower()
        if fillers == "remove" and bare in FILLERS:
            continue
        if profanity in ("mask", "bleep") and is_profane(txt):
            if profanity == "bleep":
                bleeps.append((float(w["start"]), float(w["end"])))
            txt = _mask(txt)
        out.append({**w, "word": txt})
    return out, bleeps


# ---- emoji (optional, off by default; Noto Emoji images, Apache 2.0) --------------------------------
EMOJI = {   # keyword stem -> Noto emoji code point
    "laugh": "1f602", "lol": "1f602", "lmao": "1f602", "funny": "1f602", "haha": "1f602",
    "dead": "1f480", "died": "1f480", "dying": "1f480", "fire": "1f525", "insane": "1f525",
    "crazy": "1f92f", "wait": "1f440", "money": "1f4b0", "cash": "1f4b0", "rich": "1f4b0",
    "car": "1f697", "police": "1f6a8", "cops": "1f6a8", "boom": "1f4a5", "explode": "1f4a5", "bomb": "1f4a3",
    "gun": "1f52b", "win": "1f3c6", "won": "1f3c6", "love": "2764", "cry": "1f62d", "sad": "1f62d",
    "scared": "1f631", "scream": "1f631", "goat": "1f410", "clutch": "1f976",
}


def emoji_for(word: str) -> str | None:
    w = re.sub(r"[^\w]", "", word).lower()
    if w in EMOJI:
        return EMOJI[w]
    for k, v in EMOJI.items():
        if len(k) >= 4 and w.startswith(k):
            return v
    return None


# ---- phrases and timing ----------------------------------------------------------------------------------
def _word_text(w: dict, upper: bool) -> str:
    return w["word"].upper() if upper else w["word"]


def _ends_phrase(word: str) -> bool:
    return bool(re.search(r"[.!?,;:]$", word))


def chunk_words(words: list[dict], max_words: int, gap: float = PAUSE) -> list[list[dict]]:
    """Phrases (cut at pauses and punctuation), each split evenly into captions of <= max_words words."""
    phrases: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (float(w["start"]) - float(cur[-1]["end"]) >= gap or _ends_phrase(cur[-1]["word"])):
            phrases.append(cur)
            cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    out = []
    for ph in phrases:
        n = -(-len(ph) // max_words)                  # as few captions as allowed, then balanced sizes
        size = -(-len(ph) // n)
        out += [ph[i:i + size] for i in range(0, len(ph), size)]
    return out


def drawn_width(text: str, pr: Preset) -> float:
    """What libass actually covers: the measured text + outline, 3 % for its hinting/advance rounding (measured
    against real renders), and the shadow's offset on the right."""
    return text_width(text, pr.family, pr.em, pr.outline_px) * 1.03 + pr.shadow_px


def lines_for(words: list[dict], pr: Preset, upper: bool) -> list[list[dict]] | None:
    """1 line if it fits the fixed box, else the most balanced 2 lines, else None (too long: split it)."""
    def width(ws: list[dict]) -> float:
        return drawn_width(" ".join(_word_text(w, upper) for w in ws), pr)
    if width(words) <= CAP_WIDTH:
        return [words]
    best = None
    for i in range(1, len(words)):
        a, b = words[:i], words[i:]
        if width(a) <= CAP_WIDTH and width(b) <= CAP_WIDTH:
            m = max(width(a), width(b))
            if best is None or m < best[0]:
                best = (m, [a, b])
    return best[1] if best else None


def captions_from(words: list[dict], pr: Preset, max_words: int, upper: bool,
                  duration: float | None = None) -> list[dict]:
    """[{start, end, words, lines}] -- whole phrases with fixed timing rules (see the module docstring)."""
    chunks = []
    for ch in chunk_words(words, max_words):
        stack = [ch]
        while stack:                                   # a caption that can't fit 2 lines is split, never shrunk
            c = stack.pop(0)
            ls = lines_for(c, pr, upper)
            if ls is None and len(c) > 1:
                stack[:0] = [c[:len(c) // 2], c[len(c) // 2:]]
            elif ls is None:
                # one word wider than the box on its own (e.g. UNBELIEVABLE): the only case where the text is
                # narrowed -- horizontally, same height and baseline -- so it stays inside the safe zone
                w = drawn_width(_word_text(c[0], upper), pr)
                chunks.append({"words": c, "lines": [c], "condense": round(CAP_WIDTH / w, 3)})
            else:
                chunks.append({"words": c, "lines": ls})
    caps = []
    for i, ch in enumerate(chunks):
        start = float(ch["words"][0]["start"])
        last = float(ch["words"][-1]["end"])
        nxt = float(chunks[i + 1]["words"][0]["start"]) if i + 1 < len(chunks) else None
        if nxt is not None and nxt - last < PAUSE:
            end = nxt                                   # still talking: no gap, no overlap
        else:
            end = last + HOLD if nxt is None else min(last + HOLD, nxt)
        if duration:
            end = min(end, duration)
        caps.append({**ch, "start": start, "end": max(end, start + 0.01)})
    # nothing flashes for less than MIN_CAPTION: stretch into the following silence, else merge with the next
    i = 0
    while i < len(caps):
        c = caps[i]
        if c["end"] - c["start"] >= MIN_CAPTION - 1e-6:
            i += 1
            continue
        nxt = caps[i + 1] if i + 1 < len(caps) else None
        limit = nxt["start"] if nxt else (duration or c["start"] + MIN_CAPTION)
        c["end"] = min(max(c["end"], c["start"] + MIN_CAPTION), limit)
        if c["end"] - c["start"] >= MIN_CAPTION - 1e-6 or not nxt:
            i += 1
            continue
        merged = c["words"] + nxt["words"]
        ls = lines_for(merged, pr, upper)
        if ls is not None and not c.get("condense") and not nxt.get("condense"):
            caps[i] = {"words": merged, "lines": ls, "start": c["start"], "end": nxt["end"]}
            caps.pop(i + 1)
        else:                                          # can't merge: the next caption waits a moment
            nxt["start"] = c["start"] + MIN_CAPTION
            c["end"] = nxt["start"]
            nxt["end"] = max(nxt["end"], nxt["start"] + 0.05)
            i += 1
    return caps


def _ts(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


@dataclass
class CaptionTrack:
    path: Path
    bleeps: list[tuple[float, float]] = field(default_factory=list)
    emojis: list[dict] = field(default_factory=list)       # {"code","start","end","x","y","size"}
    events: list[dict] = field(default_factory=list)       # one per highlight step, see build_track
    title: dict | None = None                              # {"text","box","top","bottom","end"}
    captions: list[dict] = field(default_factory=list)     # one per caption: {start, end, text, box}


def caption_box(n_lines: int, pr: Preset, nudge: float = 0.0, widths: list[float] | None = None) -> dict:
    """The pixel box a caption occupies (outline included): for tests, the facecam corner and the Editor."""
    gap = pr.em * LINE_GAP
    info = _font_info(pr.family)
    cap = pr.em * (info[3] / info[0] if info else 0.7)
    base = (pr.anchor_y or BASELINE) + nudge
    top = base - (n_lines - 1) * gap - cap - pr.outline_px
    bottom = base + pr.em * 0.02 + pr.outline_px
    w = max(widths) if widths else CAP_WIDTH
    return {"x0": CAP_X - w / 2, "x1": CAP_X + w / 2, "top": round(top, 1), "bottom": round(bottom, 1),
            "baseline": round(base, 1), "cx": CAP_X}


def build_track(words: list[dict], out: Path, style: str | dict = DEFAULT, words_per_chunk: int | None = None, *,
                offset: float = 0.0, nudge: float = 0.0, uppercase: bool | None = None, profanity: str = "keep",
                fillers: str = "remove", emoji: bool = False, hook: str | None = None, hook_sec: float = 2.2,
                credit: str | None = None, duration: float | None = None, title_box: str = "outline",
                text_layers: list[dict] | None = None, vocab: dict | bool | None = None,
                credit_pos: str | None = None, title_color: str | None = None) -> CaptionTrack:
    """THE caption renderer (preview frames, proxy and export all use this ASS file).

    words: [{"word","start","end"}] in source time; `offset` is subtracted so they line up with the clip.
    style: a template key or dict. vocab: your vocabulary (None = the saved one, False = off)."""
    pr = template_from(style)
    upper = pr.uppercase if uppercase is None else uppercase
    emoji = emoji or pr.emoji
    nudge = max(-120.0, min(60.0, float(nudge or 0)))   # a fixed offset for the whole clip, kept in the safe zone
    fs = ass_size(pr.family, pr.em)
    sh_a = pr.shadow_alpha
    tfs = ass_size(pr.family, 70)
    tcol = re.sub(r"[^0-9A-Fa-f]", "", str(title_color or "E53935"))[:6].upper()
    tcol = tcol if len(tcol) == 6 else "E53935"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
LayoutResX: {W}
LayoutResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{pr.family},{fs:.1f},{_c(pr.text)},{_c(pr.text)},{_c(pr.outline)},{_c('000000', sh_a)},0,0,0,0,100,100,0,0,1,{pr.outline_px},{pr.shadow_px},2,0,0,0,1
Style: Title,{pr.family},{tfs:.1f},{_c('FFFFFF')},{_c('FFFFFF')},{_c('000000')},{_c('000000', sh_a)},0,0,0,0,100,100,0,0,1,8,{pr.shadow_px},8,0,0,0,1
Style: TitleWhiteBox,{pr.family},{tfs:.1f},{_c('111111')},{_c('111111')},{_c('FFFFFF')},{_c('000000', 200)},0,0,0,0,100,100,0,0,3,14,0,8,0,0,0,1
Style: TitleBlackBox,{pr.family},{tfs:.1f},{_c('FFFFFF')},{_c('FFFFFF')},{_c('000000', 30)},{_c('000000', 200)},0,0,0,0,100,100,0,0,3,14,0,8,0,0,0,1
Style: TitleColorBox,{pr.family},{tfs:.1f},{_c('FFFFFF')},{_c('FFFFFF')},{_c(tcol)},{_c('000000', 200)},0,0,0,0,100,100,0,0,3,14,0,8,0,0,0,1
Style: TitlePlain,{pr.family},{ass_size(pr.family, 50):.1f},{_c('FFFFFF')},{_c('FFFFFF')},{_c('000000', 80)},{_c('000000', 120)},0,0,0,0,100,100,0,0,1,3,4,8,0,0,0,1
Style: CapBox,{pr.family},{fs:.1f},{_c(pr.box_color)},{_c(pr.box_color)},{_c(pr.box_color)},{_c(pr.box_color)},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Text,{pr.family},{tfs:.1f},{_c('FFFFFF')},{_c('FFFFFF')},{_c('000000')},{_c('000000', sh_a)},0,0,0,0,100,100,0,0,1,8,{pr.shadow_px},5,0,0,0,1
Style: Credit,Montserrat ExtraBold,{ass_size('Montserrat ExtraBold', 34):.1f},{_c('FFFFFF', 40)},{_c('FFFFFF')},{_c('000000', 60)},{_c('000000', 160)},0,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    shown, bleeps = clean_words(words, profanity, fillers)
    if vocab is not False:
        from . import vocab as _vocab
        try:
            shown = _vocab.fix_words(shown, vocab if isinstance(vocab, dict) else None)
        except Exception:                   # noqa: BLE001  (no DB: captions still work)
            pass
    ws = [{**w, "start": float(w["start"]) - offset, "end": float(w["end"]) - offset} for w in shown]
    ws = [w for w in ws if w["end"] > 0 and (not duration or w["start"] < duration)]
    for w in ws:
        w["start"] = max(0.0, w["start"])
    ws.sort(key=lambda w: w["start"])
    bleeps = [(a - offset, b - offset) for a, b in bleeps if b - offset > 0]
    lines: list[str] = []
    events: list[dict] = []
    captions: list[dict] = []
    emojis: list[dict] = []
    max_words = max(1, min(6, words_per_chunk or pr.max_words))
    gap = pr.em * LINE_GAP
    base = (pr.anchor_y or BASELINE) + nudge
    hi_c = pr.highlight if pr.highlight_on else pr.text
    for cap in captions_from(ws, pr, max_words, upper, duration):
        rows = cap["lines"]
        n = len(rows)
        widths = [drawn_width(" ".join(_word_text(w, upper) for w in r), pr) * cap.get("condense", 1.0) for r in rows]
        box = caption_box(n, pr, nudge, widths)
        captions.append({"start": round(cap["start"], 3), "end": round(cap["end"], 3),
                         "text": " / ".join(" ".join(_word_text(w, upper) for w in r) for r in rows), "box": box})
        if pr.box:                       # one solid rounded box behind the whole caption, for its whole time
            pad_x, pad_y = pr.em * 0.32, pr.em * 0.22
            x0, x1 = box["x0"] - pad_x, box["x1"] + pad_x
            y0, y1 = box["top"] - pad_y, box["bottom"] + pad_y + pr.em * 0.12
            lines.append(f"Dialogue: 0,{_ts(cap['start'])},{_ts(cap['end'])},CapBox,,0,0,0,,"
                         f"{{\\an7\\pos(0,0)\\p1}}{rounded_rect(x0, y0, x1, y1, pr.em * 0.22)}{{\\p0}}")
        # one step per spoken word: identical text and positions, only the spoken word's colour changes
        order = [w for r in rows for w in r]
        steps = [(cap["start"], None)] if float(order[0]["start"]) > cap["start"] + 0.01 else []
        for j, w in enumerate(order):
            steps.append((max(cap["start"], float(w["start"])), w))
        for j, (s0, cur) in enumerate(steps):
            s1 = steps[j + 1][0] if j + 1 < len(steps) else cap["end"]
            if s1 - s0 < 0.01:
                continue
            for li, r in enumerate(rows):
                y = base - (n - 1 - li) * gap
                parts = []
                for w in r:
                    txt = _esc(_word_text(w, upper))
                    # every word is its own colour run in every frame, so the text is shaped (kerned) the same way
                    # whichever word is lit: the glyphs never shift by a pixel when the highlight moves on
                    col = hi_c if (w is cur and pr.highlight_on) else pr.text
                    parts.append(f"{{\\c{_c(col)}}}{txt}")
                sx = f"\\fscx{cap['condense'] * 100:.1f}" if cap.get("condense") else ""
                lines.append(f"Dialogue: {1 if pr.box else 0},{_ts(s0)},{_ts(s1)},Cap,,0,0,0,,"
                             f"{{\\an2\\pos({CAP_X},{y + _below_baseline(pr):.1f}){sx}\\q2}}{' '.join(parts)}")
            events.append({"start": round(s0, 3), "end": round(s1, 3), "word": cur["word"] if cur else None,
                           "x": CAP_X, "y": round(base), "baseline": round(base, 1), "lines": n,
                           "rows": [[_word_text(w, upper) for w in r] for r in rows],
                           "hi": [[w is cur for w in r] for r in rows], "box": box})
        if emoji:
            code = next((c for w in order if (c := emoji_for(w["word"]))), None)
            if code:
                size = int(pr.em * 1.1)
                emojis.append({"code": code, "start": cap["start"], "end": cap["end"], "size": size,
                               "x": CAP_X - size // 2, "y": int(box["top"] - size - 14)})
    title = None
    if hook and title_box != "none":
        boxed = title_box in ("white_box", "black_box", "color_box")
        sty = {"white_box": "TitleWhiteBox", "black_box": "TitleBlackBox", "color_box": "TitleColorBox",
               "plain": "TitlePlain"}.get(title_box, "Title")
        tp = Preset("title", "", pr.family, 50 if title_box == "plain" else 70, upper, "FFFFFF", "FFFFFF", "000000",
                    8 if not boxed else 14, 0, 0, 99)
        tws = [{"word": t} for t in hook.split()]
        rows = lines_for(tws, tp, upper) or [tws]
        if len(rows) > 2:
            rows = rows[:2]
        step = tp.em * 1.2 + (28 if boxed else 0)
        top = TITLE_TOP + (14 if boxed else 0)
        for li, r in enumerate(rows):
            txt = " ".join(_esc(_word_text(w, upper)) for w in r)
            lines.append(f"Dialogue: 1,{_ts(0)},{_ts(hook_sec)},{sty},,0,0,0,,"
                         f"{{\\an8\\pos({CAP_X},{top + li * step:.0f})\\q2}}{txt}")
        title = {"text": hook, "box": title_box, "top": TITLE_TOP, "bottom": round(top + len(rows) * step), "end": hook_sec}
    for tl in (text_layers or [])[:30]:
        txt = _esc(str(tl.get("text") or "").strip())[:200]
        if not txt:
            continue
        a, b = float(tl.get("start", 0)), float(tl.get("end", duration or 3))
        if b <= a:
            continue
        x, y = max(0.0, min(1.0, float(tl.get("x", 0.5)))) * W, max(0.0, min(1.0, float(tl.get("y", 0.3)))) * H
        fam = tl.get("font") if tl.get("font") in all_fonts() else pr.family
        fs_ = ass_size(fam, max(24, min(220, float(tl.get("size", 72)))))
        box = tl.get("box") or "outline"
        sty = {"white_box": "TitleWhiteBox", "black_box": "TitleBlackBox"}.get(box, "Text")
        col = re.sub(r"[^0-9A-Fa-f]", "", str(tl.get("color") or ""))[:6]
        colour = "" if box in ("white_box", "black_box") or len(col) != 6 else f"\\1c{_c(col)}"
        rot = float(tl.get("rotate", 0) or 0)
        lines.append(f"Dialogue: 3,{_ts(a)},{_ts(b)},{sty},,0,0,0,,{{\\an5\\pos({x:.0f},{y:.0f})\\fn{fam}\\fs{fs_:.1f}"
                     f"{colour}{f'{chr(92)}frz{-rot:.1f}' if rot else ''}\\q2}}{txt}")
    if credit:
        end = duration if duration else 3600
        cy = CREDIT_BOTTOM_Y if credit_pos == "bottom" else CREDIT_Y
        lines.append(f"Dialogue: 2,{_ts(0)},{_ts(end)},Credit,,0,0,0,,{{\\an5\\pos({CAP_X},{cy})\\q2}}"
                     f"{_esc(credit)}")
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    out.with_suffix(".events.json").write_text(json.dumps(events), encoding="utf-8")
    return CaptionTrack(out, bleeps, emojis, events, title, captions)


def rounded_rect(x0: float, y0: float, x1: float, y1: float, r: float) -> str:
    """An ASS vector drawing (\\p1) of a rounded rectangle, absolute pixels."""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    k = r * 0.45                                   # bezier handle for a near-circular corner
    f = lambda v: f"{v:.0f}"                       # noqa: E731
    return (f"m {f(x0 + r)} {f(y0)} l {f(x1 - r)} {f(y0)} b {f(x1 - k)} {f(y0)} {f(x1)} {f(y0 + k)} {f(x1)} {f(y0 + r)} "
            f"l {f(x1)} {f(y1 - r)} b {f(x1)} {f(y1 - k)} {f(x1 - k)} {f(y1)} {f(x1 - r)} {f(y1)} "
            f"l {f(x0 + r)} {f(y1)} b {f(x0 + k)} {f(y1)} {f(x0)} {f(y1 - k)} {f(x0)} {f(y1 - r)} "
            f"l {f(x0)} {f(y0 + r)} b {f(x0)} {f(y0 + k)} {f(x0 + k)} {f(y0)} {f(x0 + r)} {f(y0)}")


def _below_baseline(pr: Preset) -> float:
    """\\an2 anchors the bottom of the line box: how far that is below the baseline (measured for libass)."""
    i = _font_info(pr.family)
    return pr.em * DESC_FACTOR * (i[2] / i[0] if i else 0.25)


DESC_FACTOR = 1.0
