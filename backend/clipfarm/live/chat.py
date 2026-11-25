"""Chat-first moment detection (v0.8.9): Twitch chat over anonymous, read-only IRC (never sends anything).

Tag groups (editable per streamer in Settings; you can add the channel's own emotes and inside jokes):
  clip   "clip it", "clip that", CLIP, clipped, "someone clip", "clip clip"              weight 3
  laugh  LMAO, LMFAO, LOL, KEKW, OMEGALUL, LUL, ICANT, dead, 💀, 😂, 🤣                    weight 2
  shock  "no way", NAHHH, BRO, ?????, W, L, POG, WHAT, 😭, OMG                            weight 1
Matching is case-insensitive and forgiving: "CLIPPPP", "c l i p", "LMAOOOO", "NAHHHHH" all match.
What counts is UNIQUE CHATTERS using tags in a ~10 s window (one person spamming counts once), compared with that
channel's own rolling baseline, so a busy chat doesn't fire all the time and a small chat still can.
Bots (Nightbot, StreamElements, ...) and !commands are ignored.
"""
from __future__ import annotations

import random
import re
import socket
import threading
import time
from collections import deque

DEFAULT_TAGS = {
    "clip": ["clip it", "clip that", "clip", "clipped", "someone clip", "clip clip"],
    "laugh": ["lmao", "lmfao", "lol", "kekw", "omegalul", "lul", "icant", "dead", "💀", "😂", "🤣"],
    "shock": ["no way", "nahhh", "bro", "?????", "w", "l", "pog", "what", "😭", "omg"],
}
WEIGHTS = {"clip": 3.0, "laugh": 2.0, "shock": 1.0}
GROUPS = ("clip", "laugh", "shock")
BOTS = {"nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "wizebot", "soundalerts", "sery_bot",
        "botrixoficial", "kofistreambot", "pokemoncommunitygame", "commanderroot", "lattemotte", "own3d",
        "tangiabot", "blerp", "creatisbot", "deepbot", "coebot", "phantombot", "streamstickers", "frostytoolsdotcom"}
WINDOW = 10.0
DEFAULT_TRIGGER = {"multiplier": 3.0, "min_unique": 5}


def squeeze(s: str) -> str:
    """lower case, letters spelled out with spaces joined ("c l i p" -> "clip"), repeats collapsed ("clippp" -> "clip")."""
    s = s.lower()
    s = re.sub(r"(?<!\S)(?:\w )+\w(?!\S)", lambda m: m.group(0).replace(" ", "") if len(m.group(0)) >= 5 else m.group(0), s)
    return re.sub(r"(.)\1+", r"\1", s)


class Tagger:
    """Compiled tag lists for one channel."""

    def __init__(self, tags: dict[str, list[str]] | None = None):
        self.tags = {g: list((tags or {}).get(g) or DEFAULT_TAGS[g]) for g in GROUPS}
        self.rx: dict[str, list[tuple[str, re.Pattern]]] = {}
        for g in GROUPS:
            pats = []
            for t in self.tags[g]:
                t = str(t).strip()
                if not t:
                    continue
                if not re.search(r"\w", t):                       # punctuation / emoji tags
                    if set(t) == {"?"} or set(t) == {"!"}:
                        pats.append((t, re.compile(re.escape(t[0]) + "{3,}")))
                    else:
                        pats.append((t, re.compile(re.escape(squeeze(t)))))
                else:
                    pats.append((t, re.compile(r"(?<![\w])" + re.escape(squeeze(t)) + r"(?![\w])")))
            self.rx[g] = pats

    def match(self, text: str) -> dict[str, str]:
        """{group: the tag that matched} for one message."""
        raw = text.lower()
        sq = squeeze(text)
        out = {}
        for g in GROUPS:
            for tag, rx in self.rx[g]:
                if rx.search(raw if not re.search(r"\w", tag) and set(tag) <= {"?", "!"} else sq):
                    out[g] = tag
                    break
        return out


def is_bot_or_command(user: str, text: str, streamer_login: str | None = None) -> bool:
    u = (user or "").lower()
    if u in BOTS or u.endswith("bot") or (streamer_login and u == streamer_login.lower()):
        return True                                            # bots, and the streamer's own messages
    t = text.strip()
    return t.startswith("!")                                   # !commands (anyone's, the streamer's too)


class ChatStats:
    """The last 15 minutes of chat with per-message tags, plus this channel's rolling baseline."""
    KEEP = 900

    def __init__(self, tags: dict | None = None, trigger: dict | None = None, login: str | None = None):
        self.tagger = Tagger(tags)
        self.trigger = {**DEFAULT_TRIGGER, **(trigger or {})}
        self.login = login
        self.msgs: deque[tuple[float, str, str, dict]] = deque()     # (t, user, text, {group: tag})
        self.base: float | None = None           # weighted unique tag-chatters per 10 s, normally
        self.lock = threading.Lock()

    def add(self, t: float, user: str, text: str) -> None:
        if is_bot_or_command(user, text, self.login):
            return
        with self.lock:
            self.msgs.append((t, (user or "").lower(), text, self.tagger.match(text)))
            while self.msgs and self.msgs[0][0] < t - self.KEEP:
                self.msgs.popleft()

    def window(self, t0: float, t1: float) -> list[tuple[float, str]]:
        with self.lock:
            return [(t, m) for t, _u, m, _g in self.msgs if t0 <= t <= t1]

    def counts(self, t0: float, t1: float) -> dict:
        """Unique chatters per tag group between t0 and t1 (each person once per group), and the top tags."""
        users: dict[str, set] = {g: set() for g in GROUPS}
        tags: dict[str, dict[str, set]] = {g: {} for g in GROUPS}
        allu: set = set()
        with self.lock:
            rows = [r for r in self.msgs if t0 <= r[0] <= t1]
        for _t, u, _m, gs in rows:
            allu.add(u)
            for g, tag in gs.items():
                users[g].add(u)
                tags[g].setdefault(tag, set()).add(u)
        tagged = set().union(*users.values())
        weighted = sum(WEIGHTS[g] * len(users[g]) for g in GROUPS)
        return {"clip": len(users["clip"]), "laugh": len(users["laugh"]), "shock": len(users["shock"]),
                "unique": len(tagged), "chatters": len(allu), "messages": len(rows), "weighted": weighted,
                "top": {g: sorted(((k, len(v)) for k, v in tags[g].items()), key=lambda x: -x[1])[:3] for g in GROUPS}}

    def stats(self, now: float | None = None, span: float = WINDOW) -> dict:
        """One reading of the last `span` seconds vs the baseline (updates the baseline unless it's a spike)."""
        now = now or time.time()
        c = self.counts(now - span, now)
        if self.base is None:
            self.base = max(0.5, c["weighted"])
        ratio = c["weighted"] / max(self.base, 0.5)
        if ratio < 2:                                         # learn "normal" from normal moments only
            self.base = 0.97 * self.base + 0.03 * max(0.3, c["weighted"])
        hit = c["unique"] >= int(self.trigger["min_unique"]) and ratio >= float(self.trigger["multiplier"])
        return {**c, "base": round(self.base, 2), "ratio": round(ratio, 2), "trigger": hit,
                "strength": round(min(1.0, ratio / (2 * float(self.trigger["multiplier"]))), 3) if hit else 0.0}

    def rate_per_min(self, now: float | None = None) -> float:
        now = now or time.time()
        return len(self.window(now - 60, now))

    def calm_after(self, peak: float, now: float) -> float | None:
        """First second after the peak where tag-chatters fall back near normal (or None if not yet)."""
        t = peak + 2
        while t + 3 <= now:
            c = self.counts(t, t + 3)
            if c["weighted"] * (WINDOW / 3) <= max(self.base or 0.5, 0.5) * 1.5:
                return t
            t += 1
        return None


def evidence(c: dict, seconds: float) -> str:
    """'34 people typed KEKW/💀 and 9 said "clip it" in 8 s'"""
    parts = []
    if c["laugh"]:
        tags = "/".join(t.upper() if t.isascii() else t for t, _ in c["top"]["laugh"][:2])
        parts.append(f"{c['laugh']} {'people' if c['laugh'] != 1 else 'person'} typed {tags}")
    if c["clip"]:
        parts.append(f"{c['clip']} said 'clip it'")
    if c["shock"]:
        tags = "/".join(t.upper() if t.isascii() else t for t, _ in c["top"]["shock"][:2])
        parts.append(f"{c['shock']} reacted {tags}")
    if not parts:
        return f"{c['chatters']} chatters in {seconds:.0f} s"
    head = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    return f"{head} in {seconds:.0f} s"


class ChatReader(threading.Thread):
    """Reads #login chat into a ChatStats until stopped (anonymous, read-only)."""

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
                        self.stats.add(time.time(), *parse_privmsg(line))


def parse_privmsg(line: str) -> tuple[str, str]:
    """(user, text) from ':nick!nick@nick.tmi.twitch.tv PRIVMSG #chan :text'."""
    head, _, text = line.partition(" PRIVMSG ")
    user = head.lstrip(":").split("!", 1)[0]
    return user, text.split(" :", 1)[-1]
