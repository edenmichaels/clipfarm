"""Fill in a new project automatically: rewards account, posting accounts (limited to each campaign's
allowed platforms) and every streamer's campaign rules. The user only opens a setting to change it.

The last project's choices (run length, clips per day, platforms, look) become the next one's defaults.
"""
from __future__ import annotations

import json

from .. import db
from .forecast import ACCOUNT_PLATFORM
from .matching import _brief, active_campaigns_for

PLATFORM_LABEL = {"tiktok": "TikTok", "youtube_shorts": "Shorts", "instagram_reels": "Reels", "x": "X"}
ALL_PLATFORMS = list(PLATFORM_LABEL)

BASE_DEFAULTS = {
    "duration_sec": 86400, "clips_per_day": 8, "platforms": ["tiktok", "youtube_shorts", "instagram_reels"],
    "clip": {"min_sec": 20, "max_sec": 45}, "captions": {"style": "bold_pop"}, "layout": "auto", "zoom": True,
    "progress_bar": True, "auto_approve": False, "posting": "manual",
}


def defaults() -> dict:
    """Sensible defaults, overridden by whatever the user chose last time."""
    last = db.get_setting("last_project") or {}
    out = {**BASE_DEFAULTS, **{k: v for k, v in last.items() if k in BASE_DEFAULTS}}
    return out


def remember(choices: dict) -> None:
    db.set_setting("last_project", {k: v for k, v in choices.items() if k in BASE_DEFAULTS})


def rewards_account() -> dict:
    return {"site": "Whop / Content Rewards", "username": db.get_setting("content_rewards_username")}


def posting_accounts() -> list[dict]:
    rows = db.q("SELECT * FROM accounts ORDER BY platform, id")
    back = {v: k for k, v in ACCOUNT_PLATFORM.items()}     # youtube -> youtube_shorts
    return [{"id": a["id"], "platform": back.get(a["platform"], a["platform"]), "handle": a["handle"],
             "connected": (a.get("oauth_status") or "connected") == "connected"} for a in rows]


def fill(streamer_ids: list[int], platforms: list[str] | None = None) -> dict:
    """Everything the Review step shows. platforms = what the user wants to post to (default: last time)."""
    d = defaults()
    wanted = [p for p in (platforms or d["platforms"]) if p in ALL_PLATFORMS]
    accounts = posting_accounts()
    streamers, used_plats, campaign_ids = [], set(), set()
    for sid in streamer_ids:
        s = db.q1("SELECT id, display_name, login, platform FROM streamers WHERE id=?", (sid,))
        if not s:
            continue
        camps = active_campaigns_for(sid)
        camp = camps[0] if camps else None
        if camp:
            allowed = json.loads(camp.get("allowed_platforms") or "[]") or ALL_PLATFORMS
            plats = [p for p in wanted if p in allowed]
            campaign_ids.add(camp["id"])
            b = _brief(camp)
            rules = {"hashtags": b["required_tags"], "credit": b["required_credit"], "disclosure": b["disclosure"],
                     "on_screen": b["required_onscreen"], "allowed_platforms": allowed}
        else:
            plats, rules, b = wanted, None, None
        accts = [a for a in accounts if a["platform"] in plats]
        used_plats.update(a["platform"] for a in accts)
        streamers.append({"streamer_id": sid, "name": s["display_name"] or s["login"], "campaign": b,
                          "rules": rules, "platforms": plats, "accounts": accts,
                          "skipped_platforms": [p for p in wanted if p not in plats]})
    rewards = rewards_account()
    plat_names = [PLATFORM_LABEL[p] for p in ALL_PLATFORMS if p in used_plats]
    n_s, n_c = len(streamers), len(campaign_ids)
    summary = " · ".join(x for x in [
        f"{n_s} streamer{'s' if n_s != 1 else ''}",
        f"{n_c} campaign{'s' if n_c != 1 else ''}",
        f"posting to {', '.join(plat_names)}" if plat_names else "no posting accounts yet",
        f"Whop: {rewards['username']}" if rewards["username"] else "",
    ] if x)
    return {"summary": summary, "rewards_account": rewards, "accounts": accounts, "streamers": streamers,
            "defaults": d, "platforms": wanted}


def output_settings(choices: dict, filled: dict) -> dict:
    """What gets stored on the project: the user's choices plus the automatic per-streamer accounts/rules."""
    out = {k: choices.get(k, v) for k, v in BASE_DEFAULTS.items() if k not in ("duration_sec",)}
    out["rewards_account"] = filled["rewards_account"]["username"]
    # "Clips per day" is shared between the project's streamers (enforced by the engine's daily quotas).
    n = max(1, len(filled["streamers"]))
    cpd = int(choices.get("clips_per_day") or BASE_DEFAULTS["clips_per_day"])
    out["quotas"] = {str(s["streamer_id"]): {"clips_per_day": max(1, -(-cpd // n))} for s in filled["streamers"]}
    out["per_streamer"] = {str(s["streamer_id"]): {"program_id": (s["campaign"] or {}).get("id"),
                                                   "platforms": s["platforms"],
                                                   "account_ids": [a["id"] for a in s["accounts"]],
                                                   "rules": s["rules"]} for s in filled["streamers"]}
    return out
