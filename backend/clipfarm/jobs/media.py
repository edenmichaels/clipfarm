"""Media fetching via yt-dlp (bundled as a Python dependency).

Strategy for long VODs (disk-friendly):
  1. download AUDIO ONLY of the VOD -> run highlight detection on it
  2. download only the VIDEO SECTIONS around detected moments
Clips (≤60 s) are downloaded whole.
Every call is preceded by compliance.assert_can_clip() in the job handler.
"""
from __future__ import annotations

from pathlib import Path


def _ydl(opts: dict):
    import yt_dlp  # type: ignore

    from .. import proc
    from ..editing.ffmpeg_util import ffmpeg_bin
    proc.install_guard()            # yt-dlp starts ffmpeg itself: make sure it never opens a console window
    base = {"quiet": True, "no_warnings": True, "noprogress": True, "retries": 5,
            "concurrent_fragment_downloads": 4, "overwrites": False}
    ff = ffmpeg_bin()
    if Path(ff).is_file():
        base["ffmpeg_location"] = str(Path(ff).parent)
    return yt_dlp.YoutubeDL({**base, **opts})


def download_clip(url: str, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / f"{name}.%(ext)s")
    with _ydl({"outtmpl": tmpl, "format": "best[height<=1080]/best", "merge_output_format": "mp4"}) as y:
        y.extract_info(url, download=True)
    return _find_output(out_dir, name)


def _find_output(out_dir: Path, name: str) -> Path:
    """yt-dlp may pick the extension; return whatever video file it wrote."""
    for f in sorted(out_dir.glob(f"{name}.*")):
        if f.suffix in (".mp4", ".mkv", ".webm", ".mov"):
            return f
    raise FileNotFoundError(f"Download produced no video for {name}")


def download_audio(url: str, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / f"{name}.audio.%(ext)s")
    with _ydl({"outtmpl": tmpl, "format": "bestaudio/worst"}) as y:
        info = y.extract_info(url, download=True)
        return Path(y.prepare_filename(info))


def download_section(url: str, out_dir: Path, name: str, start: float, end: float, low_res: bool = False) -> Path:
    """Fetch only [start, end] seconds of a VOD (yt-dlp uses ffmpeg under the hood).
    low_res: the smallest video (e.g. Twitch 160p) -- enough for motion/menu detection, ~1/30th the size."""
    from yt_dlp.utils import download_range_func  # type: ignore
    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / f"{name}.%(ext)s")
    fmt = "worst[height>=144][vcodec!=none]/worst[vcodec!=none]/worst" if low_res else "best[height<=1080]/best"
    with _ydl({"outtmpl": tmpl, "format": fmt,
               "download_ranges": download_range_func(None, [(start, end)]),
               "force_keyframes_at_cuts": True, "merge_output_format": "mp4"}) as y:
        y.extract_info(url, download=True)
    return _find_output(out_dir, name)
