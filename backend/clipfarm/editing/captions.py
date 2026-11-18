"""Animated word-by-word captions as an ASS subtitle file (rendered by FFmpeg's libass).

Words are grouped into short chunks (default 3 words). The chunk is shown while it's spoken and
the current word is highlighted + popped (scale animation) — the familiar short-form style.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class CaptionStyle:
    name: str
    font: str
    size: int
    primary: str          # ASS colour &HAABBGGRR
    highlight: str
    outline: str
    outline_w: int
    margin_v: int         # px from bottom (1920 canvas)
    uppercase: bool = True
    pop: bool = True


STYLES = {
    "bold_pop": CaptionStyle("bold_pop", "Anton", 112, "&H00FFFFFF", "&H0000E5FF", "&H00000000", 6, 620),
    "clean": CaptionStyle("clean", "Arial", 72, "&H00FFFFFF", "&H0000FF9C", "&H64000000", 4, 560, uppercase=False, pop=False),
    "neon": CaptionStyle("neon", "Anton", 104, "&H00FFF0F0", "&H00FF40FF", "&H00401040", 5, 640),
}


def _ts(sec: float) -> str:
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[dict], out: Path, style_name: str = "bold_pop", words_per_chunk: int = 3,
              width: int = 1080, height: int = 1920, offset: float = 0.0) -> Path:
    """words: [{"word","start","end"}] relative to the *source*; offset is subtracted so
    timestamps line up with the cut clip."""
    st = STYLES.get(style_name, STYLES["bold_pop"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st.font},{st.size},{st.primary},{st.highlight},{st.outline},&H96000000,-1,0,0,0,100,100,0,0,1,{st.outline_w},2,2,60,60,{st.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    ws = [{**w, "start": w["start"] - offset, "end": w["end"] - offset} for w in words if w["end"] - offset > 0]
    for i in range(0, len(ws), words_per_chunk):
        chunk = ws[i:i + words_per_chunk]
        chunk_end = chunk[-1]["end"]
        for j, cur in enumerate(chunk):
            # Each word gets its own event: whole chunk visible, current word highlighted.
            start = cur["start"]
            end = chunk[j + 1]["start"] if j + 1 < len(chunk) else chunk_end
            parts = []
            for k, w in enumerate(chunk):
                txt = _escape(w["word"].upper() if st.uppercase else w["word"])
                if k == j:
                    anim = r"\fscx115\fscy115\t(0,80,\fscx100\fscy100)" if st.pop else ""
                    parts.append(f"{{\\c{st.highlight}{anim}}}{txt}{{\\c{st.primary}\\fscx100\\fscy100}}")
                else:
                    parts.append(txt)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(max(end, start + 0.05))},Cap,,0,0,0,,{' '.join(parts)}")
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out
