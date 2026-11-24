"""Tier 1: a rolling ~3-minute buffer of the live stream, saved as the HLS segments arrive (stream copy, no
re-encoding), plus cheap audio analysis: each new segment's audio is decoded at 16 kHz and scored by YAMNet.

The live playlist URL comes from yt-dlp (the same tool ClipFarm uses for VODs); segments are fetched with plain
HTTP. Old segments are deleted as new ones arrive, so disk use stays at roughly 3 minutes of video.
"""
from __future__ import annotations

import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

from .. import proc
from ..editing.ffmpeg_util import ffmpeg_bin

KEEP_SEC = 180
MAX_HEIGHT = 1080


@dataclass
class Segment:
    t0: float          # wall-clock start (epoch seconds)
    dur: float
    path: Path
    audio: dict | None = None      # {"laughter": [..per second..], "scream": [...]}


def live_playlist(login: str) -> str:
    """The live HLS media playlist for twitch.tv/<login>, best quality up to 1080p."""
    from ..jobs.media import _ydl
    with _ydl({"skip_download": True}) as y:
        info = y.extract_info(f"https://www.twitch.tv/{login}", download=False)
    fmts = [f for f in info.get("formats", []) if f.get("protocol", "").startswith("m3u8") and f.get("vcodec") != "none"
            and (f.get("height") or 0) <= MAX_HEIGHT]
    if not fmts:
        raise RuntimeError(f"{login} isn't live (no stream found).")
    best = max(fmts, key=lambda f: ((f.get("height") or 0), (f.get("fps") or 0), (f.get("tbr") or 0)))
    return best["url"]


def parse_playlist(text: str, base: str) -> list[tuple[int, float, str, float | None]]:
    """[(media sequence, duration, absolute url, program-date-time epoch or None)] for the segments in a playlist.
    Ad segments Twitch stitches in (titles with 'Amazon' / 'stitched') are skipped."""
    seq, out, dur, pdt, title = 0, [], None, None, ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            from datetime import datetime
            try:
                pdt = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00")).timestamp()
            except ValueError:
                pdt = None
        elif line.startswith("#EXTINF:"):
            d, _, title = line[8:].partition(",")
            dur = float(d)
        elif line and not line.startswith("#"):
            if dur is not None and "amazon" not in title.lower() and "stitched" not in title.lower():
                out.append((seq, dur, urllib.parse.urljoin(base, line), pdt))
            seq += 1
            dur, pdt, title = None, None, ""
    return out


def segment_audio(path: Path) -> dict | None:
    """YAMNet laughter/scream scores per second of one segment (audio only, 16 kHz)."""
    from ..highlights import audio_events as ae
    if not ae.available():
        return None
    child = ae.decode(path)
    raw = child.stdout.read() if child.stdout else b""
    child.wait()
    wave = np.frombuffer(raw, np.float32)
    if wave.size < ae.SR // 2:
        return None
    secs = max(1, int(round(wave.size / ae.SR)))
    ps = ae.per_second(ae.scores(wave), secs)
    return {g: [round(float(x), 3) for x in ps.get(g, [])] for g in ("laughter", "scream", "cheer")}


class LiveBuffer(threading.Thread):
    """Keeps the last KEEP_SEC of the stream on disk and scores each segment's audio."""

    def __init__(self, login: str, folder: Path, stop: threading.Event, analyse_audio: bool = True):
        super().__init__(daemon=True, name=f"live-buffer-{login}")
        self.login, self.folder, self.stop, self.analyse_audio = login, folder, stop, analyse_audio
        self.segments: list[Segment] = []
        self.lock = threading.Lock()
        self.error: str | None = None
        self._seen = -1

    def run(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        url, backoff = None, 5
        while not self.stop.is_set():
            try:
                if not url:
                    url = live_playlist(self.login)
                r = requests.get(url, timeout=15)
                if r.status_code in (403, 404, 410):
                    url = None                       # token expired or stream ended: ask again
                    self.stop.wait(backoff)
                    continue
                r.raise_for_status()
                for seq, dur, surl, pdt in parse_playlist(r.text, url):
                    if seq <= self._seen:
                        continue
                    self._seen = seq
                    self._fetch(seq, dur, surl, pdt)
                self._trim()
                self.error, backoff = None, 5
                self.stop.wait(2.0)
            except Exception as e:                  # noqa: BLE001  (network hiccups: retry quietly)
                self.error = str(e)[:200]
                url = None
                self.stop.wait(backoff)
                backoff = min(60, backoff * 2)
        self.clear()

    def _fetch(self, seq: int, dur: float, url: str, pdt: float | None) -> None:
        p = self.folder / f"{seq:010d}.ts"
        data = requests.get(url, timeout=20).content
        p.write_bytes(data)
        t0 = pdt or time.time() - dur
        seg = Segment(t0, dur, p)
        if self.analyse_audio:
            try:
                seg.audio = segment_audio(p)
            except Exception:                       # noqa: BLE001
                seg.audio = None
        with self.lock:
            self.segments.append(seg)

    def _trim(self, now: float | None = None) -> None:
        now = now or time.time()
        with self.lock:
            keep = [s for s in self.segments if s.t0 + s.dur >= now - KEEP_SEC]
            for s in self.segments:
                if s not in keep:
                    s.path.unlink(missing_ok=True)
            self.segments = keep

    def clear(self) -> None:
        with self.lock:
            for s in self.segments:
                s.path.unlink(missing_ok=True)
            self.segments = []

    # ---- reading the buffer --------------------------------------------------------------------------------
    def span(self) -> tuple[float, float] | None:
        with self.lock:
            if not self.segments:
                return None
            return self.segments[0].t0, self.segments[-1].t0 + self.segments[-1].dur

    def audio_peak(self, t0: float, t1: float) -> dict:
        """Max laughter/scream score between two wall-clock times."""
        out = {"laughter": 0.0, "scream": 0.0, "cheer": 0.0}
        with self.lock:
            segs = list(self.segments)
        for s in segs:
            if not s.audio:
                continue
            for g, vals in s.audio.items():
                for i, v in enumerate(vals):
                    if t0 <= s.t0 + i <= t1:
                        out[g] = max(out[g], v)
        return out

    def disk_bytes(self) -> int:
        with self.lock:
            return sum(s.path.stat().st_size for s in self.segments if s.path.exists())

    def cut(self, t0: float, t1: float, out: Path) -> tuple[float, float]:
        """Join the segments covering [t0, t1] into one MP4 (stream copy). Returns the real (start, end) covered."""
        with self.lock:
            segs = [s for s in self.segments if s.t0 + s.dur > t0 and s.t0 < t1]
        if not segs:
            raise RuntimeError("That moment is no longer in the buffer.")
        join_segments([s.path for s in segs], out)
        return segs[0].t0, segs[-1].t0 + segs[-1].dur


def join_segments(paths: list[Path], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in paths), encoding="utf-8")
    try:
        proc.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                  "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(out)], check=True, capture_output=True)
    finally:
        lst.unlink(missing_ok=True)
    return out


def frames(video: Path, dur: float, n: int = 3, width: int = 320) -> list[bytes]:
    """n small JPEG frames spread across the cut (for the judge)."""
    out = []
    for k in range(n):
        t = dur * (k + 1) / (n + 1)
        r = proc.run([ffmpeg_bin(), "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                      "-vf", f"scale={width}:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "6", "-"],
                     capture_output=True)
        if r.returncode == 0 and r.stdout:
            out.append(r.stdout)
    return out
