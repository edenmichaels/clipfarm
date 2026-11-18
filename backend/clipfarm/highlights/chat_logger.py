"""Read-only Twitch chat logger (anonymous IRC login, no account needed).

Twitch has no official API for past-VOD chat, so while a target streamer is live we log chat to
<media>/chat/<login>-<date>.jsonl. The VOD analyser later aligns it with the VOD timeline using
the stream's started_at time. Read-only: this never sends messages.
"""
from __future__ import annotations

import json
import random
import socket
import threading
import time
from pathlib import Path


class ChatLogger(threading.Thread):
    def __init__(self, login: str, out_file: Path, stop_event: threading.Event):
        super().__init__(daemon=True, name=f"chat-{login}")
        self.login, self.out_file, self.stop_event = login.lower(), out_file, stop_event

    def run(self) -> None:
        self.out_file.parent.mkdir(parents=True, exist_ok=True)
        backoff = 2
        while not self.stop_event.is_set():
            try:
                self._session()
                backoff = 2
            except OSError:
                time.sleep(backoff)
                backoff = min(60, backoff * 2)

    def _session(self) -> None:
        with socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=300) as s, \
                open(self.out_file, "a", encoding="utf-8") as f:
            s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{self.login}\r\n".encode())
            buf = ""
            while not self.stop_event.is_set():
                data = s.recv(4096)
                if not data:
                    return
                buf += data.decode("utf-8", errors="ignore")
                *lines, buf = buf.split("\r\n")
                for line in lines:
                    if line.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n")
                    elif " PRIVMSG " in line:
                        msg = line.split(" PRIVMSG ", 1)[1].split(" :", 1)[-1]
                        f.write(json.dumps({"t": time.time(), "m": msg}) + "\n")
                f.flush()


def load_chat(path: Path, broadcast_start_epoch: float) -> list[tuple[float, str]]:
    """Return [(seconds_since_broadcast_start, message)] for chat_series()."""
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                out.append((row["t"] - broadcast_start_epoch, row["m"]))
            except (ValueError, KeyError):
                continue
    return out
