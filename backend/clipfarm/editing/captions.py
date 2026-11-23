"""Short-form captions: 1–3 words at a time, synced word by word, the spoken word highlighted with a pop.

Rendered by FFmpeg's libass from an ASS file. v0.4 rewrite: every line is placed with an explicit
centre point (\\an + \\pos) on the 1080x1920 canvas, and line breaks are measured with the real font
(libass sizes text from the font's Windows ascent+descent, so widths here match the render to the
pixel). Nothing depends on style margins, alignment defaults or the source resolution, which is what
let captions drift off-centre and run off the edges before.

Placement keeps clear of the TikTok / Reels / Shorts interface (top bar, right-hand buttons, bottom
caption + username area), the streamer's face in split layouts, and a campaign's required overlay.
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


def fonts_dir() -> Path | None:
    from .ffmpeg_util import _ROOTS
    for root in _ROOTS:
        d = root / "assets" / "fonts"
        if (d / FONTS["Montserrat Black"]).exists():
            return d
    return None


@lru_cache(maxsize=16)
def _metrics(family: str) -> tuple[int, int, str] | None:
    """(unitsPerEm, winAscent+winDescent, path) for a bundled font."""
    d = fonts_dir()
    if not d:
        return None
    from fontTools.ttLib import TTFont
    p = d / FONTS[family]
    t = TTFont(str(p), lazy=True)
    return t["head"].unitsPerEm, t["OS/2"].usWinAscent + t["OS/2"].usWinDescent, str(p)


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


# ---- presets ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    family: str
    em: float              # font size in px (em) at 1080x1920
    uppercase: bool
    text: str              # RGB hex
    highlight: str
    outline: str
    outline_px: float
    shadow_px: float
    shadow_alpha: int      # 0 = solid, 255 = invisible
    pop: int               # % scale the spoken word pops from (100 = no pop)
    max_words: int
    blur: float = 1.5      # softens the outline and shadow edge
    animation: str = "pop"           # pop | bounce | karaoke | typewriter | none
    highlight_on: bool = True        # False = "no highlight": the active word stays the text colour
    y: float | None = None           # vertical centre as a fraction of the height (None = layout default)
    emoji: bool = False

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


ANIMATIONS = ["pop", "bounce", "karaoke", "typewriter", "none"]
TEMPLATE_FIELDS = ["family", "em", "uppercase", "text", "highlight", "outline", "outline_px", "shadow_px",
                   "shadow_alpha", "pop", "max_words", "blur", "animation", "y", "emoji", "highlight_on"]


def template_from(t: dict | str | None) -> Preset:
    """A template dict (user-made, possibly partial) or a preset key -> Preset. Invalid values fall back."""
    if t is None or isinstance(t, str):
        return preset(t)
    base = preset(t.get("base") or t.get("key"))
    vals = base.as_dict()
    for k in TEMPLATE_FIELDS:
        if k in t and t[k] is not None:
            vals[k] = t[k]
    if vals["family"] not in FONTS:
        vals["family"] = base.family
    if vals["animation"] not in ANIMATIONS:
        vals["animation"] = "pop"
    for c in ("text", "highlight", "outline"):
        v = str(vals[c]).lstrip("#").upper()
        vals[c] = v if re.fullmatch(r"[0-9A-F]{6}", v) else getattr(base, c)
    vals["em"] = float(min(140, max(40, float(vals["em"]))))
    vals["highlight_on"] = bool(vals.get("highlight_on", True))
    vals["max_words"] = int(min(3, max(1, int(vals["max_words"]))))
    vals["outline_px"] = float(min(16, max(0, float(vals["outline_px"]))))
    vals["shadow_px"] = float(min(16, max(0, float(vals["shadow_px"]))))
    vals["shadow_alpha"] = int(min(255, max(0, int(vals["shadow_alpha"]))))
    vals["pop"] = int(min(140, max(100, int(vals["pop"]))))
    if vals["y"] is not None:
        vals["y"] = float(min(0.74, max(0.11, float(vals["y"]))))
    vals["key"] = t.get("key") or base.key
    vals["label"] = t.get("label") or t.get("name") or base.label
    return Preset(**vals)


PRESETS: dict[str, Preset] = {
    "bold_pop": Preset("bold_pop", "Bold Pop", "Montserrat Black", 100, True, "FFFFFF", "FFE600", "000000", 9, 6, 90, 118, 3),
    "hype_yellow": Preset("hype_yellow", "Hype Yellow", "Anton", 118, True, "FFE600", "3DFF5A", "000000", 10, 7, 80, 120, 2),
    "clean_white": Preset("clean_white", "Clean White", "Montserrat ExtraBold", 84, False, "FFFFFF", "5CE1FF", "101010", 6, 4, 120, 108, 3),
    "minimal": Preset("minimal", "Minimal", "Archivo Black", 70, False, "FFFFFF", "FFFFFF", "000000", 5, 3, 140, 100, 3),
}
LEGACY = {"clean": "clean_white", "neon": "hype_yellow"}
DEFAULT = "bold_pop"


def preset(key: str | None) -> Preset:
    key = LEGACY.get(key or "", key or DEFAULT)
    return PRESETS.get(key, PRESETS[DEFAULT])


def presets_public() -> list[dict]:
    return [{"key": p.key, "label": p.label, "font": p.family, "font_file": FONTS[p.family], "outline_px": p.outline_px,
             "animation": p.animation, "template": p.as_dict(), "size_px": p.em, "uppercase": p.uppercase,
             "text": "#" + p.text, "highlight": "#" + p.highlight, "outline": "#" + p.outline,
             "max_words": p.max_words, "pop": p.pop > 100} for p in PRESETS.values()]


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


# ---- placement ------------------------------------------------------------------------------------------
@dataclass
class Placement:
    y: float                   # vertical centre of the caption block
    max_lines: int = 2
    region: tuple[float, float] = (SAFE["top"], SAFE["bottom"])   # the block must stay inside this
    hook_y: float = 300        # vertical centre of the hook
    credit_y: float = 236      # vertical centre of the credit line
    note: str = ""


def placement(layout: str, src_w: int = 1920, src_h: int = 1080, keep_clear: tuple[str, ...] = (),
              nudge: float = 0.0, cam_h: int = 640) -> Placement:
    """Where captions go for a layout. nudge = px up (-) or down (+), set by the user in Review."""
    if layout == "split":
        # On the seam between facecam (top) and gameplay: never over the face, never over the HUD.
        p = Placement(y=cam_h + 70, max_lines=2, region=(cam_h - 70, SAFE["bottom"]), hook_y=cam_h + 330,
                      credit_y=SAFE["bottom"] - 30, note="seam")
    elif layout == "blur_fill":
        band = W * src_h / max(src_w, 1)
        top, bottom = (H - band) / 2, (H + band) / 2
        if "streamer_overlay" in keep_clear or "streamer_face" in keep_clear:
            # The campaign's overlay is somewhere in the picture: put captions under the picture.
            p = Placement(y=bottom + 90, max_lines=2, region=(bottom + 16, SAFE["bottom"]),
                          hook_y=max(SAFE["top"] + 90, top - 120), credit_y=SAFE["top"] + 26, note="below picture")
        else:
            p = Placement(y=H * 0.64, hook_y=max(SAFE["top"] + 160, min(top - 110, 420)), credit_y=SAFE["top"] + 26)
    else:   # crop (full-height gameplay)
        p = Placement(y=H * 0.64, hook_y=SAFE["top"] + 170, credit_y=SAFE["top"] + 26)
    p.y += nudge
    return p


POSITIONS = ("seam", "upper", "centre", "lower")


def position_y(position: str, layout: str, cam_h: int, pl: Placement) -> float:
    """Centre y of a Style caption position. Split layouts keep every position under the facecam."""
    if layout == "split":
        return {"seam": cam_h + 70, "upper": cam_h + 260, "centre": (cam_h + SAFE["bottom"]) / 2,
                "lower": H * 0.70}[position]
    if position == "seam":            # no seam on this layout: the lower third is the closest match
        position = "lower"
    return {"upper": H * 0.30, "centre": H * 0.50, "lower": H * 0.64}[position]


# ---- layout of one chunk -------------------------------------------------------------------------------
@dataclass
class Line:
    words: list[dict]


@dataclass
class Block:
    lines: list[list[dict]]
    em: float
    y: float                   # centre of the block
    line_h: float
    fits: bool = True

    @property
    def height(self) -> float:
        return self.line_h * len(self.lines)


def _word_text(w: dict, upper: bool) -> str:
    return w["word"].upper() if upper else w["word"]


def layout_chunk(words: list[dict], pr: Preset, pl: Placement, upper: bool, max_width: float = SAFE_WIDTH) -> Block:
    """Fit the chunk inside the safe width: one line, else two balanced lines, else shrink the text."""
    pop = pr.pop / 100

    def width(ws: list[dict], em: float) -> float:
        txt = " ".join(_word_text(w, upper) for w in ws)
        # widest moment = longest word popped
        extra = max((text_width(_word_text(w, upper), pr.family, em) for w in ws), default=0) * (pop - 1)
        return text_width(txt, pr.family, em, pr.outline_px) + extra

    em = pr.em
    lo, hi = pl.region
    room = hi - lo
    candidates = [[words]]
    if len(words) > 1 and pl.max_lines >= 2:
        candidates += [[words[:i], words[i:]] for i in range(1, len(words))]
    while True:
        fits = [c for c in candidates if all(width(l, em) <= max_width for l in c) and em * 1.12 * len(c) <= room]
        if fits:
            best = min(fits, key=lambda c: (len(c), max(width(l, em) for l in c)))
            break
        if em <= pr.em * 0.6:
            best = min(candidates, key=lambda c: (max(width(l, em) for l in c) > max_width, len(c)))
            break
        em *= 0.94
    ok = all(width(l, em) <= max_width for l in best) and em * 1.12 * len(best) <= room
    line_h = em * 1.12
    y = pl.y
    blk = Block(best, em, y, line_h, ok)
    top = y - blk.height / 2
    if top < lo:
        y += lo - top
    if y + blk.height / 2 > hi:
        y -= y + blk.height / 2 - hi
    blk.y = max(SAFE["top"] + blk.height / 2, min(SAFE["bottom"] - blk.height / 2, y))
    return blk


# ---- chunking -----------------------------------------------------------------------------------------
def chunk_words(words: list[dict], max_words: int, gap: float = 0.55) -> list[list[dict]]:
    """1–max_words per chunk; a pause or sentence end always starts a new chunk."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > gap or re.search(r"[.!?]$", cur[-1]["word"])):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


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
    events: list[dict] = field(default_factory=list)       # {"start","end","word","x","y","w","h"} for tests/preview
    title: dict | None = None                              # {"text","box","y","h","end"} where the title went


def build_ass(words: list[dict], out: Path, style_name: str = DEFAULT, words_per_chunk: int | None = None,
              width: int = W, height: int = H, offset: float = 0.0, *, layout: str = "crop", src_w: int = 1920,
              src_h: int = 1080, keep_clear: tuple[str, ...] = (), nudge: float = 0.0, uppercase: bool | None = None,
              profanity: str = "keep", fillers: str = "remove", emoji: bool = False, hook: str | None = None,
              hook_sec: float = 2.2, credit: str | None = None, duration: float | None = None,
              cam_h: int = 640) -> Path:
    return build_track(words, out, style_name, words_per_chunk, offset=offset, layout=layout, src_w=src_w,
                       src_h=src_h, keep_clear=keep_clear, nudge=nudge, uppercase=uppercase, profanity=profanity,
                       fillers=fillers, emoji=emoji, hook=hook, hook_sec=hook_sec, credit=credit,
                       duration=duration, cam_h=cam_h).path


def build_track(words: list[dict], out: Path, style_name: str = DEFAULT, words_per_chunk: int | None = None, *,
                offset: float = 0.0, layout: str = "crop", src_w: int = 1920, src_h: int = 1080,
                keep_clear: tuple[str, ...] = (), nudge: float = 0.0, uppercase: bool | None = None,
                profanity: str = "keep", fillers: str = "remove", emoji: bool = False, hook: str | None = None,
                hook_sec: float = 2.2, credit: str | None = None, duration: float | None = None,
                cam_h: int = 640, layout_segments: list[tuple[float, str]] | None = None,
                credit_pos: str | None = None, position: str | None = None, title_box: str = "outline",
                title_pos: str | None = None, text_layers: list[dict] | None = None) -> CaptionTrack:
    """words: [{"word","start","end"}] in source time; `offset` is subtracted so they line up with the clip.
    style_name: a preset key or a template dict. layout_segments: [(clip time, layout)] when the layout
    changes mid-clip -- each caption uses the placement of the layout on screen when it appears."""
    pr = template_from(style_name)
    upper = pr.uppercase if uppercase is None else uppercase
    emoji = emoji or pr.emoji

    # ONE caption position for the whole clip. (It used to follow the layout on screen at each chunk, so when the
    # AI reframe switched split <-> crop mid-clip the captions jumped between the seam and the lower third.)
    # The position comes from the layout that's on screen longest.
    segs = sorted(layout_segments or [], key=lambda x: x[0])
    if segs:
        total = duration or max((float(w["end"]) - offset for w in words), default=0.0) or 1.0
        span: dict[str, float] = {}
        for i, (t0, name) in enumerate(segs):
            t1 = segs[i + 1][0] if i + 1 < len(segs) else max(total, t0)
            span[name] = span.get(name, 0.0) + max(0.0, min(t1, total) - max(t0, 0.0))
        layout = max(span, key=lambda k: span[k]) if span else layout
    lay0 = "crop" if layout in ("speaker", "pip", "cam_full") else layout
    pl = placement(lay0, src_w, src_h, tuple(keep_clear), nudge, cam_h)
    if position in POSITIONS:
        # a Style's caption position: one of four fixed spots (the split layout keeps them off the facecam)
        pl.y = position_y(position, lay0, cam_h, pl) + nudge
    elif pr.y is not None and lay0 != "split":
        pl.y = pr.y * H + nudge
    if title_pos == "top":
        pl.hook_y = SAFE["top"] + 60
    elif title_pos == "seam" and lay0 == "split":
        pl.hook_y = cam_h
    if lay0 == "split" and title_pos == "seam" and position in (None, "seam"):
        pl.y = cam_h + 230 + nudge         # the title owns the seam; captions sit just under it
    # Fix the centre once so that the tallest block (2 lines at full size) fits the region; every chunk then shares
    # exactly this centre, whether it has one line or two.
    lo, hi = pl.region
    tall = min(pr.em * 1.12 * min(2, pl.max_lines), hi - lo)   # chunks shrink to fit a short region
    pl.y = max(lo + tall / 2, min(hi - tall / 2, pl.y))
    pl.y = max(SAFE["top"] + tall / 2, min(SAFE["bottom"] - tall / 2, pl.y))
    fixed_y = pl.y

    def place_for(t: float) -> Placement:
        return pl
    fs = ass_size(pr.family, pr.em)
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
Style: Cap,{pr.family},{fs:.1f},{_c(pr.text)},{_c(pr.highlight)},{_c(pr.outline)},{_c('000000', pr.shadow_alpha)},0,0,0,0,100,100,0,0,1,{pr.outline_px},{pr.shadow_px},5,0,0,0,1
Style: Hook,{pr.family},{ass_size(pr.family, 74):.1f},{_c('FFFFFF')},{_c(pr.highlight)},{_c('000000')},{_c('000000', 90)},0,0,0,0,100,100,0,0,1,8,5,5,0,0,0,1
Style: HookWhiteBox,{pr.family},{ass_size(pr.family, 64):.1f},{_c('111111')},{_c('111111')},{_c('FFFFFF')},{_c('000000', 200)},0,0,0,0,100,100,0,0,3,18,0,5,0,0,0,1
Style: HookBlackBox,{pr.family},{ass_size(pr.family, 64):.1f},{_c('FFFFFF')},{_c('FFFFFF')},{_c('000000', 30)},{_c('000000', 200)},0,0,0,0,100,100,0,0,3,18,0,5,0,0,0,1
Style: Credit,Montserrat ExtraBold,{ass_size('Montserrat ExtraBold', 34):.1f},{_c('FFFFFF', 40)},{_c('FFFFFF')},{_c('000000', 60)},{_c('000000', 160)},0,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    shown, bleeps = clean_words(words, profanity, fillers)
    ws = [{**w, "start": float(w["start"]) - offset, "end": float(w["end"]) - offset} for w in shown]
    ws = [w for w in ws if w["end"] > 0]
    for w in ws:
        w["start"] = max(0.0, w["start"])
    bleeps = [(a - offset, b - offset) for a, b in bleeps if b - offset > 0]
    lines: list[str] = []
    events: list[dict] = []
    emojis: list[dict] = []
    per_chunk = max(1, min(3, words_per_chunk or pr.max_words))
    chunks = []
    for ch in chunk_words(ws, per_chunk):        # a chunk that can't fit even shrunk is split into smaller ones
        stack = [ch]
        while stack:
            c = stack.pop(0)
            if len(c) > 1 and not layout_chunk(c, pr, place_for(c[0]["start"]), upper).fits:
                stack[:0] = [c[:len(c) // 2], c[len(c) // 2:]]
            else:
                chunks.append(c)
    blur = f"\\blur{pr.blur:g}" if pr.blur else ""
    for ci, chunk in enumerate(chunks):
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        chunk_end = chunk[-1]["end"] + 0.25          # linger a moment, but never into a silence
        if nxt is not None:
            chunk_end = min(chunk_end, nxt)
        if duration:
            chunk_end = min(chunk_end, duration)
        cpl = place_for(chunk[0]["start"])
        blk = layout_chunk(chunk, pr, cpl, upper)
        blk.y = fixed_y                              # never re-centred per chunk
        cfs = ass_size(pr.family, blk.em)
        first_y = blk.y - blk.height / 2 + blk.line_h / 2
        for j, cur in enumerate(chunk):
            start = cur["start"]
            end = chunk[j + 1]["start"] if j + 1 < len(chunk) else chunk_end
            end = max(end, start + 0.05)
            rows = []
            order = [w for line in blk.lines for w in line]
            cur_i = order.index(cur)
            a = pr.animation
            if a == "pop" and pr.pop > 100:
                anim = f"\\fscx{pr.pop}\\fscy{pr.pop}\\t(0,90,\\fscx100\\fscy100)"
            elif a == "bounce":
                anim = (f"\\fscx{max(100, pr.pop - 6)}\\fscy78\\t(0,70,\\fscy{pr.pop + 8})"
                        f"\\t(70,150,\\fscx100\\fscy100)")
            else:
                anim = ""
            hi_c = pr.highlight if pr.highlight_on else pr.text
            pop_word = None                               # (text, x, y) of the active word, drawn on its own layer
            for li, line in enumerate(blk.lines):
                parts = []
                y = first_y + li * blk.line_h
                for wj, w in enumerate(line):
                    txt = _esc(_word_text(w, upper))
                    wi = order.index(w)
                    if a == "typewriter" and wi > cur_i:
                        parts.append(f"{{\\alpha&HFF&}}{txt}{{\\alpha&H00&}}")     # not typed yet (keeps layout)
                    elif w is cur and anim:
                        # The line keeps an invisible copy (so nothing else moves); the visible word is a
                        # separate event centred on that copy and scaled around its own centre.
                        parts.append(f"{{\\alpha&HFF&}}{txt}{{\\alpha&H00&}}")
                        line_txt = " ".join(_word_text(x, upper) for x in line)
                        before = " ".join(_word_text(x, upper) for x in line[:wj])
                        lw = text_width(line_txt, pr.family, blk.em)
                        x0 = 540 - lw / 2 + (text_width(before + " ", pr.family, blk.em) if before else 0.0)
                        pop_word = (txt, x0 + text_width(_word_text(w, upper), pr.family, blk.em) / 2, y)
                    elif w is cur:
                        parts.append(f"{{\\c{_c(hi_c)}}}{txt}{{\\c{_c(pr.text)}}}")
                    elif a == "karaoke" and wi < cur_i:
                        parts.append(f"{{\\c{_c(hi_c)}}}{txt}{{\\c{_c(pr.text)}}}")   # already sung
                    else:
                        parts.append(txt)
                rows.append(" ".join(parts))
            # One event per line, each with its own centre point: lines are centred independently.
            for li, row in enumerate(rows):
                y = first_y + li * blk.line_h
                lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,"
                             f"{{\\an5\\pos(540,{y:.0f})\\fs{cfs:.1f}{blur}\\q2}}{row}")
            if pop_word:
                ptxt, px, py = pop_word
                lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Cap,,0,0,0,,"
                             f"{{\\an5\\pos({px:.0f},{py:.0f})\\fs{cfs:.1f}{blur}\\q2\\c{_c(hi_c)}{anim}}}{ptxt}")
            events.append({"start": round(start, 3), "end": round(end, 3), "word": cur["word"], "x": 540,
                           "y": round(blk.y), "em": round(blk.em, 1), "lines": len(blk.lines),
                           "line_h": round(blk.line_h, 1),
                           "rows": [[_word_text(w, upper) for w in line] for line in blk.lines],
                           "hi": [[w is cur for w in line] for line in blk.lines],
                           "animation": pr.animation})
            if emoji and (code := emoji_for(cur["word"])):
                size = int(blk.em * 1.1)
                emojis.append({"code": code, "start": start, "end": max(end, start + 0.6), "size": size,
                               "x": 540 - size // 2, "y": int(blk.y - blk.height / 2 - size - 12)})
    title = None
    if hook and title_box != "none":
        boxed = title_box in ("white_box", "black_box")
        em = 64 if boxed else 74
        hb = layout_chunk([{"word": t} for t in hook.split()], Preset("hook", "", pr.family, em, upper, "FFFFFF",
                          pr.highlight, "000000", 8, 5, 90, 100, 99), Placement(y=pl.hook_y), upper)
        if boxed:       # the box adds 2x18 px padding per line; keep the whole title inside the safe area
            hb.line_h += 36
            top = hb.y - hb.height / 2
            hb.y += max(0.0, SAFE["top"] - top) - max(0.0, hb.y + hb.height / 2 - SAFE["bottom"])
        hfs = ass_size(pr.family, hb.em)
        hy0 = hb.y - hb.height / 2 + hb.line_h / 2
        sty = {"white_box": "HookWhiteBox", "black_box": "HookBlackBox"}.get(title_box, "Hook")
        anim = "" if boxed else "\\fscx118\\fscy118\\t(0,140,\\fscx100\\fscy100)"
        for li, line in enumerate(hb.lines):
            txt = " ".join(_esc(_word_text(w, upper)) for w in line)
            lines.append(f"Dialogue: 1,{_ts(0)},{_ts(hook_sec)},{sty},,0,0,0,,{{\\an5\\pos(540,{hy0 + li * hb.line_h:.0f})"
                         f"\\fs{hfs:.1f}{'' if boxed else chr(92) + 'blur1.5'}\\q2{anim}\\fad(0,180)}}{txt}")
        title = {"text": hook, "box": title_box, "y": round(hb.y), "h": round(hb.height), "end": hook_sec}
    for tl in (text_layers or [])[:30]:
        txt = _esc(str(tl.get("text") or "").strip())[:200]
        if not txt:
            continue
        a, b = float(tl.get("start", 0)), float(tl.get("end", duration or 3))
        if b <= a:
            continue
        x, y = max(0.0, min(1.0, float(tl.get("x", 0.5)))) * W, max(0.0, min(1.0, float(tl.get("y", 0.3)))) * H
        fam = tl.get("font") if tl.get("font") in FONTS else pr.family
        fs_ = ass_size(fam, max(24, min(220, float(tl.get("size", 72)))))
        box = tl.get("box") or "outline"
        sty = {"white_box": "HookWhiteBox", "black_box": "HookBlackBox"}.get(box, "Hook")
        col = re.sub(r"[^0-9A-Fa-f]", "", str(tl.get("color") or ""))[:6]
        colour = "" if box in ("white_box", "black_box") or len(col) != 6 else f"\\1c{_c(col)}"
        rot = float(tl.get("rotate", 0) or 0)
        lines.append(f"Dialogue: 3,{_ts(a)},{_ts(b)},{sty},,0,0,0,,{{\\an5\\pos({x:.0f},{y:.0f})\\fn{fam}\\fs{fs_:.1f}"
                     f"{colour}{f'{chr(92)}frz{-rot:.1f}' if rot else ''}\\q2}}{txt.replace(chr(10), chr(92) + 'N')}")
    if credit:
        end = duration if duration else 3600
        cy = {"top": SAFE["top"] + 26, "bottom": SAFE["bottom"] - 30}.get(credit_pos or "", pl.credit_y)
        lines.append(f"Dialogue: 2,{_ts(0)},{_ts(end)},Credit,,0,0,0,,{{\\an5\\pos(540,{cy:.0f})\\q2}}"
                     f"{_esc(credit)}")
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    out.with_suffix(".events.json").write_text(json.dumps(events), encoding="utf-8")
    return CaptionTrack(out, bleeps, emojis, events, title)
