"""Starting data: the Content Rewards campaigns Eden joined on 19 Nov 2026 and his posting accounts.

This is a starting point, NOT a whitelist. Discovery, imports and auto-pilot add and rank other
campaigns and streamers on their own. Values marked "page" were read from each campaign page on
19 Nov 2026; everything else is flagged "needs confirming" in the Campaigns screen.

Change from the brief: KreekCraft, Dylan Page and AsianJeff are listed as prize pools in the brief,
but their campaign pages show per-view rates ($1/1K with per-clip caps), so they are seeded as
pay-per-view.
"""
from __future__ import annotations

import json
import sqlite3

from .fields import needs_confirming

SOURCE_NOTE = "Content Rewards campaign, joined Nov 2026"
CR = "https://contentrewards.com/c/campaigns/"
JOINED_AT = "2026-11-19"

# Each campaign: values read from its page ("page") and channel hints. A hint read from a URL on
# the campaign page is certain; a hint guessed from the name must be confirmed by Eden.
CAMPAIGNS: list[dict] = [
    dict(external_id="051002dd-5c46-43d0-a446-43f2957ba3a9", name="Dasgasd3m x RUCKUS | GTA 6",
         streamer_label="Dasgasd3m", campaign_type="pay_per_view", payout_per_1k=2.0, min_payout=10, max_payout=150,
         budget_total=1000, budget_remaining=1000, clippers_joined=0,
         allowed_platforms=["tiktok", "youtube_shorts"],
         rules="Clip Dom's funny and entertaining GTA 6 moments. GTA 6-specific posting accounts required.",
         hints={"twitch": ("dasgasd3m", "name")}),
    dict(external_id="954cf1f2-1a69-4f0f-b46c-18a9dfddb219", name="BrettLiftsXP GTA VI Clipping",
         streamer_label="BrettLiftsXP", campaign_type="pay_per_view", payout_per_1k=2.0, min_payout=2, max_payout=100,
         budget_total=1000, budget_remaining=868, clippers_joined=21,
         rules="Clips from recent streams only. Old clips from old streams are not accepted.",
         hints={"twitch": ("brettliftsxp", "name")}),
    dict(external_id="dd617572-f0f7-4754-8df5-4c0deb81763a", name="Lauren Hamden GTA 6 Clipping",
         streamer_label="Lauren Hamden", campaign_type="pay_per_view", payout_per_1k=1.5, min_payout=15,
         max_payout=250, budget_total=5000, budget_remaining=4492, clippers_joined=10,
         rules="Post on a dedicated account. Rules: https://reachclipping.com/Sisters",
         hints={"twitch": ("laurenhamdenn", "page")}),
    dict(external_id="df6de26f-86ec-4782-b818-1fc8053b99a4", name="TOMO GTA VI Clipping",
         streamer_label="TOMO", campaign_type="pay_per_view", payout_per_1k=1.5, min_payout=3.5, max_payout=150,
         budget_total=1000, budget_remaining=528, clippers_joined=21,
         rules="Clip Tomo's GTA VI YouTube livestreams and videos; drive people to subscribe on YouTube.",
         hints={"youtube": ("@Tomo", "page")}),
    dict(external_id="2eaa14fb-6927-4136-897e-b096e0d907d6", name="KreekCraft GTA 6 Clipping",
         streamer_label="KreekCraft", campaign_type="pay_per_view", payout_per_1k=1.0, min_payout=3, max_payout=300,
         budget_total=6824, budget_remaining=6824, clippers_joined=0,
         required_onscreen=["streamer_overlay"],
         rules="Every clip must include KreekCraft's stream overlay and drive viewers back to his livestream.",
         hints={"youtube": ("@KreekCraft", "name"), "twitch": ("kreekcraft", "name")}),
    dict(external_id="39753a1a-f1f3-491b-a8f6-0cfee0af0c97", name="Dylan Page GTA VI Marathon",
         streamer_label="Dylan Page", campaign_type="pay_per_view", payout_per_1k=1.0, min_payout=10, max_payout=250,
         budget_total=5000, budget_remaining=4988, clippers_joined=1,
         required_tags=["#DylanPage"], required_onscreen=["streamer_face"],
         allowed_platforms=["tiktok", "instagram_reels", "x", "youtube_shorts"],
         rules="120-hour GTA 6 marathon live on YouTube. Clips must show Dylan and include #DylanPage in the "
               "caption. Ends when the marathon ends.",
         hints={"youtube": ("@dylanpage", "name")}),
    dict(external_id="bbd3a963-9a23-457c-bc15-6697971d9902", name="AsianJeff GTA 6 Clipping",
         streamer_label="AsianJeff", campaign_type="pay_per_view", payout_per_1k=1.0, min_payout=10, max_payout=300,
         budget_total=2500, budget_remaining=2500, clippers_joined=0,
         allowed_platforms=["tiktok", "instagram_reels", "youtube_shorts", "x"],
         rules="Clip AsianJeff's GTA 6 playthrough (twitch.tv/asianjeff).",
         hints={"twitch": ("asianjeff", "page")}),
    dict(external_id="1d2bcb00-c904-415f-aa82-f95b96baad86", name="Kai Cenat $10,000 GTA 6 Pool",
         streamer_label="Kai Cenat", campaign_type="prize_pool", pool_size=10000, view_threshold=750_000,
         min_views=1000, clippers_joined=66,
         allowed_platforms=["tiktok", "youtube_shorts", "instagram_reels", "x"],
         rules="Your clips need 750K views in total to qualify; each clip needs 1K views to count. Paid after "
               "the pool ends. Full rules in the Google Doc linked on the campaign page.",
         hints={"twitch": ("kaicenat", "name")}),
    dict(external_id="a5c63fad-cd53-4b81-9305-4a2d1696872a", name="Lacy $12,500 GTA VI Marathon Pool",
         streamer_label="Lacy", campaign_type="prize_pool", pool_size=12500, view_threshold=750_000,
         clippers_joined=340, rules="750K total views to share the pool. Rules: https://reachclipping.com/LACYGTA",
         hints={"twitch": ("lacy", "name")}),
    dict(external_id="be9b123e-ea47-4305-b6c5-af81f503b247", name="YourRAGE $5,000 GTA 6 Pool",
         streamer_label="YourRAGE", campaign_type="prize_pool", pool_size=5000, view_threshold=500_000,
         clippers_joined=0, rules="500K total views to share the pool. Runs for YourRAGE's GTA 6 marathon.",
         hints={"twitch": ("yourragegaming", "page")}),
    dict(external_id="985dc674-c75e-44fc-ac87-1b1506324389", name="Clix $5,000 GTA VI Marathon Pool",
         streamer_label="Clix", campaign_type="prize_pool", pool_size=5000, view_threshold=400_000,
         clippers_joined=152, rules="400K total views to share the pool. Rules: https://reachclipping.com/ClixGTA",
         hints={"twitch": ("clix", "name")}),
    # Joined, but a music-sound campaign, not a streamer: never linked to a streamer.
    dict(external_id="40046522-56d9-45ad-9198-faea8eca90c6", name="Tokyo Girl (Passive) – GTA 6 sound",
         streamer_label=None, campaign_type="pay_per_view", payout_per_1k=0.7, min_payout=2, max_payout=35,
         budget_total=2000, budget_remaining=1912, clippers_joined=1,
         rules="Music campaign: GTA 6 edits that use the song \"Tokyo Girl\" by Passive. Not a streamer campaign.",
         hints={}),
]

ACCOUNTS = [("tiktok", "@vira1gta6clips"), ("youtube", "@gta6viralclips-v5y"), ("instagram", "@gta6.viralclipsss")]

# Keys whose values were read from the campaign page.
_PAGE_FIELDS = ("campaign_type", "payout_per_1k", "min_payout", "max_payout", "pool_size", "view_threshold",
                "budget_total", "budget_remaining", "min_views", "required_tags", "required_onscreen",
                "allowed_platforms")


def seed(conn: sqlite3.Connection) -> None:
    for c in CAMPAIGNS:
        if conn.execute("SELECT 1 FROM programs WHERE operator='content_rewards' AND external_id=?",
                        (c["external_id"],)).fetchone():
            continue   # never overwrite a campaign that already exists
        sources = {f: "page" for f in _PAGE_FIELDS if c.get(f) not in (None, [])}
        row = dict(
            name=c["name"], operator="content_rewards", url=CR + c["external_id"],
            payout_per_1k=c.get("payout_per_1k"), currency="USD", min_views=c.get("min_views"),
            max_payout=c.get("max_payout"), allowed_platforms=json.dumps(c.get("allowed_platforms") or []),
            required_tags=json.dumps(c.get("required_tags") or []), required_credit="{url}",
            disclosure=None, rules=c.get("rules"), source="manual", active=1,
            campaign_type=c["campaign_type"], streamer_label=c.get("streamer_label"),
            pool_size=c.get("pool_size"), view_threshold=c.get("view_threshold"),
            budget_total=c.get("budget_total"), budget_remaining=c.get("budget_remaining"),
            min_payout=c.get("min_payout"), required_onscreen=json.dumps(c.get("required_onscreen") or []),
            pays_au=1, fee=0.10, approval_rate=0.60, external_id=c["external_id"], status="active",
            joined=1, joined_at=JOINED_AT, clippers_joined=c.get("clippers_joined"),
            channel_hints=json.dumps({p: {"value": v, "source": s} for p, (v, s) in c["hints"].items()}),
            field_sources=json.dumps(sources), last_synced_at="2026-11-19 09:00:00",
        )
        row["needs_confirming"] = json.dumps(needs_confirming(row))
        cols = ", ".join(row)
        conn.execute(f"INSERT INTO programs({cols}, verified_at) VALUES ({', '.join('?' * len(row))}, "
                     "'2026-11-19 09:00:00')", tuple(row.values()))

    for platform, handle in ACCOUNTS:
        if not conn.execute("SELECT 1 FROM accounts WHERE platform=? AND lower(handle)=lower(?)",
                            (platform, handle)).fetchone():
            conn.execute("INSERT INTO accounts(platform, handle, external_id, oauth_status) VALUES (?,?,NULL,"
                         "'not_connected')", (platform, handle))
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('content_rewards_username', ?)",
                 (json.dumps("grandtheftclipper"),))
