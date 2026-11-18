"""Paths and default settings.

Everything user-specific lives under the app data dir:
  Windows: %APPDATA%/ClipFarm      macOS: ~/Library/Application Support/ClipFarm
The media folder is user-chosen in the setup wizard (defaults to <data>/media).
"""
from __future__ import annotations

import os
import platform
from pathlib import Path


def app_data_dir() -> Path:
    override = os.environ.get("CLIPFARM_DATA_DIR")
    if override:
        p = Path(override)
    elif platform.system() == "Windows":
        p = Path(os.environ.get("APPDATA", Path.home())) / "ClipFarm"
    elif platform.system() == "Darwin":
        p = Path.home() / "Library" / "Application Support" / "ClipFarm"
    else:
        p = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "clipfarm"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return app_data_dir() / "clipfarm.db"


# Defaults written to the settings table on first run. Users change them in Settings.
DEFAULT_SETTINGS: dict = {
    "setup_complete": False,
    "accent": "#ffd400",            # UI accent colour (Settings > General > Appearance)
    "disclaimer_accepted": False,
    "media_dir": None,                  # filled by setup wizard
    "storage_cap_gb": 50,
    "delete_sources_after_processing": True,
    "render": {"encoder": "auto", "quality": "high", "fps": 30, "width": 1080, "height": 1920},
    "clip": {"min_sec": 15, "max_sec": 60, "pad_before": 3.0, "pad_after": 2.0,
             "top_n_per_hour": 4, "score_threshold": 0.55},
    "captions": {"style": "bold_pop", "words_per_chunk": 3},
    "layout": "auto",                   # auto | split | crop | blur_fill
    "llm": {"provider": "ollama", "model": "llama3.1:8b",
            "base_url": "http://127.0.0.1:11434", "cloud_base_url": "https://api.openai.com/v1",
            "cloud_model": "gpt-4o-mini"},
    "transcription": {"enabled": True, "model": "small", "device": "auto"},
    "posting": {"mode": "manual", "posts_per_day_per_account": 3,
                "window_start_hour": 9, "window_end_hour": 22, "min_gap_minutes": 90},
    "block_unknown_permission": True,   # unknown streamers need explicit opt-in per streamer
    "ranking_refresh_minutes": 30,
}

API_HOST = "127.0.0.1"
API_PORT = int(os.environ.get("CLIPFARM_PORT", "47821"))
