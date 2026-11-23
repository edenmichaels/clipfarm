"""v0.8.4 audit: crash recovery, children dying with the engine, a short soak, bundled-asset licences."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_crash_recovery_requeues_jobs_and_cleans_render_leftovers(fresh_db):
    from clipfarm import db
    from clipfarm.jobs import engine
    jid = db.ex("INSERT INTO jobs(kind, payload, status) VALUES ('export_edit', '{}', 'running')")
    stale = Path(tempfile.mkdtemp(prefix="cff_"))
    (stale / "captions.ass").write_text("x")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    fresh = Path(tempfile.mkdtemp(prefix="cff_"))          # a render running right now in another test: kept
    eng = engine.Engine(workers=0, render_workers=0)
    eng.start()
    assert db.q1("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "queued"
    assert not stale.exists() and fresh.exists()
    fresh.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Job Object: Windows only (the shipped app)")
def test_children_die_when_the_engine_is_killed(tmp_path):
    script = tmp_path / "eng.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(ROOT / 'backend')!r})\n"
        "from clipfarm import proc\n"
        "proc.kill_children_when_engine_dies()\n"
        "p = proc.popen(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=r=30', '-f', 'null', '-'])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(120)\n", encoding="utf-8")
    eng = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    child = int(eng.stdout.readline())
    import psutil
    assert psutil.pid_exists(child)
    eng.kill()                                               # like Task Manager's End task
    eng.wait(10)
    for _ in range(50):
        if not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(0.1)
    else:
        pytest.fail("ffmpeg kept running after the engine was killed")


def test_short_soak_has_no_leaks(tmp_path):
    out = tmp_path / "soak.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "soak.py"), "--minutes", "0.5", "--out", str(out)],
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    s = json.loads(out.read_text())
    assert s["rounds"] >= 3 and not s["problems"]


def test_bundled_assets_have_licences():
    assets = ROOT / "backend" / "assets"
    assert any(p.name.startswith("LICENSE") for p in (assets / "emoji").iterdir())
    fonts = {p.name for p in (assets / "fonts").iterdir()}
    for f in fonts:
        if f.endswith(".ttf"):
            fam = f.split("-")[0]
            assert any(x.startswith("OFL") and fam in x for x in fonts), f"no licence for {f}"
    from clipfarm.music import sfx
    assert "CC0" in sfx.LICENCE
    # the app never fetches media from TikTok / Instagram, and never embeds commercial music
    src = "\n".join(p.read_text(encoding="utf-8").lower() for p in (ROOT / "backend" / "clipfarm").rglob("*.py"))
    assert "tiktok.com/api" not in src and "instagram.com/api" not in src and "i.instagram" not in src


def test_render_paths_use_no_shell():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "backend" / "clipfarm").rglob("*.py"))
    assert "shell=True" not in src and "os.system(" not in src
