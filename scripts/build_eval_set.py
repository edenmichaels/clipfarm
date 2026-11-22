"""Build the labelled highlight-evaluation set (CI only; needs network for the CC0 sounds and espeak-ng).

Three synthetic "VODs" (~8 min each), each a timeline of labelled segments:
  positives  funny (real laughter), action (real explosions + gunfire + on-screen flashes/shake),
             reaction (real screaming / cheering) -- with matching chat reactions
  negatives  menu/inventory screen with loud menu music, loading screen, BRB/AFK screen, calm talking,
             constantly loud talking over gameplay, loud music over gameplay, a sub-train chat spam with
             nothing happening on screen
  filler     ordinary gameplay with quiet ambience

Sounds: Freesound previews of CC0 recordings. The page of every sound is fetched and must still say
"Creative Commons 0" or the sound is skipped (and the run fails if too few remain). Speech: espeak-ng.
Music: ClipFarm's own composed tracks. Video: generated with FFmpeg.

Output: <out>/vod{1,2,3}.mp4, chat{1,2,3}.jsonl, labels.json, SOURCES.md
"""
import json
import random
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
snd = out / "sounds"
snd.mkdir(exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# id -> (kind, page url). Licences checked on the page at build time.
SOUNDS = {
    "laugh_group": (486183, "laughter", "https://freesound.org/people/craigsmith/sounds/486183/"),
    "laugh_everybody": (25296, "laughter", "https://freesound.org/s/25296/"),
    "laugh_women": (438419, "laughter", "https://freesound.org/s/438419/"),
    "laugh_canned": (371562, "laughter", "https://freesound.org/people/Kinoton/sounds/371562/"),
    "laugh_hard": (393353, "laughter", "https://freesound.org/people/NSDAP/sounds/393353/"),
    "laugh_raw": (336533, "laughter", "https://freesound.org/s/336533/"),
    "laugh_kids": (383207, "laughter", "https://freesound.org/s/383207/"),
    "boom_bang": (580040, "explosion", "https://freesound.org/people/SamsterBirdies/sounds/580040/"),
    "boom_unfa": (609587, "explosion", "https://freesound.org/people/unfa/sounds/609587/"),
    "rifle": (396326, "gunshot", "https://freesound.org/people/SuperPhat/sounds/396326/"),
    "cheer_crowd": (324892, "cheer", "https://freesound.org/s/324892/"),
    "yell_battle": (764265, "scream", "https://freesound.org/s/764265/"),
    "scream_panic": (435716, "scream", "https://freesound.org/s/435716/"),
    "kids_yeah": (675108, "cheer", "https://freesound.org/people/craigsmith/sounds/675108/"),
}
UA = {"User-Agent": "Mozilla/5.0 (ClipFarm CI eval builder)"}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


sources, ok = [], {}
for key, (sid, kind, page) in SOUNDS.items():
    try:
        html = get(page).decode("utf-8", "ignore")
    except Exception as e:
        print(f"skip {key}: page {e}")
        continue
    if "Creative Commons 0" not in html:
        print(f"skip {key}: licence on page is not CC0")
        continue
    m = re.search(rf"https://cdn\.freesound\.org/previews/\d+/{sid}_\d+-hq\.mp3", html)
    if not m:
        print(f"skip {key}: no preview link")
        continue
    p = snd / f"{key}.mp3"
    p.write_bytes(get(m.group(0)))
    ok[key] = kind
    sources.append(f"- {key}: {page} -- CC0 (checked {m.group(0)})")
kinds = {k: [s for s, kk in ok.items() if kk == k] for k in ("laughter", "explosion", "gunshot", "cheer", "scream")}
print("sounds:", {k: len(v) for k, v in kinds.items()})
assert len(kinds["laughter"]) >= 3 and kinds["explosion"] and (kinds["cheer"] or kinds["scream"]), "too few CC0 sounds"

# speech (espeak-ng): calm and loud
LINES = ["okay so we need to grab the car first and then head over to the bank, should be easy",
         "let me check the map real quick, I think the safe house is somewhere over here",
         "chat what do you guys think, should we do the heist now or wait for the others",
         "alright alright I'm reading chat, thanks for the follow, appreciate you",
         "WHAT ARE YOU DOING BRO GET IN THE CAR GET IN THE CAR WE HAVE TO GO RIGHT NOW",
         "NO NO NO THAT IS NOT WHERE WE PARKED I AM SO CONFUSED RIGHT NOW HONESTLY"]
for i, line in enumerate(LINES):
    loud = line.isupper()
    subprocess.run(["espeak-ng", "-s", "175" if loud else "150", "-a", "200" if loud else "90", "-w",
                    str(snd / f"speech{i}.wav"), line.lower()], check=True)

# music (ClipFarm originals)
from clipfarm.music import compose  # noqa: E402
music = compose.render_all(snd / "music", "ffmpeg")

W, H, R = 640, 360, 30
rng = random.Random(42)


def video(kind: str, dur: float, path: Path):
    if kind == "gameplay":
        src = f"testsrc2=s={W}x{H}:r={R}:d={dur}"
        ff("-f", "lavfi", "-i", src, "-vf", f"hue=s=0.6,crop={W}:{H}:'mod(t*40,20)':0,scale={W}:{H}", "-pix_fmt",
           "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", path)
    elif kind == "action":     # flashes + shake on top of fast movement
        ff("-f", "lavfi", "-i", f"testsrc2=s={W + 40}x{H + 40}:r={R}:d={dur}", "-vf",
           f"crop={W}:{H}:'20+15*sin(t*37)':'20+15*cos(t*41)',"
           f"eq=brightness='0.5*gt(mod(t,1.3),1.15)':eval=frame", "-pix_fmt", "yuv420p", "-c:v", "libx264",
           "-preset", "ultrafast", path)
    elif kind == "menu":       # static inventory-like grid with text
        boxes = ",".join(f"drawbox=x={40 + (i % 6) * 95}:y={60 + (i // 6) * 90}:w=80:h=75:color=white@0.8:t=2"
                         for i in range(18))
        ff("-f", "lavfi", "-i", f"color=c=0x202838:s={W}x{H}:r={R}:d={dur}", "-vf",
           boxes + ",drawbox=x=20:y=15:w=600:h=30:color=0x405070:t=fill", "-pix_fmt", "yuv420p", "-c:v", "libx264",
           "-preset", "ultrafast", path)
    elif kind == "loading":
        ff("-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={R}:d={dur}", "-vf",
           f"drawbox=x='{W - 60}':y='{H - 60}':w=20:h=20:color=white@0.6:t=fill", "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-preset", "ultrafast", path)
    elif kind == "brb":
        ff("-f", "lavfi", "-i", f"color=c=0x553399:s={W}x{H}:r={R}:d={dur}", "-vf",
           "drawbox=x=170:y=140:w=300:h=80:color=white@0.9:t=fill", "-pix_fmt", "yuv420p", "-c:v", "libx264",
           "-preset", "ultrafast", path)
    elif kind == "talk":       # streamer just sitting there: almost no motion
        ff("-f", "lavfi", "-i", f"color=c=0x6a7a5a:s={W}x{H}:r={R}:d={dur}", "-vf",
           "drawbox=x='300+2*sin(t)':y=120:w=90:h=120:color=0xd0a080:t=fill", "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-preset", "ultrafast", path)


def audio(parts: list[tuple[str, float, float]], dur: float, path: Path, bed: float = 0.02):
    """parts: (file, start, gain). Gameplay ambience bed under everything."""
    inputs, filt = ["-f", "lavfi", "-t", str(dur), "-i", f"anoisesrc=c=brown:a={bed}"], []
    for i, (f, st, g) in enumerate(parts, start=1):
        inputs += ["-i", str(f)]
        filt.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,volume={g},adelay={int(st * 1000)}:all=1[a{i}]")
    mix = "[0:a]" + "".join(f"[a{i}]" for i in range(1, len(parts) + 1))
    filt.append(f"{mix}amix=inputs={len(parts) + 1}:duration=first:normalize=0,atrim=0:{dur}[out]")
    ff(*inputs, "-filter_complex", ";".join(filt), "-map", "[out]", "-ac", "1", "-ar", "48000", path)


def chat_burst(t0: float, dur: float, tokens: list[str], rate: float, msgs: list):
    t = t0
    while t < t0 + dur:
        msgs.append({"t": round(t, 2), "m": " ".join(rng.choice(tokens) for _ in range(rng.randint(1, 3)))})
        t += rng.expovariate(rate)


NORMAL = ["hi", "lol", "nice", "gg", "what game is this", "o7", "true", "monkaS", "hello chat", "W", "based"]
LAUGH = ["KEKW", "LUL", "OMEGALUL", "LMAO", "😂", "💀", "KEKW KEKW", "ICANT", "hahaha"]
HYPE = ["POGGERS", "PogChamp", "W", "NO WAY", "CLIP IT", "insane", "holy", "🔥", "clip that", "WWW"]
SUBS = ["PogChamp", "W sub", "hype", "PogChamp PogChamp", "sub hype", "W", "thanks for the sub"]

PLANS = [   # (kind, seconds, label) ; label: pos / neg / filler
    [("gameplay", 40, "filler"), ("menu", 45, "neg"), ("gameplay", 30, "filler"), ("funny", 25, "pos"),
     ("talk", 60, "neg"), ("action", 30, "pos"), ("gameplay", 30, "filler"), ("loudtalk", 50, "neg"),
     ("loading", 25, "neg"), ("funny", 25, "pos"), ("gameplay", 30, "filler"), ("loudmusic", 45, "neg"),
     ("reaction", 25, "pos"), ("gameplay", 25, "filler")],
    [("gameplay", 30, "filler"), ("brb", 50, "neg"), ("action", 30, "pos"), ("gameplay", 30, "filler"),
     ("subtrain", 40, "neg"), ("funny", 25, "pos"), ("talk", 50, "neg"), ("gameplay", 25, "filler"),
     ("loudmusic", 40, "neg"), ("action", 30, "pos"), ("menu", 40, "neg"), ("funny", 25, "pos"),
     ("gameplay", 30, "filler")],
    [("gameplay", 30, "filler"), ("loudtalk", 60, "neg"), ("reaction", 25, "pos"), ("loading", 30, "neg"),
     ("gameplay", 25, "filler"), ("funny", 25, "pos"), ("menu", 45, "neg"), ("action", 30, "pos"),
     ("talk", 45, "neg"), ("subtrain", 40, "neg"), ("funny", 25, "pos"), ("gameplay", 30, "filler"),
     ("action", 30, "pos"), ("gameplay", 20, "filler")],
]
VKIND = {"funny": "gameplay", "action": "action", "reaction": "gameplay", "loudtalk": "gameplay",
         "loudmusic": "gameplay", "subtrain": "gameplay"}

labels = {}
for vi, plan in enumerate(PLANS, start=1):
    segs, parts, msgs, t = [], [], [], 0.0
    for si, (kind, dur, lab) in enumerate(plan):
        vpath = out / f"_v{vi}_{si}.mp4"
        video(VKIND.get(kind, kind), dur, vpath)
        segs.append(vpath)
        chat_burst(t, dur, NORMAL, 0.8, msgs)       # normal chat everywhere
        if kind == "funny":
            f = snd / f"{rng.choice(kinds['laughter'])}.mp3"
            parts += [(snd / "speech2.wav", t + 2, 0.8), (f, t + 8, 1.6)]
            chat_burst(t + 10, 10, LAUGH, 4.0, msgs)
            labels.setdefault(f"vod{vi}", []).append({"start": t + 6, "end": t + 22, "type": "Funny"})
        elif kind == "action":
            for k in range(4):
                parts.append((snd / f"{rng.choice(kinds['explosion'])}.mp3", t + 4 + k * 4.5, 1.2))
            if kinds["gunshot"]:
                parts += [(snd / f"{kinds['gunshot'][0]}.mp3", t + 6 + k * 3, 1.0) for k in range(3)]
            chat_burst(t + 8, 12, HYPE, 3.5, msgs)
            labels.setdefault(f"vod{vi}", []).append({"start": t + 3, "end": t + 24, "type": "Action/Explosion"})
        elif kind == "reaction":
            pool = kinds["scream"] + kinds["cheer"]
            parts += [(snd / "speech4.wav", t + 2, 1.2), (snd / f"{rng.choice(pool)}.mp3", t + 8, 1.4)]
            chat_burst(t + 10, 10, HYPE, 3.0, msgs)
            labels.setdefault(f"vod{vi}", []).append({"start": t + 6, "end": t + 22, "type": "Rage/Reaction"})
        elif kind in ("talk",):
            for k in range(int(dur // 9)):
                parts.append((snd / f"speech{k % 4}.wav", t + k * 9, 0.6))
        elif kind == "loudtalk":
            for k in range(int(dur // 7)):
                parts.append((snd / f"speech{4 + k % 2}.wav", t + k * 7, 1.6))
        elif kind in ("menu", "loudmusic", "loading"):
            tr = rng.choice(music)
            parts.append((snd / "music" / tr["file"], t, 1.3 if kind != "loading" else 0.6))
        elif kind == "subtrain":
            chat_burst(t + 5, 25, SUBS, 4.0, msgs)
        t += dur
    lst = out / f"_list{vi}.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in segs))
    ff("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out / f"_video{vi}.mp4")
    audio(parts, t, out / f"_audio{vi}.wav")
    ff("-i", out / f"_video{vi}.mp4", "-i", out / f"_audio{vi}.wav", "-c:v", "copy", "-c:a", "aac", "-shortest",
       out / f"vod{vi}.mp4")
    (out / f"chat{vi}.jsonl").write_text("\n".join(json.dumps(m) for m in sorted(msgs, key=lambda m: m["t"])))
    for p in [*segs, lst, out / f"_video{vi}.mp4", out / f"_audio{vi}.wav"]:
        p.unlink(missing_ok=True)
    labels[f"vod{vi}_plan"] = [[k, d, lab] for k, d, lab in plan]
(out / "labels.json").write_text(json.dumps(labels, indent=1))
(out / "SOURCES.md").write_text("Evaluation sounds (Freesound, Creative Commons 0, licence checked on each page at build time):\n"
                                + "\n".join(sources) + "\n\nSpeech: espeak-ng. Music: ClipFarm Originals (CC0). "
                                "Video and chat: generated.\n")
print("eval set ready:", out)
