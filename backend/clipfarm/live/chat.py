"""Tier 0: live chat signals from Twitch's anonymous, read-only IRC (never sends messages)."""
from __future__ import annotations

import random
import re
import socket
import threading
import time
from collections import deque

LAUGH = re.compile(r"\b(kekw|lul|lulw|omegalul|lmao+|lmfao|icant|pepelaugh|kekl|ahaha+|hahaha+|xd)\b|😂|🤣|💀", re.I)
CLIP_IT = re.compile(r"\bclip\s*(it|that|this)\b|\bclipped\b", re.I)
HYPE = re.compile(r"\b(pog+|poggers|pogchamp|w+|holy|no\s*way|wtf|omg)\b|‼|🔥", re.I)


class ChatStats:
    """Messages of the last 15 minutes plus a slowly updated 'normal rate' for this channel."""
    KEEP = 900

    def __init__(self) -> None:
        self.msgs: deque[tuple[float, str]] = deque()
        self.base_rate = None          # messages/sec this channel normally has (EWMA)
        self.lock = threading.Lock()

    def add(self, t: float, msg: str) -> None:
        with self.lock:
            self.msgs.append((t, msg))
            while self.msgs and self.msgs[0][0] < t - self.KEEP:
                self.msgs.popleft()

    def window(self, t0: float, t1: float) -> list[tuple[float, str]]:
        with self.lock:
            return [(t, m) for t, m in self.msgs if t0 <= t <= t1]

    def stats(self, now: float | None = None, span: float = 10.0) -> dict:
        """Rate/laughs/clip-its in the last `span` seconds compared with the channel's normal rate."""
        now = now or time.time()
        w = self.window(now - span, now)
        rate = len(w) / span
        if self.base_rate is None:
            self.base_rate = max(rate, 0.05)
        ratio = rate / max(self.base_rate, 0.05)
        # update the normal rate slowly, and barely during spikes (so a spike doesn't become "normal")
        a = 0.01 if ratio > 2 else 0.05
        self.base_rate = (1 - a) * self.base_rate + a * max(rate, 0.02)
        laughs = sum(1 for _, m in w if LAUGH.search(m))
        clipit = sum(1 for _, m in w if CLIP_IT.search(m))
        hype = sum(1 for _, m in w if HYPE.search(m))
        return {"rate": round(rate, 2), "base": round(self.base_rate, 3), "ratio": round(ratio, 2),
                "laugh_share": round(laughs / len(w), 2) if w else 0.0, "laughs": laughs, "clip_it": clipit,
                "hype": hype, "n": len(w)}

    def score(self, st: dict) -> float:
        """0-1 trigger strength from one stats() reading."""
        s = 0.0
        if st["ratio"] >= 2.5 and st["n"] >= 6:
            s += min(0.5, (st["ratio"] - 2.5) / 6 + 0.25)
        s += min(0.4, st["laugh_share"] * 0.8) if st["laughs"] >= 3 else 0
        s += min(0.4, st["clip_it"] * 0.2)
        return round(min(1.0, s), 3)


class ChatReader(threading.Thread):
    """Reads #login chat into a ChatStats until stopped."""

    def __init__(self, login: str, stats: ChatStats, stop: threading.Event):
        super().__init__(daemon=True, name=f"live-chat-{login}")
        self.login, self.stats, self.stop = login.lower(), stats, stop

    def run(self) -> None:
        backoff = 2
        while not self.stop.is_set():
            try:
                self._session()
                backoff = 2
            except OSError:
                self.stop.wait(backoff)
                backoff = min(60, backoff * 2)

    def _session(self) -> None:
        with socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30) as s:
            s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{self.login}\r\n".encode())
            buf = ""
            while not self.stop.is_set():
                try:
                    data = s.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    return
                buf += data.decode("utf-8", errors="ignore")
                *lines, buf = buf.split("\r\n")
                for line in lines:
                    if line.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n")
                    elif " PRIVMSG " in line:
                        self.stats.add(time.time(), line.split(" PRIVMSG ", 1)[1].split(" :", 1)[-1])
