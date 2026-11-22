"""Bundled sound-effects pack: original sounds synthesised here (no samples from anywhere), released CC0.

Each effect is generated from numpy oscillators/noise the first time it's needed and written as a 48 kHz WAV.
Your own SFX folder (Settings › Music) can be added too; you're responsible for those licences.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

from .. import config, db

SR = 48000
LICENCE = "CC0 1.0 (original sound effect synthesised by ClipFarm; no attribution needed)"
LICENCE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
LOCAL_LICENCE = "Your own file: you confirmed you have the rights to use it"


def _t(sec: float) -> np.ndarray:
    return np.arange(int(SR * sec)) / SR


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(0, t - attack) / max(decay, 1e-4))


def _noise(sec: float, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(int(SR * sec))


def _lowpass(x: np.ndarray, alpha: np.ndarray | float) -> np.ndarray:
    """One-pole low-pass; alpha may vary over time (0..1, higher = brighter)."""
    a = np.broadcast_to(np.asarray(alpha, dtype=np.float64), x.shape)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(x.size):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    return y


def whoosh() -> np.ndarray:
    n = _noise(0.55, 1)
    t = _t(0.55)
    sweep = 0.02 + 0.25 * np.sin(np.pi * t / 0.55) ** 2
    return _lowpass(n, sweep) * np.sin(np.pi * t / 0.55) ** 1.5 * 2.2


def boom() -> np.ndarray:
    t = _t(1.4)
    f = 38 + 60 * np.exp(-t * 9)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t.size, 0.004, 0.45)
    crack = _lowpass(_noise(1.4, 2), 0.35) * _env(t.size, 0.001, 0.12)
    rumble = _lowpass(_noise(1.4, 3), 0.01) * _env(t.size, 0.02, 0.6) * 6
    return np.tanh(1.6 * (body + 0.7 * crack + rumble))


def impact() -> np.ndarray:
    t = _t(0.9)
    f = 45 + 140 * np.exp(-t * 30)
    return np.tanh(2.2 * np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t.size, 0.002, 0.25))


def pop() -> np.ndarray:
    t = _t(0.12)
    f = 1400 * np.exp(-t * 18) + 380
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t.size, 0.001, 0.03)


def ding() -> np.ndarray:
    t = _t(1.3)
    tone = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d)
               for f, a, d in ((1318.5, 1.0, 3.2), (2637, 0.35, 5.0), (3951, 0.18, 7.0), (1975.5, 0.25, 4.0)))
    return tone * _env(t.size, 0.002, 10)


def riser() -> np.ndarray:
    t = _t(1.6)
    f = 180 * (2 ** (t / 1.6 * 3))
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.35
    air = _lowpass(_noise(1.6, 4), 0.03 + 0.4 * (t / 1.6) ** 2) * 1.6
    return (tone + air) * (t / 1.6) ** 2


def bonk() -> np.ndarray:
    t = _t(0.35)
    f = 260 * np.exp(-t * 6)
    knock = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t.size, 0.001, 0.06)
    click = _lowpass(_noise(0.35, 5), 0.6) * _env(t.size, 0.0005, 0.006)
    return np.tanh(2 * (knock + 0.5 * click))


def rewind() -> np.ndarray:
    t = _t(0.7)
    f = 900 + 700 * np.sin(2 * np.pi * 9 * t) * np.exp(-t * 2)
    tone = np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR)) * 0.25
    return _lowpass(tone + 0.4 * _noise(0.7, 6), 0.25) * _env(t.size, 0.01, 0.35)


def shutter() -> np.ndarray:
    t = _t(0.16)
    a = _lowpass(_noise(0.16, 7), 0.7) * _env(t.size, 0.0005, 0.01)
    b = np.roll(_lowpass(_noise(0.16, 8), 0.5) * _env(t.size, 0.0005, 0.015), int(SR * 0.06))
    return a + b


def sad_horn() -> np.ndarray:
    out = []
    for f0, d in ((392, 0.32), (370, 0.32), (349, 0.32), (330, 0.9)):
        t = _t(d)
        f = f0 * (1 + 0.012 * np.sin(2 * np.pi * 5.5 * t)) * (1 - (0.04 * t / d if d > 0.5 else 0))
        ph = 2 * np.pi * np.cumsum(f) / SR
        tone = np.tanh(1.8 * (np.sin(ph) + 0.5 * np.sin(2 * ph) + 0.25 * np.sin(3 * ph)))
        out.append(tone * _env(t.size, 0.02, d * 0.9))
    return _lowpass(np.concatenate(out), 0.12) * 1.4


SFX = {   # id: (title, generator, tags)
    "whoosh": ("Whoosh", whoosh, ["zoom", "transition"]),
    "boom": ("Boom", boom, ["explosion", "action"]),
    "impact": ("Impact hit", impact, ["reaction", "action", "rage"]),
    "pop": ("Pop", pop, ["funny", "text"]),
    "ding": ("Ding", ding, ["clutch", "wholesome", "success"]),
    "riser": ("Riser", riser, ["build-up", "clutch"]),
    "bonk": ("Bonk", bonk, ["fail", "funny"]),
    "rewind": ("Rewind", rewind, ["funny", "replay"]),
    "shutter": ("Camera shutter", shutter, ["freeze", "replay"]),
    "sad_horn": ("Sad horn", sad_horn, ["fail", "ironic"]),
}


def sfx_dir() -> Path:
    return config.app_data_dir() / "sfx" / "bundled"


def _write(path: Path, x: np.ndarray) -> None:
    x = x / max(1e-6, float(np.max(np.abs(x)))) * 0.89
    pcm = (x * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def ensure() -> list[dict]:
    """Write any missing bundled effects; return the manifest."""
    d = sfx_dir()
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for key, (title, fn, tags) in SFX.items():
        p = d / f"{key}.wav"
        if not p.exists():
            _write(p, fn())
        out.append({"id": key, "title": title, "path": str(p), "tags": tags, "licence": LICENCE,
                    "licence_url": LICENCE_URL, "source": "bundled"})
    (d / "manifest.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def listing() -> list[dict]:
    items = ensure()
    local = (db.get_setting("music", {}) or {}).get("sfx_dir")
    if local and Path(local).is_dir():
        for p in sorted(Path(local).iterdir()):
            if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".ogg", ".flac"):
                items.append({"id": f"local:{p.name}", "title": p.stem.replace("_", " "), "path": str(p),
                              "tags": [p.stem.lower()], "licence": LOCAL_LICENCE, "source": "local"})
    return items


def get(sid: str) -> dict | None:
    return next((s for s in listing() if s["id"] == sid), None)


def licensed(path: str | Path) -> bool:
    """Only bundled effects or files from your own SFX folder are ever mixed in."""
    p = Path(path).resolve()
    roots = [sfx_dir().resolve()]
    local = (db.get_setting("music", {}) or {}).get("sfx_dir")
    if local:
        roots.append(Path(local).resolve())
    return any(r == p.parent or r in p.parents for r in roots)
