"""Demo data for the before/after screenshots (CI only). Works with the v0.2 and v0.3 engines.

Usage (from backend/):  CLIPFARM_DATA_DIR=/tmp/demo python ../scripts/demo_seed.py
Creates a GTA 6 game, a few live streamers (some on Eden's seeded campaigns, one on a campaign
not joined yet, two with no campaign), projections, rankings, a running project, clips and payouts.
No network.
"""
import json
import sys

sys.path.insert(0, ".")
from clipfarm import db  # noqa: E402
from clipfarm.campaigns import forecast, matching  # noqa: E402
from clipfarm.discovery import service as disc  # noqa: E402

db.init_db()
db.set_setting("setup_complete", True)
db.set_setting("disclaimer_accepted", True)
db.set_setting("transcription", {"enabled": False})
gid = disc.upsert_game("Grand Theft Auto VI", None, None, None)

STREAMERS = [  # login, display, viewers, campaign streamer_label (None = no campaign)
    ("kreekcraft", "KreekCraft", 41200, "KreekCraft"),
    ("dylanpage", "Dylan Page", 18800, "Dylan Page"),
    ("asianjeff", "AsianJeff", 9300, "AsianJeff"),
    ("tomo", "TOMO", 12700, "TOMO"),            # campaign left un-joined -> "Worth joining" in v0.3
    ("summitpeak", "SummitPeak", 55000, None),
    ("vegasnights", "VegasNights", 7400, None),
]
for login, name, viewers, label in STREAMERS:
    plat, handle = ("youtube", "@Tomo") if login == "tomo" else ("twitch", login)   # TOMO's campaign links YouTube
    sid = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                   VALUES (?, ?, ?, ?, 'allowed')""", (plat, f"demo_{login}", handle, name))
    for v in (viewers * 0.9, viewers):
        db.ex("INSERT INTO streamer_snapshots(streamer_id, game_id, is_live, viewers) VALUES (?,?,1,?)", (sid, gid, int(v)))
    if label and label != "TOMO":
        pid = db.q1("SELECT id FROM programs WHERE streamer_label=?", (label,))["id"]
        db.ex("INSERT INTO streamer_programs(streamer_id, program_id, confirmed) VALUES (?,?,1)", (sid, pid))
db.ex("UPDATE programs SET joined=0 WHERE streamer_label='TOMO'")
matching.sync_permissions()
forecast.project_all()
disc.rank_game(gid)

# A running project with a few clips, and some earnings.
pj = db.ex("""INSERT INTO projects(name, game_id, status, duration_sec, started_at, ends_at, output_settings)
              VALUES ('GTA 6 · KreekCraft + Dylan Page', ?, 'running', 86400, datetime('now','-9 hours'),
              datetime('now','+15 hours'), ?)""", (gid, json.dumps({"platforms": ["tiktok", "youtube_shorts"]})))
kc = db.q1("SELECT id FROM streamers WHERE login='kreekcraft'")["id"]
for i, status in enumerate(["pending", "pending", "pending", "approved", "approved"]):
    db.ex("""INSERT INTO clips(project_id, streamer_id, game_id, title, duration_sec, review_status)
             VALUES (?,?,?,?,?,?)""", (pj, kc, gid, f"KreekCraft heist goes wrong #{i + 1}", 34, status))
prog = db.q1("SELECT id FROM programs WHERE streamer_label='KreekCraft'")["id"]
for amt, st in ((4.10, "pending"), (2.35, "approved")):
    db.ex("INSERT INTO payouts(program_id, amount, status, submitted_at) VALUES (?,?,?,datetime('now','-1 day'))",
          (prog, amt, st))
print("demo data ready")
