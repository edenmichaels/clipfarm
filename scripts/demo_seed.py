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


# v0.6+: editable clips for the Editor (skipped on older versions that don't have edit projects).
def seed_edit_projects():
    import shutil
    import subprocess
    try:
        from clipfarm.edit import compile as ec, project as ep
        from clipfarm.jobs import storage
    except ImportError:
        return
    ff = shutil.which("ffmpeg")
    if not ff:
        print("no ffmpeg: skipping Editor demo clips")
        return
    media = storage.media_dir()
    src = media / "sources" / "demo_vod.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    # 60 s 1080p "gameplay" with a facecam box in the top-right corner and a voice-ish tone.
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=60",
                    "-f", "lavfi", "-i", "color=c=0x3a5f8a:s=480x320:r=30:d=60",
                    "-f", "lavfi", "-i", "sine=f=220:d=60",
                    "-filter_complex", "[0:v][1:v]overlay=1400:40,drawbox=x=1400:y=40:w=480:h=320:c=white:t=6[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)],
                   check=True)
    lines = ("bro no way he just drove straight into the bank vault the cops are everywhere um what is he doing "
             "chat look at this he actually made it out with the money that was insane").split()
    words = [{"word": w, "start": round(2 + i * 0.42, 2), "end": round(2.32 + i * 0.42, 2)} for i, w in enumerate(lines)]
    info = {"path": str(src), "width": 1920, "height": 1080, "fps": 30, "start": 0.0, "end": 60.0}
    demos = [("He drove INTO the bank vault 😂", "Funny", 0.92, 1.5, 22, 14, "NO WAY he drove into the vault"),
             ("Police chase ends in the craziest escape", "Action/Explosion", 0.81, 3, 30, 20, "Watch the cops lose him"),
             ("Chat could not believe this", "Rage/Reaction", 0.64, 0.5, 40, 30, None),
             ("Heist plan goes wrong", "Fail", 0.55, 5, 52, 26, None)]
    for title, mtype, mscore, a, b, peak, hook in demos:
        cid = db.ex("""INSERT INTO clips(project_id, streamer_id, game_id, program_id, title, duration_sec,
                       review_status, moment_type, source_path) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (pj, kc, gid, prog, title, b - a, "pending", mtype, str(src)))
        e = ep.blank(info, words, a, b)
        e["hook"].update(text=hook, options=[x for x in (hook, "Wait for the end", "He actually did it") if x])
        e["moment"].update(type=mtype, peak=peak, score=mscore, reason="laughter + chat spike")
        e["layout"]["facecam"] = {"x": 0.729, "y": 0.037, "w": 0.25, "h": 0.296}
        e["layout"]["keyframes"] = [{"t": a, "layout": "split", "path": [[0, 0.5]], "auto": True, "scene": "facecam+gameplay"}]
        e["rules"].update(program_id=prog, program="KreekCraft", tags=["#KreekCraft"], credit="twitch.tv/kreekcraft")
        # the last demo clip is missing the campaign hashtag, to show a red rule flag and its one-click fix
        tags = ["gta6"] if mtype == "Fail" else ["KreekCraft", "gta6"]
        e["texts"] = {"tiktok": {"title": title, "description": "", "hashtags": tags}}
        e["music"]["enabled"] = False
        ep.create(cid, e)
        try:                        # low-res base for the live preview + a thumbnail
            base, _ = ec.render_base(e, media / "edit_cache" / f"c{cid}", "proxy")
            thumb = media / "thumbs" / f"c{cid}.jpg"
            thumb.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([ff, "-v", "error", "-y", "-ss", "1", "-i", str(base), "-frames:v", "1", "-vf", "scale=270:-2",
                            str(thumb)], check=True)
            db.ex("UPDATE clips SET thumb_path=?, file_path=? WHERE id=?", (str(thumb), str(base), cid))
        except Exception as ex:     # noqa: BLE001
            print("demo base render failed:", ex)
    print("editor demo clips ready")


seed_edit_projects()
print("demo data ready")
