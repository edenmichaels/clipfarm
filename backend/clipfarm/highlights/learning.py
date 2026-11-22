"""Learning from you (one system): thumbs up/down (+ quick "why?" tags), what you keep, delete and export in
the Editor, and real view counts reweight both the highlight signals and the virality score.

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
        "bad captions", "wrong music", "bad framing", "great moment", "great timing"]
KEYS = list(DEFAULT_WEIGHTS)


def record(clip_id: int, rating: int, tags: list[str] | None = None) -> dict:
    tags = [t for t in (tags or []) if t in TAGS]
    db.ex("""INSERT INTO clip_feedback(clip_id, rating, tags) VALUES (?,?,?)
             ON CONFLICT(clip_id) DO UPDATE SET rating=excluded.rating, tags=excluded.tags,
             updated_at=datetime('now')""", (clip_id, int(np.sign(rating)), json.dumps(tags)))
    return refit_for_clip(clip_id)


def refit_for_clip(clip_id: int) -> dict:
    """Refit the streamer's and the game's weights (highlights + virality) after new feedback or a signal."""
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
    """Every clip in scope you've told us something about: 👍/👎 (full weight), keep/export/delete in the
    Editor (lighter weight), and real views compared with the prediction."""
    kind, sid = scope.split(":", 1)
    col = "c.streamer_id" if kind == "streamer" else "c.game_id"
    rows = db.q(f"""SELECT c.id, f.rating, m.signals, c.virality_parts, c.expected_views,
                    (SELECT GROUP_CONCAT(s.kind) FROM clip_signals s WHERE s.clip_id=c.id) sigs,
                    (SELECT MAX(pm.views) FROM posts p JOIN post_metrics pm ON pm.post_id=p.id
                     WHERE p.clip_id=c.id) views
                    FROM clips c LEFT JOIN clip_feedback f ON f.clip_id=c.id LEFT JOIN moments m ON m.id=c.moment_id
                    WHERE {col}=?""", (int(sid),))
    out = []
    for r in rows:
        sigs = set((r["sigs"] or "").split(","))
        y = w = None
        if r["rating"] is not None and r["rating"] != 0:
            y, w = (1.0 if r["rating"] > 0 else 0.0), 1.0
        elif "delete" in sigs:
            y, w = 0.0, 0.6
        elif sigs & {"keep", "export"}:
            y, w = 1.0, 0.6
        if r["views"] and r["expected_views"]:
            beat = 1.0 if r["views"] >= r["expected_views"] else 0.0
            y, w = (beat, 0.8) if y is None else (0.7 * y + 0.3 * beat, w)
        if y is not None:
            out.append({**r, "y": y, "w": w})
    return out


def _fit(X: np.ndarray, y: np.ndarray, sw: np.ndarray, base: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Weighted, L2-regularised logistic regression -> non-negative weights blended with the defaults."""
    Xs = (X - X.mean(0)) / (X.std(0) + 1e-6)
    w = np.zeros(X.shape[1])
    b = 0.0
    sw = sw / sw.mean()
    for _ in range(800):
        p = 1 / (1 + np.exp(-(Xs @ w + b)))
        g = sw * (p - y)
        w -= 0.1 * (Xs.T @ g / len(y) + 0.05 * w)
        b -= 0.1 * float(g.mean())
    learned = np.clip(w, 0, None)
    if learned.sum() <= 0:
        return None
    learned /= learned.sum()
    trust = min(0.7, len(y) / 300)          # more feedback, more trust in the learned weights
    final = (1 - trust) * base + trust * learned
    return final / final.sum(), trust


def refit(scope: str, min_ratings: int = MIN_RATINGS) -> dict:
    rows = _rows(scope)
    if len(rows) < min_ratings:
        return {"fitted": False, "ratings": len(rows), "needed": min_ratings}
    y = np.array([r["y"] for r in rows], float)
    sw = np.array([r["w"] for r in rows], float)
    res: dict = {"fitted": False, "ratings": len(rows)}
    # highlight signals (clips that came from a detected moment)
    hl = [i for i, r in enumerate(rows) if r["signals"]]
    if len(hl) >= min_ratings:
        X = np.array([[json.loads(rows[i]["signals"]).get(k, 0.0) for k in KEYS] for i in hl], float)
        fit = _fit(X, y[hl], sw[hl], np.array([DEFAULT_WEIGHTS[k] for k in KEYS]))
        if fit:
            final, trust = fit
            weights = {k: round(float(v), 3) for k, v in zip(KEYS, final)}
            db.set_setting(f"highlight_weights:{scope}", {"weights": weights, "ratings": len(hl), "trust": round(trust, 2)})
            res.update(fitted=True, weights=weights)
    # virality score parts
    from ..scoring.virality import DEFAULT_WEIGHTS as VW
    vk = list(VW)
    vr = [i for i, r in enumerate(rows) if r["virality_parts"]]
    if len(vr) >= min_ratings:
        X = np.array([[json.loads(rows[i]["virality_parts"]).get(k, 50) / 100 for k in vk] for i in vr], float)
        fit = _fit(X, y[vr], sw[vr], np.array([VW[k] for k in vk]))
        if fit:
            final, trust = fit
            vw = {k: round(float(v), 3) for k, v in zip(vk, final)}
            db.set_setting(f"virality_weights:{scope}", {"weights": vw, "ratings": len(vr), "trust": round(trust, 2)})
            res.update(fitted=True, virality_weights=vw)
    if not res["fitted"]:
        res["reason"] = "no signal predicted your feedback yet"
    return res


def weights_for(streamer_id: int | None, game_id: int | None) -> dict[str, float]:
    """Streamer-specific weights first, then game, then your global override, then the defaults."""
    for scope in (f"streamer:{streamer_id}", f"game:{game_id}"):
        w = db.get_setting(f"highlight_weights:{scope}")
        if w:
            return w["weights"]
    return {**DEFAULT_WEIGHTS, **(db.get_setting("highlight_weights") or {})}


def all_weights() -> dict:
    from ..scoring.virality import DEFAULT_WEIGHTS as VW
    rows = db.q("SELECT key, value FROM settings WHERE key LIKE 'highlight_weights:%'")
    vrows = db.q("SELECT key, value FROM settings WHERE key LIKE 'virality_weights:%'")
    return {"default": DEFAULT_WEIGHTS, "global": {**DEFAULT_WEIGHTS, **(db.get_setting("highlight_weights") or {})},
            "learned": {r["key"].split(":", 1)[1]: json.loads(r["value"]) for r in rows},
            "virality_default": VW, "virality_global": {**VW, **(db.get_setting("virality_weights") or {})},
            "virality_learned": {r["key"].split(":", 1)[1]: json.loads(r["value"]) for r in vrows}}


def predicted_vs_actual(limit: int = 200) -> dict:
    """Clips with real views next to the views ClipFarm predicted from their score."""
    rows = db.q("""SELECT c.id, c.title, c.virality, c.expected_views, c.profit_platform platform,
                   (SELECT MAX(pm.views) FROM posts p JOIN post_metrics pm ON pm.post_id=p.id WHERE p.clip_id=c.id) views
                   FROM clips c WHERE c.expected_views IS NOT NULL ORDER BY c.id DESC LIMIT ?""", (limit,))
    rows = [r for r in rows if r["views"]]
    ratios = [r["views"] / max(1, r["expected_views"]) for r in rows]
    corr = None
    if len(rows) >= 5:
        v = np.array([r["virality"] or 0 for r in rows], float)
        a = np.log1p(np.array([r["views"] for r in rows], float))
        if v.std() > 0 and a.std() > 0:
            corr = round(float(np.corrcoef(v, a)[0, 1]), 2)
    return {"clips": rows, "n": len(rows), "median_ratio": round(float(np.median(ratios)), 2) if ratios else None,
            "score_vs_views_correlation": corr}


def reset(scope: str | None = None) -> None:
    if scope:
        db.ex("DELETE FROM settings WHERE key IN (?,?)", (f"highlight_weights:{scope}", f"virality_weights:{scope}"))
    else:
        db.ex("DELETE FROM settings WHERE key LIKE 'highlight_weights%' OR key LIKE 'virality_weights%'")
