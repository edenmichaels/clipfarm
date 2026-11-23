"""The viral model: how likely a moment is to go viral, 0-100, with the reason and each feature's part in it.

A small logistic model per game (runs in microseconds on CPU). It starts from DEFAULT weights, the research
on what goes viral, and is retrained as real data comes in:
- Twitch's most-viewed community clips per game (positives), random/mundane windows from the same VODs
  (negatives), refreshed daily (viral_data.py);
- your own posts' real views, your thumbs up/down and "why?" tags.
Training pulls the weights towards the data but keeps them near the defaults when there's little data (a
penalty on the distance from the defaults). Hard penalties sit on top: menus, loading/BRB, cutscenes or calm
talk with no reaction, sad/serious talk, calm driving and music on its own can never be top picks.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np

from .. import db
from .features import FEATURES, vector

# Starting knowledge (GTA 6 launch week and streamer clips generally): comedy and chaos driven by the streamer's
# reaction; spectacle only with a reaction; calm/sad/menus never.
DEFAULT = {
    "laugh": 1.6, "scream": 0.9, "disbelief": 0.7, "voice_jump": 0.6, "face": 0.5, "reaction": 1.4,
    "chat_busy": 0.9, "chat_laugh": 1.0, "chat_hype": 0.7, "clip_calls": 0.8,
    "event": 0.35, "n_events": 0.3, "explosion": 0.2, "crash": 0.25, "gunfire": 0.1, "police": 0.3,
    "fail_tag": 0.6, "stunt": 0.5, "npc": 0.6, "surprise": 0.6,
    "llm_fun": 1.4, "llm_serious": -2.2,
    "community": 1.2, "trend": 0.6,
    "menu": -2.5, "music_only": -2.0, "driving": -1.8, "sad": -2.6, "calm_talk": -1.6, "silence": -1.5,
    "event_x_reaction": 2.2, "explosion_x_laugh": 1.6, "surprise_x_reaction": 1.5, "funny_x_chat": 1.0,
}
DEFAULT_BIAS = -3.1
LABELS = {
    "laugh": "the streamer laughs", "scream": "the streamer screams", "disbelief": "disbelief (\"no way\", \"bro\")",
    "voice_jump": "the streamer's voice jumps", "face": "a big facecam reaction", "reaction": "a big reaction",
    "chat_busy": "chat is far busier than normal", "chat_laugh": "chat is laughing", "chat_hype": "chat is hyped",
    "clip_calls": "chat says \"clip it\"", "event": "something happens in the game", "n_events": "several things happen",
    "explosion": "an explosion", "crash": "a crash", "gunfire": "gunfire", "police": "police / wanted",
    "fail_tag": "a fail or glitch", "stunt": "a stunt", "npc": "an NPC fights back", "surprise": "a surprise",
    "llm_fun": "the AI rates it funny/chaotic", "llm_serious": "serious or sad talk", "community": "viewers clipped it",
    "trend": "matches this week's trending", "menu": "menus / loading / BRB", "music_only": "music with no talking",
    "driving": "calm driving", "sad": "sad or serious", "calm_talk": "calm talking", "silence": "silence",
    "event_x_reaction": "event + reaction", "explosion_x_laugh": "explosion/crash + laughter",
    "surprise_x_reaction": "surprise, then a reaction", "funny_x_chat": "funny and chat agrees",
}
TYPES = ["Funny", "Chaos/Fail", "Action + Reaction", "Rage", "Clutch/Skill", "Wholesome"]
OLD_TYPES = {"Action/Explosion": "Action + Reaction", "Fail": "Chaos/Fail", "Rage/Reaction": "Rage"}
MIN_EXAMPLES = 40          # per game before its own model is trained (the all-games model is used before that)


def canon_type(t: str | None) -> str | None:
    return OLD_TYPES.get(t, t) if t else t


# ---- model storage ---------------------------------------------------------------------------------------
def _default_model() -> dict:
    return {"weights": dict(DEFAULT), "bias": DEFAULT_BIAS, "trained": False, "n_pos": 0, "n_neg": 0,
            "metrics": {}, "trained_at": None}


def load(game_id: int | None) -> dict:
    for gid in ([game_id] if game_id else []) + [0]:
        try:
            r = db.q1("SELECT data FROM viral_models WHERE game_id=?", (gid,))
        except Exception:        # noqa: BLE001  (no database: evaluation scripts, tests) -> the default model
            r = None
        if r:
            m = json.loads(r["data"])
            m["weights"] = {**DEFAULT, **m.get("weights", {})}
            m["game_id"] = gid
            return m
    return {**_default_model(), "game_id": None}


def save(game_id: int, model: dict) -> None:
    db.ex("""INSERT INTO viral_models(game_id, data, trained_at) VALUES (?,?,datetime('now'))
             ON CONFLICT(game_id) DO UPDATE SET data=excluded.data, trained_at=excluded.trained_at""",
          (game_id, json.dumps(model)))


def reset(game_id: int | None = None) -> None:
    if game_id is None:
        db.ex("DELETE FROM viral_models")
    else:
        db.ex("DELETE FROM viral_models WHERE game_id=?", (game_id,))


# ---- scoring -----------------------------------------------------------------------------------------------
def hard_penalty(f: dict[str, float]) -> float:
    """Multiplier 0..1 for things that never go viral, whatever else is going on."""
    react = max(f["reaction"], f["chat_laugh"], f["llm_fun"])
    mult = 1.0
    if f["menu"] > 0.35 and react < 0.4:
        mult *= 0.15                        # menus, maps, loading, BRB
    if f["sad"] > 0.45 and f["laugh"] < 0.3:
        mult *= 0.2                         # sad / serious talk
    if f["driving"] > 0.4 and f["event"] < 0.3 and react < 0.4:
        mult *= 0.25                        # long calm driving
    if f["music_only"] > 0.5 and react < 0.3:
        mult *= 0.15                        # music on its own (intro, ads, loading)
    if f["calm_talk"] > 0.5 and react < 0.3 and f["event"] < 0.3:
        mult *= 0.3                         # calm talking / story / cutscene with no reaction
    if f["silence"] > 0.6:
        mult *= 0.2
    return mult


def score(f: dict[str, float], model: dict | None = None) -> dict:
    m = model or _default_model()
    w = m["weights"]
    contrib = {k: round(float(w.get(k, 0.0)) * float(f.get(k, 0.0)), 4) for k in FEATURES}
    z = m["bias"] + sum(contrib.values())
    p = 1 / (1 + math.exp(-z))
    mult = hard_penalty(f)
    s = int(round(100 * p * mult))
    return {"score": max(0, min(100, s)), "raw": round(p, 4), "penalty": mult, "contributions": contrib}


def moment_type(f: dict[str, float], llm_type: str | None = None) -> str:
    llm_type = canon_type(llm_type)
    if llm_type in TYPES:
        return llm_type
    funny = max(f["laugh"], f["chat_laugh"], f["llm_fun"] * 0.8)
    chaos = max(f["fail_tag"], f["npc"], f["stunt"] * 0.8) * max(f["reaction"], 0.5)
    action = f["event_x_reaction"] + 0.3 * f["event"]
    rage = f["scream"] * (1 - min(1.0, f["laugh"] * 2))
    clutch = f["chat_hype"] * (1 - f["laugh"])
    scores = {"Funny": funny, "Chaos/Fail": chaos, "Action + Reaction": action, "Rage": rage, "Clutch/Skill": clutch}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0.2 else "Wholesome"


def _when(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def reason(f: dict[str, float], result: dict, tags: list[dict], *, chat_ratio: float | None = None,
           trend_tag: str | None = None) -> str:
    """Plain English, e.g. 'Car explodes at 0:06, streamer screams and laughs, chat 6x normal, matches this
    week's trending "police chase"'."""
    from .events import LABEL
    bits = []
    if tags:
        t = tags[0]
        bits.append(f"{LABEL.get(t['tag'], t['tag']).split(' / ')[0].capitalize()} at {_when(t['t'])}")
    rx = [w for k, w in (("laugh", "laughs"), ("scream", "screams"), ("disbelief", "can't believe it"),
                         ("face", "reacts on cam")) if f.get(k, 0) >= 0.45]
    if rx:
        bits.append("streamer " + " and ".join(rx[:2]))
    if chat_ratio and chat_ratio >= 2:
        bits.append(f"chat {chat_ratio:.0f}× normal" + (" and laughing" if f["chat_laugh"] >= 0.4 else ""))
    elif f["chat_laugh"] >= 0.4:
        bits.append("chat laughing")
    if f["llm_fun"] >= 0.6:
        bits.append("the talk is funny/chaotic")
    if trend_tag and f["trend"] >= 0.3:
        bits.append(f"matches this week's trending \"{trend_tag}\"")
    if result["penalty"] < 1:
        neg = max(("menu", "sad", "driving", "music_only", "calm_talk", "silence"), key=lambda k: f.get(k, 0))
        bits.append(f"held back: {LABELS[neg]}")
    if not bits:
        top = sorted(result["contributions"].items(), key=lambda kv: -kv[1])[:2]
        bits = [LABELS[k] for k, v in top if v > 0] or ["nothing stands out"]
    s = ", ".join(bits)
    return s[0].upper() + s[1:]


def explain(f: dict[str, float], result: dict) -> list[dict]:
    """'Why this moment?': each feature's contribution, biggest first (positive and negative)."""
    rows = [{"feature": k, "label": LABELS[k], "value": f[k], "contribution": v}
            for k, v in result["contributions"].items() if abs(v) >= 0.01]
    rows.sort(key=lambda r: -abs(r["contribution"]))
    if result["penalty"] < 1:
        rows.insert(0, {"feature": "hard_penalty", "label": "never goes viral (menu, sad, calm...)",
                        "value": result["penalty"], "contribution": round(math.log(max(result["penalty"], 1e-3)), 3)})
    return rows


# ---- training ----------------------------------------------------------------------------------------------
def fit(X: np.ndarray, y: np.ndarray, sw: np.ndarray | None = None, prior: dict | None = None,
        prior_bias: float = DEFAULT_BIAS, strength: float | None = None, iters: int = 1500) -> tuple[dict, float]:
    """Weighted logistic regression pulled towards the prior weights (strong with little data)."""
    n = len(y)
    sw = np.ones(n) if sw is None else sw / sw.mean()
    w0 = np.array([(prior or DEFAULT).get(k, 0.0) for k in FEATURES])
    lam = strength if strength is not None else 8.0 / max(n, 1) ** 0.5
    w, b = w0.copy(), prior_bias
    pos = max(1e-6, float((y * sw).sum()))
    neg = max(1e-6, float(((1 - y) * sw).sum()))
    cw = np.where(y > 0.5, 0.5 * n / pos, 0.5 * n / neg) * sw      # balance positives and negatives
    lr = 0.5
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = cw * (p - y)
        w -= lr * (X.T @ g / n + lam * (w - w0))
        b -= lr * float(g.mean())
    return {k: round(float(v), 4) for k, v in zip(FEATURES, w)}, round(float(b), 4)


def precision_at(scores: np.ndarray, y: np.ndarray, k: int = 10) -> float:
    idx = np.argsort(-scores)[:k]
    return float((y[idx] > 0.5).mean()) if idx.size else 0.0


def auc(scores: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = scores[y > 0.5], scores[y <= 0.5]
    if not pos.size or not neg.size:
        return None
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def train(game_id: int | None = None, holdout_days: int = 1) -> dict:
    """Retrain from the viral examples. Hold out the most recent day(s) to measure accuracy honestly, then
    refit on everything. game_id=None trains the all-games model."""
    q = "SELECT features, label, weight, day, views, source_kind FROM viral_examples WHERE features IS NOT NULL"
    rows = db.q(q + (" AND game_id=?" if game_id else ""), (game_id,) if game_id else ())
    if len(rows) < (MIN_EXAMPLES if game_id else 20) or len({r["label"] > 0.5 for r in rows}) < 2:
        return {"trained": False, "examples": len(rows), "needed": MIN_EXAMPLES}
    X = np.array([vector(json.loads(r["features"])) for r in rows], np.float64)
    y = np.array([float(r["label"]) for r in rows])
    sw = np.array([float(r["weight"] or 1.0) for r in rows])
    days = sorted({r["day"] for r in rows if r["day"]})
    metrics: dict = {}
    if len(days) > holdout_days:
        test = np.array([r["day"] in days[-holdout_days:] for r in rows])
        if test.any() and (~test).any() and len(set(y[test] > 0.5)) == 2:
            base = load(game_id) if game_id else _default_model()
            w, b = fit(X[~test], y[~test], sw[~test], base.get("weights"), base.get("bias", DEFAULT_BIAS))
            st = X[test] @ np.array([w[k] for k in FEATURES]) + b
            sd = X[test] @ np.array([DEFAULT[k] for k in FEATURES]) + DEFAULT_BIAS
            metrics = {"holdout_days": days[-holdout_days:], "test_examples": int(test.sum()),
                       "auc": auc(st, y[test]), "precision_at_10": precision_at(st, y[test]),
                       "default_auc": auc(sd, y[test]), "default_precision_at_10": precision_at(sd, y[test])}
    w, b = fit(X, y, sw)
    model = {"weights": w, "bias": b, "trained": True, "n_pos": int((y > 0.5).sum()), "n_neg": int((y <= 0.5).sum()),
             "metrics": metrics, "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    save(game_id or 0, model)
    return {**model, "game_id": game_id or 0}


def trend_tags(game_id: int | None, days: int = 7) -> dict[str, float]:
    """This week's event tags among viral positives for the game: {tag: share 0..1}."""
    rows = db.q("""SELECT tags FROM viral_examples WHERE label > 0.5 AND tags IS NOT NULL
                   AND created_at >= datetime('now', ?)""" + (" AND game_id=?" if game_id else ""),
                (f"-{days} days", game_id) if game_id else (f"-{days} days",))
    counts: dict[str, int] = {}
    for r in rows:
        for t in json.loads(r["tags"]):
            counts[t] = counts.get(t, 0) + 1
    # your style references count too: tags you put on screenshots of clips you like (e.g. "fail", "rage")
    try:
        from .events import TAGS
        from ..edit import style_refs
        for t, n in style_refs.tag_counts().items():
            key = t.replace(" ", "_")
            if key in TAGS:
                counts[key] = counts.get(key, 0) + min(n, 3)
    except Exception:                      # noqa: BLE001  (no references table yet / no DB)
        pass
    if not counts:
        return {}
    top = max(counts.values())
    return {k: round(v / top, 3) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:6]}
