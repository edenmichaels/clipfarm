"""ClipFarm's bundled music: original tracks composed and synthesised here, in code.

Why: every track is ours (licence: CC0 / owned by the ClipFarm user), never uploaded or registered
anywhere, so Content ID / TikTok / Instagram can't match it to anyone else's rights claim. The beat
grid and the "drop" are known exactly, so edits line up with the music.

Each track is ~48–64 s, built from synthesised drums, bass, chords and leads. Deterministic: the same
code always produces the same audio.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SR = 44100
A4 = 440.0
NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def hz(name: str) -> float:
    """'A4' / 'C#3' -> Hz."""
    n, o = (name[:-1], int(name[-1]))
    return A4 * 2 ** ((NOTE[n] + 12 * (o + 1) - 69) / 12)


def chord(root: str, kind: str = "maj") -> list[float]:
    r = hz(root)
    iv = {"maj": [0, 4, 7], "min": [0, 3, 7], "maj7": [0, 4, 7, 11], "min7": [0, 3, 7, 10], "sus2": [0, 2, 7],
          "dim": [0, 3, 6], "pow": [0, 7, 12]}[kind]
    return [r * 2 ** (i / 12) for i in iv]


# ---- DSP helpers (numpy only) --------------------------------------------------------------------
def t_axis(sec: float) -> np.ndarray:
    return np.arange(int(sec * SR)) / SR


def env(n: int, a: float = 0.005, d: float = 0.1, s: float = 0.0, r: float = 0.05, hold: float | None = None) -> np.ndarray:
    a_n, d_n, r_n = max(1, int(a * SR)), max(1, int(d * SR)), max(1, int(r * SR))
    h_n = max(0, n - a_n - d_n - r_n) if hold is None else int(hold * SR)
    e = np.concatenate([np.linspace(0, 1, a_n), np.linspace(1, s, d_n), np.full(h_n, s), np.linspace(s, 0, r_n)])
    return np.pad(e, (0, max(0, n - e.size)))[:n]


def lowpass(x: np.ndarray, cutoff: float, q: float = 0.7) -> np.ndarray:
    """Smooth FFT low-pass (no ringing), applied to the whole buffer."""
    if x.size == 0:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1 / SR)
    X *= 1 / np.sqrt(1 + (f / cutoff) ** (4 * q))
    return np.fft.irfft(X, n=x.size)


def highpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1 / SR)
    X *= 1 - 1 / np.sqrt(1 + (f / cutoff) ** 4)
    return np.fft.irfft(X, n=x.size)


def saw(f: float | np.ndarray, t: np.ndarray, phase: float = 0.0) -> np.ndarray:
    ph = (np.cumsum(np.broadcast_to(f, t.shape)) / SR + phase) if np.ndim(f) else f * t + phase
    return 2 * (ph % 1) - 1


def square(f: float, t: np.ndarray, duty: float = 0.5) -> np.ndarray:
    return np.where((f * t) % 1 < duty, 1.0, -1.0)


def tri(f: float, t: np.ndarray) -> np.ndarray:
    return 2 * np.abs(2 * ((f * t) % 1) - 1) - 1


# ---- instruments ----------------------------------------------------------------------------------
_rng = np.random.default_rng(7)


def kick(dur=0.45, punch=1.0):
    t = t_axis(dur)
    f = 45 + 110 * np.exp(-t * 28)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
    click = _rng.standard_normal(t.size) * np.exp(-t * 400) * 0.3
    return np.tanh(1.6 * punch * (body + click))


def snare(dur=0.25):
    t = t_axis(dur)
    noise = highpass(_rng.standard_normal(t.size), 1500) * np.exp(-t * 18)
    tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30)
    return 0.6 * noise + 0.5 * tone


def clap(dur=0.3):
    t = t_axis(dur)
    n = highpass(_rng.standard_normal(t.size), 1100)
    e = sum(np.exp(-np.clip(t - o, 0, None) * 60) * (t >= o) for o in (0, 0.011, 0.022)) + np.exp(-t * 12) * 0.6
    return 0.5 * n * e


def hat(dur=0.05, open_=False):
    t = t_axis(0.3 if open_ else dur)
    return 0.32 * highpass(_rng.standard_normal(t.size), 7000) * np.exp(-t * (9 if open_ else 70))


def block(freq=1200, dur=0.08):
    t = t_axis(dur)
    return 0.5 * np.sin(2 * np.pi * freq * t) * np.exp(-t * 60)


def taiko(dur=0.7):
    t = t_axis(dur)
    f = 62 + 40 * np.exp(-t * 20)
    return np.tanh(1.8 * (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5)
                          + 0.25 * lowpass(_rng.standard_normal(t.size), 900) * np.exp(-t * 25)))


def crash(dur=1.8):
    t = t_axis(dur)
    return 0.25 * highpass(_rng.standard_normal(t.size), 4000) * np.exp(-t * 2.2)


def bass808(freq, dur, glide_from=None):
    t = t_axis(dur)
    f = np.full(t.size, freq) if glide_from is None else freq + (glide_from - freq) * np.exp(-t * 18)
    return np.tanh(2.2 * np.sin(2 * np.pi * np.cumsum(f) / SR) * env(t.size, 0.004, 0.2, 0.7, 0.08))


def pluck(freq, dur, bright=3000, decay=9.0):
    t = t_axis(dur)
    x = 0.6 * saw(freq, t) + 0.4 * square(freq, t, 0.3)
    return lowpass(x * np.exp(-t * decay), bright)


def pizz(freq, dur):
    t = t_axis(dur)
    return lowpass(tri(freq, t) * 0.8 + 0.2 * saw(freq * 2, t), 2600) * np.exp(-t * 14)


def tuba(freq, dur):
    t = t_axis(dur)
    return lowpass(square(freq, t, 0.45), 700) * env(t.size, 0.02, 0.12, 0.6, 0.06)


def kazoo(freq, dur):
    t = t_axis(dur)
    vib = 1 + 0.012 * np.sin(2 * np.pi * 5.5 * t)
    x = saw(freq * vib, t) * 0.6 + square(freq, t, 0.25) * 0.4
    return lowpass(x, 2200) * env(t.size, 0.03, 0.05, 0.8, 0.05)


def piano(freq, dur):
    t = t_axis(dur)
    x = sum((0.6 / k) * np.sin(2 * np.pi * freq * k * (1 + 0.0004 * k * k) * t) * np.exp(-t * (2.2 + 1.3 * k))
            for k in range(1, 7))
    return x * env(t.size, 0.004, 0.05, 1.0, 0.08)


def keys(freq, dur):
    """Soft electric-piano (lo-fi chords)."""
    t = t_axis(dur)
    x = np.sin(2 * np.pi * freq * t + 0.6 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 4))
    return x * np.exp(-t * 1.5) * env(t.size, 0.01, 0.1, 1.0, 0.1)


def pad(freqs, dur, cutoff=1800):
    t = t_axis(dur)
    x = sum(saw(f * d, t, ph) for f in freqs for d, ph in ((1.0, 0.0), (1.004, 0.3), (0.996, 0.6)))
    x = lowpass(x / (3 * len(freqs)), cutoff)
    return x * env(t.size, min(0.6, dur / 3), 0.2, 0.85, min(0.6, dur / 3))


def stab(freqs, dur):
    t = t_axis(dur)
    x = sum(saw(f * d, t) for f in freqs for d in (1.0, 1.007, 0.993)) / (3 * len(freqs))
    return lowpass(x, 3500) * env(t.size, 0.003, 0.15, 0.3, 0.05)


def bell(freq, dur):
    t = t_axis(dur)
    return (np.sin(2 * np.pi * freq * t) + 0.4 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-t * 3)) * np.exp(-t * 3.5)


def brass(freq, dur):
    t = t_axis(dur)
    return lowpass(saw(freq, t), 1400 + 900 * (1 - np.exp(-t * 6)).mean()) * env(t.size, 0.06, 0.1, 0.75, 0.12)


def riser(dur):
    t = t_axis(dur)
    x = _rng.standard_normal(t.size)
    out = np.zeros_like(x)
    step = SR // 10
    for i in range(0, x.size, step):
        c = 400 + 9000 * (i / x.size) ** 2
        out[i:i + step] = lowpass(x[i:i + step], c)
    return out * np.linspace(0, 0.35, t.size)


# ---- arrangement --------------------------------------------------------------------------------------
@dataclass
class Track:
    key: str
    title: str
    moods: list[str]
    bpm: float
    bars: int
    swing: float = 0.0
    beats_per_bar: int = 4
    drop_bar: int = 4                         # bar where the drop lands
    buf: np.ndarray = field(default=None, repr=False)

    def __post_init__(self):
        self.beat = 60 / self.bpm
        self.length = self.bars * self.beats_per_bar * self.beat + 2.0
        self.buf = np.zeros((int(self.length * SR), 2))

    def at(self, bar: float, beat: float = 0.0, sub: float = 0.0) -> float:
        """Time of bar/beat/sixteenth, with swing on off-beat eighths."""
        pos = bar * self.beats_per_bar + beat + sub
        frac = pos % 1
        if self.swing and abs(frac - 0.5) < 1e-6:
            pos += self.swing * 0.5
        return pos * self.beat

    def add(self, sound: np.ndarray, t: float, gain: float = 1.0, pan: float = 0.0):
        i = int(t * SR)
        if i >= self.buf.shape[0]:
            return
        s = sound[: self.buf.shape[0] - i] * gain
        lft, rgt = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        self.buf[i:i + s.size, 0] += s * lft * 1.41
        self.buf[i:i + s.size, 1] += s * rgt * 1.41

    def beats(self) -> list[float]:
        return [round(i * self.beat, 4) for i in range(self.bars * self.beats_per_bar)]

    def drop(self) -> float:
        return round(self.drop_bar * self.beats_per_bar * self.beat, 4)

    def master(self) -> np.ndarray:
        x = self.buf
        x = x - x.mean(axis=0)
        peak = np.abs(x).max() + 1e-9
        x = np.tanh(1.4 * x / peak) / np.tanh(1.4)       # gentle glue / limiting
        fade = int(1.5 * SR)
        x[-fade:] *= np.linspace(1, 0, fade)[:, None]
        return (x * 0.89).astype(np.float32)


def _goofy_footsteps() -> Track:
    tr = Track("goofy_footsteps", "Goofy Footsteps", ["funny"], 116, 24, swing=0.33, drop_bar=4)
    prog = [("C3", "maj"), ("A2", "min"), ("F2", "maj"), ("G2", "maj")]
    mel = ["E5", "G5", "A5", "G5", "E5", "D5", "C5", "D5", "E5", "E5", "D5", "G4", "A4", "C5", "D5", "C5"]
    for bar in range(tr.bars):
        root, kind = prog[bar % 4]
        r = hz(root)
        for b in range(4):
            tr.add(tuba(r if b % 2 == 0 else r * 1.5, tr.beat * 0.6), tr.at(bar, b), 0.55)
            if bar >= 2:
                tr.add(block(1500 if b % 2 else 1100), tr.at(bar, b, 0.5), 0.35, 0.4)
        if bar >= 4:
            for b in (1, 3):
                tr.add(clap(), tr.at(bar, b), 0.45)
            tr.add(kick(0.3, 0.7), tr.at(bar, 0), 0.6)
            for i in range(8):
                note = mel[(bar * 8 + i) % len(mel)]
                if (bar + i) % 3 != 2:
                    tr.add(pizz(hz(note), tr.beat * 0.45), tr.at(bar, i // 2, (i % 2) * 0.5), 0.33, -0.3)
        for c in chord(root[:-1] + "4", kind):
            tr.add(pluck(c, tr.beat * 0.3, 2500, 14), tr.at(bar, 1, 0.5), 0.12, 0.3)
            tr.add(pluck(c, tr.beat * 0.3, 2500, 14), tr.at(bar, 3, 0.5), 0.12, 0.3)
    return tr


def _banana_peel() -> Track:
    tr = Track("banana_peel", "Banana Peel", ["funny", "sad"], 132, 28, drop_bar=4)
    prog = [("F2", "maj"), ("C2", "maj"), ("D2", "min"), ("A#1", "maj")]
    mel = ["A4", "C5", "A4", "F4", "G4", "A4", "G4", "C4", "D4", "F4", "A4", "G4", "F4", "E4", "F4", "C4"]
    for bar in range(tr.bars):
        root, kind = prog[bar % 4]
        r = hz(root)
        tr.add(tuba(r, tr.beat * 0.5), tr.at(bar, 0), 0.6)
        tr.add(tuba(r * 1.5, tr.beat * 0.5), tr.at(bar, 2), 0.55)
        for b in (1, 3):
            for c in chord(root[:-1] + "4", kind):
                tr.add(pluck(c, tr.beat * 0.25, 2200, 16), tr.at(bar, b), 0.13)
        if bar >= 4:
            tr.add(kick(0.3, 0.8), tr.at(bar, 0), 0.6)
            tr.add(snare(), tr.at(bar, 2), 0.35)
            for i in range(4):
                tr.add(hat(), tr.at(bar, i, 0.5), 0.5, 0.5)
            for i in range(4):
                tr.add(kazoo(hz(mel[(bar * 4 + i) % len(mel)]), tr.beat * 0.85), tr.at(bar, i), 0.3, -0.2)
    return tr


def _full_send() -> Track:
    tr = Track("full_send", "Full Send", ["hype"], 144, 32, drop_bar=4)
    roots = ["C#2", "C#2", "A1", "B1"]
    bells = ["C#5", "E5", "G#5", "E5", "F#5", "E5", "C#5", "B4"]
    for bar in range(tr.bars):
        r = hz(roots[bar % 4])
        for i in range(8):
            tr.add(bell(hz(bells[(bar * 8 + i) % 8]), tr.beat * 0.5), tr.at(bar, i // 2, (i % 2) * 0.5), 0.12, 0.25)
        if bar < 4:
            if bar == 3:
                tr.add(riser(tr.beat * 4), tr.at(bar, 0), 0.8)
            continue
        if bar == 4 or bar % 8 == 4:
            tr.add(crash(), tr.at(bar, 0), 0.7)
        tr.add(kick(0.5, 1.2), tr.at(bar, 0), 0.9)
        tr.add(kick(0.5, 1.0), tr.at(bar, 2, 0.5), 0.7)
        tr.add(clap(), tr.at(bar, 2), 0.6)
        tr.add(snare(), tr.at(bar, 2), 0.35)
        roll = bar % 4 == 3
        for i in range(16 if roll else 8):
            tr.add(hat(), tr.at(bar, 0, i * (0.25 if roll else 0.5)), 0.45 + 0.1 * (i % 2), 0.35)
        tr.add(bass808(r, tr.beat * 1.8, glide_from=r * 1.5 if bar % 2 else None), tr.at(bar, 0), 0.75)
        tr.add(bass808(r * 1.19, tr.beat * 1.2), tr.at(bar, 2, 0.5), 0.6)
    return tr


def _redline() -> Track:
    tr = Track("redline", "Redline", ["hype", "epic"], 128, 32, drop_bar=8)
    prog = [("A1", "min"), ("F1", "maj"), ("C2", "maj"), ("G1", "maj")]
    for bar in range(tr.bars):
        root, kind = prog[bar % 4]
        r = hz(root)
        build = bar < 8
        for b in range(4):
            if bar >= 2:
                tr.add(kick(0.4, 1.1), tr.at(bar, b), 0.8 if not build else 0.55)
            tr.add(hat(), tr.at(bar, b, 0.5), 0.45, -0.4)
            if not build:
                tr.add(pluck(r * 2, tr.beat * 0.45, 900 + 300 * (bar % 4), 6), tr.at(bar, b, 0.5), 0.55)
                if b in (1, 3):
                    tr.add(clap(), tr.at(bar, b), 0.5)
        if build:
            tr.add(pad([f * 2 for f in chord(root, kind)], tr.beat * 4, 1200 + bar * 250), tr.at(bar, 0), 0.35)
            if bar == 7:
                tr.add(riser(tr.beat * 4), tr.at(bar, 0), 0.9)
        else:
            if bar % 8 == 0:
                tr.add(crash(), tr.at(bar, 0), 0.6)
            for b in (0, 1.5, 3):
                tr.add(stab([f * 4 for f in chord(root, kind)], tr.beat * 0.4), tr.at(bar, b), 0.3, 0.2)
    return tr


def _heist_mode() -> Track:
    tr = Track("heist_mode", "Heist Mode", ["tense"], 100, 24, drop_bar=8)
    roots = ["D2", "D2", "A#1", "A1"]
    for bar in range(tr.bars):
        r = hz(roots[bar % 4])
        tr.add(pad([r * 2, r * 2 * 2 ** (3 / 12), r * 3], tr.beat * 4, 900), tr.at(bar, 0), 0.3)
        for i in range(8):
            tr.add(pluck(r * (2 if i % 4 else 1), tr.beat * 0.4, 1200, 12), tr.at(bar, i // 2, (i % 2) * 0.5), 0.45)
        for i in range(4):
            tr.add(block(2400, 0.04), tr.at(bar, i), 0.25, 0.6)
        if bar >= 8:
            tr.add(kick(0.4), tr.at(bar, 0), 0.7)
            tr.add(snare(), tr.at(bar, 3, 0.5), 0.25)
            if bar % 4 == 0:
                tr.add(taiko(), tr.at(bar, 0), 0.6)
        if bar == 7:
            tr.add(riser(tr.beat * 4), tr.at(bar, 0), 0.6)
    return tr


def _last_stand() -> Track:
    tr = Track("last_stand", "Last Stand", ["epic"], 90, 20, drop_bar=8)
    prog = [("D2", "min"), ("A#1", "maj"), ("F2", "maj"), ("C2", "maj")]
    melody = ["A4", "D5", "F5", "E5", "D5", "C5", "A#4", "C5"]
    for bar in range(tr.bars):
        root, kind = prog[bar % 4]
        tr.add(pad([f * 2 for f in chord(root, kind)] + [hz(root) * 4], tr.beat * 4, 1500 if bar < 8 else 2600),
               tr.at(bar, 0), 0.45)
        tr.add(tuba(hz(root), tr.beat * 3.5), tr.at(bar, 0), 0.35)
        tr.add(taiko(), tr.at(bar, 0), 0.7 if bar >= 4 else 0.35)
        if bar >= 4:
            tr.add(taiko(0.4), tr.at(bar, 2, 0.5), 0.45)
            tr.add(taiko(0.4), tr.at(bar, 3), 0.5)
        if bar >= 8:
            if bar % 4 == 0:
                tr.add(crash(), tr.at(bar, 0), 0.55)
            tr.add(snare(), tr.at(bar, 1), 0.3)
            tr.add(snare(), tr.at(bar, 3), 0.3)
            for i in range(2):
                tr.add(brass(hz(melody[(bar * 2 + i) % 8]), tr.beat * 1.9), tr.at(bar, i * 2), 0.32, -0.2)
        if bar == 7:
            tr.add(riser(tr.beat * 4), tr.at(bar, 0), 0.7)
    return tr


def _afterglow() -> Track:
    tr = Track("afterglow", "Afterglow", ["chill"], 84, 20, swing=0.25, drop_bar=2)
    prog = [("D3", "maj7"), ("B2", "min7"), ("G2", "maj7"), ("A2", "min7")]
    vinyl = 0.012 * lowpass(_rng.standard_normal(int(tr.length * SR)), 3000)
    tr.buf[:, 0] += vinyl
    tr.buf[:, 1] += vinyl
    for bar in range(tr.bars):
        root, kind = prog[bar % 4]
        for c in chord(root, kind):
            tr.add(keys(c, tr.beat * 3.8), tr.at(bar, 0), 0.16)
        tr.add(keys(hz(root) / 2, tr.beat * 2), tr.at(bar, 0), 0.35)
        if bar >= 2:
            tr.add(kick(0.35, 0.6), tr.at(bar, 0), 0.55)
            tr.add(kick(0.35, 0.6), tr.at(bar, 2, 0.5), 0.4)
            tr.add(snare(), tr.at(bar, 1), 0.22)
            tr.add(snare(), tr.at(bar, 3), 0.22)
            for i in range(8):
                tr.add(hat(), tr.at(bar, i // 2, (i % 2) * 0.5), 0.25, 0.3)
    return tr


def _sad_trombone() -> Track:
    tr = Track("sad_trombone_hours", "Sad Trombone Hours", ["sad", "funny"], 72, 16, drop_bar=2)
    prog = [("A2", "min"), ("F2", "maj"), ("D2", "min"), ("E2", "maj")]
    mel = ["E5", "D5", "C5", "B4", "A4", "G#4", "A4", "E4"]
    for bar in range(tr.bars):
        root, kind = prog[bar % 4]
        for c in chord(root[:-1] + "3", kind):
            tr.add(piano(c, tr.beat * 3.6), tr.at(bar, 0), 0.22)
        tr.add(piano(hz(root), tr.beat * 3.6), tr.at(bar, 0), 0.3)
        if bar >= 2:
            for i in range(2):
                tr.add(piano(hz(mel[(bar * 2 + i) % 8]), tr.beat * 1.8), tr.at(bar, i * 2), 0.32, -0.15)
        if bar % 8 == 7:   # the "wah wah wah" fail sting
            for k, n in enumerate(["D#4", "D4", "C#4"]):
                tr.add(kazoo(hz(n), tr.beat * 0.9), tr.at(bar, k), 0.25)
            t = t_axis(tr.beat * 1.5)
            fall = hz("C4") * (1 - 0.06 * t / t[-1]) * (1 + 0.02 * np.sin(2 * np.pi * 6 * t))
            tr.add(lowpass(saw(fall, t), 1400) * env(t.size, 0.03, 0.1, 0.8, 0.3), tr.at(bar, 3), 0.25)
    return tr


TRACKS = {f.__name__.lstrip("_"): f for f in
          (_goofy_footsteps, _banana_peel, _full_send, _redline, _heist_mode, _last_stand, _afterglow, _sad_trombone)}
ENERGY = {"goofy_footsteps": 0.55, "banana_peel": 0.6, "full_send": 0.95, "redline": 0.9, "heist_mode": 0.6,
          "last_stand": 0.8, "afterglow": 0.3, "sad_trombone": 0.25}


def build(name: str) -> Track:
    global _rng
    _rng = np.random.default_rng(sum(map(ord, name)))
    return TRACKS[name]()


def render_all(out_dir, ffmpeg: str) -> list[dict]:
    """Write every track as .m4a + return manifest entries."""
    import subprocess
    import tempfile
    from pathlib import Path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name in TRACKS:
        tr = build(name)
        dst = out_dir / f"{tr.key}.m4a"
        if not dst.exists():
            pcm = (tr.master() * 32767).astype("<i2").tobytes()
            with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
                f.write(pcm)
            subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", f.name,
                            "-c:a", "aac", "-b:a", "192k", str(dst)], check=True)
            Path(f.name).unlink(missing_ok=True)
        manifest.append({"file": dst.name, "title": tr.title, "artist": "ClipFarm Originals", "moods": tr.moods,
                         "bpm": tr.bpm, "beats": tr.beats(), "drop_at": tr.drop(), "energy": ENERGY.get(name, 0.5),
                         "instrumental": True, "duration": round(tr.length, 2),
                         "licence": "CC0 1.0 (original music composed by ClipFarm; you own your copy)",
                         "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/", "attribution": None})
    return manifest
