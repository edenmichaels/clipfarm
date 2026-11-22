"""Learning from you: thumbs up/down (+ quick "why?" tags) and real view counts reweight the signals.

After about 50 rated clips for a streamer (or a game), a small regularised logistic regression on the
moment's signal values finds which signals predicted the clips you liked and the ones that got views.
The learned weights are blended with the defaults (shrinkage), never go negative, and always sum to 1.
Settings shows the weights in use, per streamer / game, with "Reset to default".
"""
from __future__ import annotations

import json

import numpy as np

from .. import db
from .detect import DEFAULT_WEIGHTS

MIN_RATINGS = 50
TAGS = ["not funny", "too slow", "cut too early", "cut too late", "boring game part", "wrong moment",
        "great moment", "great timing"]
KEYS = list(DEFAULT_WEIGHTS)


def record(clip_id: int, rating: int, tags: list[str] | None = None) -> dict:
    tags = [t for t in (tags or []) if t in TAGS]
    db.ex("""INSERT INTO clip_feedback(clip_id, rating, tags) VALUES (?,?,?)
             ON CONFLICT(clip_id) DO UPDATE SET rating=excluded.rating, tags=excluded.tags,
             updated_at=datetime('now')""", (clip_id, int(np.sign(rating)), json.dumps(tags)))
    c = db.q1("SELECT streamer_id, game_id FROM clips WHERE id=?", (clip_id,))
    out = {"refit": []}
    for kind, sid in (("streamer", c and c["streamer_id"]), ("game", c and c["game_id"])):
        if not sid:
            continue
        scope = f"{kind}:{sid}"
        res = refit(scope)
        if res.get("fitted"):
            out["refit"].append(scope)
    return out


def _rows(scope: str) -> list[dict]:
    kind, sid = scope.split(":", 1)
    col = "c.streamer_id" if kind == "streamer" else "c.game_id"
    return db.q(f"""SELECT f.rating, m.signals,
                    (SELECT MAX(pm.views) FROM posts p JOIN post_metrics pm ON pm.post_id=p.id
                     WHERE p.clip_id=c.id) views
                    FROM clip_feedback f JOIN clips c ON c.id=f.clip_id JOIN moments m ON m.id=c.moment_id
                    WHERE {col}=?""", (int(sid),))


def refit(scope: str, min_ratings: int = MIN_RATINGS) -> dict:
    rows = _rows(scope)
    if len(rows) < min_ratings:
        return {"fitted": False, "ratings": len(rows), "needed": min_ratings}
    X = np.array([[json.loads(r["signals"] or "{}").get(k, 0.0) for k in KEYS] for r in rows], float)
    y = np.array([1.0 if r["rating"] > 0 else 0.0 for r in rows])
    views = np.array([r["views"] or 0 for r in rows], float)
    if (views > 0).sum() >= 10:            # clips that beat the median views count as "good" too
        med = np.median(views[views > 0])
        y = np.clip(0.7 * y + 0.3 * (views > med), 0, 1)
    Xs = (X - X.mean(0)) / (X.std(0) + 1e-6)
    w = np.zeros(len(KEYS))
    b = 0.0
    for _ in range(800):                   # L2-regularised logistic regression, gradient descent
        p = 1 / (1 + np.exp(-(Xs @ w + b)))
        w -= 0.1 * (Xs.T @ (p - y) / len(y) + 0.05 * w)
        b -= 0.1 * float((p - y).mean())
    learned = np.clip(w, 0, None)
    if learned.sum() <= 0:
        return {"fitted": False, "ratings": len(rows), "reason": "no signal predicted your ratings"}
    learned /= learned.sum()
    base = np.array([DEFAULT_WEIGHTS[k] for k in KEYS])
    trust = min(0.7, len(rows) / 300)      # more ratings, more trust in the learned weights
    final = (1 - trust) * base + trust * learned
    final /= final.sum()
    weights = {k: round(float(v), 3) for k, v in zip(KEYS, final)}
    db.set_setting(f"highlight_weights:{scope}", {"weights": weights, "ratings": len(rows), "trust": round(trust, 2)})
    return {"fitted": True, "ratings": len(rows), "weights": weights}


def weights_for(streamer_id: int | None, game_id: int | None) -> dict[str, float]:
    """Streamer-specific weights first, then game, then your global override, then the defaults."""
    for scope in (f"streamer:{streamer_id}", f"game:{game_id}"):
        w = db.get_setting(f"highlight_weights:{scope}")
        if w:
            return w["weights"]
    return {**DEFAULT_WEIGHTS, **(db.get_setting("highlight_weights") or {})}


def all_weights() -> dict:
    rows = db.q("SELECT key, value FROM settings WHERE key LIKE 'highlight_weights:%'")
    return {"default": DEFAULT_WEIGHTS, "global": {**DEFAULT_WEIGHTS, **(db.get_setting("highlight_weights") or {})},
            "learned": {r["key"].split(":", 1)[1]: json.loads(r["value"]) for r in rows}}


def reset(scope: str | None = None) -> None:
    if scope:
        db.ex("DELETE FROM settings WHERE key=?", (f"highlight_weights:{scope}",))
    else:
        db.ex("DELETE FROM settings WHERE key LIKE 'highlight_weights%'")
