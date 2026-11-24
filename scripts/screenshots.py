"""Before/after screenshots (CI only): Home, the menu, every New Project step, the streamer list and (v0.6+)
the Editor in simple and advanced mode.

Usage: python scripts/screenshots.py <out_dir>   (UI on http://localhost:1420, engine on 47821)
Works with the v0.2 UI (4 steps) and the v0.3 UI (3 steps). Each shot is best-effort.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def shot(page, name, full=True):
    page.wait_for_timeout(900)
    page.screenshot(path=str(out / f"{name}.png"), full_page=full)
    print("saved", name)


def attempt(name, fn):
    try:
        fn()
    except Exception as e:  # keep going: one missing screen shouldn't lose the rest
        print(f"skipped {name}: {e}")


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1360, "height": 860})
    page.goto("http://localhost:1420")
    page.wait_for_selector("nav.side", timeout=60000)
    page.wait_for_timeout(2500)
    if page.locator(".studio").count():                       # v0.8.3+: the Editor opens first
        page.locator("nav.side").get_by_role("button", name="Home").click()
        page.wait_for_timeout(1500)
    shot(page, "1-home", full=False)
    page.locator("nav.side").screenshot(path=str(out / "0-menu.png"))

    page.get_by_role("button", name="New Project").first.click()
    page.wait_for_timeout(1500)
    shot(page, "2-new-project-step1")

    def step2():
        page.locator("button.game", has_text="Grand Theft Auto VI").first.click()
        page.wait_for_timeout(5000)
        shot(page, "3-new-project-step2-streamers")
    attempt("step2", step2)

    def panel():
        row = page.locator(".srow-main").first
        row.click()
        page.wait_for_timeout(1500)
        shot(page, "3b-streamer-panel", full=False)
        page.get_by_role("button", name="Close").first.click()
        page.wait_for_timeout(800)
    if page.locator(".srow-main").count():
        attempt("panel", panel)

    def later_steps():
        if page.locator(".srow.on").count() == 0 and page.locator(".srow").count() == 0:
            ticks = page.locator("button.tick")       # v0.2 table: tick the first clippable streamers
            for i in range(min(2, ticks.count())):
                ticks.nth(i).click()
        page.get_by_role("button", name="Next", exact=False).last.click(timeout=10000)
        page.wait_for_timeout(2500)
        shot(page, "4-new-project-step3")
        more = page.get_by_role("button", name="Next: output")
        if more.count():
            more.click()
            page.wait_for_timeout(1200)
            shot(page, "5-new-project-step4")
        details = page.get_by_role("button", name="Show details", exact=False)
        if details.count():
            details.first.click()
            page.wait_for_timeout(1200)
            shot(page, "5-new-project-step3-details")
    attempt("steps 3+", later_steps)

    def editor():
        page.locator("nav.side").get_by_role("button", name="Editor").click()
        page.wait_for_selector(".ed-card", timeout=15000)
        page.wait_for_timeout(3500)
        if page.get_by_role("button", name="Show more").count():          # v0.6-v0.8.2 editor
            shot(page, "6-editor-simple", full=False)
            page.get_by_role("button", name="Show more").click()
            page.wait_for_timeout(2500)
            shot(page, "7-editor-advanced", full=False)
            for tab, name in (("Captions", "8-editor-captions"), ("Layout", "8b-editor-layout"),
                              ("Campaign rules", "9-editor-rules")):
                page.get_by_role("tab", name=tab, exact=False).first.click()
                page.wait_for_timeout(1200)
                shot(page, name, full=False)
            page.get_by_role("button", name="Show less").click()
            return
        # v0.8.3+: the Editor is the app (and the start screen)
        shot(page, "6-editor-100pct", full=False)
        for tab, name in (("Styles", "7-editor-styles"), ("Stickers", "7b-editor-stickers")):
            page.locator(".st-left").get_by_role("tab", name=tab).click()
            page.wait_for_timeout(1500)
            shot(page, name, full=False)
        page.locator(".st-left").get_by_role("tab", name="Bin").click()
        for tab, name in (("Captions", "8-editor-captions"), ("Layout", "8b-editor-layout-facecam"),
                          ("Colour", "8c-editor-colour"), ("Rules", "9-editor-rules")):
            page.locator(".st-right").get_by_role("tab", name=tab, exact=False).first.click()
            page.wait_for_timeout(1500)
            shot(page, name, full=False)
        page.locator(".tl-track.text .bar").first.click()
        page.wait_for_timeout(1000)
        shot(page, "8d-editor-text-selected", full=False)
        page.keyboard.press("?")
        page.wait_for_timeout(600)
        shot(page, "8e-editor-shortcuts", full=False)
        page.keyboard.press("Escape")
    if page.locator("nav.side").get_by_role("button", name="Editor").count():
        attempt("editor", editor)

    def my_clips():                       # v0.8.6+: selection bar, delete confirmation, Recently deleted, Post & Submit
        page.set_viewport_size({"width": 1360, "height": 860})
        page.locator("nav.side").get_by_role("button", name="My Clips").click()
        page.wait_for_timeout(2500)
        if not page.locator(".clip-check").count():
            return
        boxes = page.locator(".clip-check")
        boxes.nth(0).click()
        boxes.nth(1).click(modifiers=["Shift"])
        shot(page, "10-myclips-selection", full=False)
        page.locator(".selbar").get_by_role("button", name="Delete").click()
        page.wait_for_selector("[role=dialog]", timeout=5000)
        shot(page, "11-myclips-delete-confirm", full=False)
        page.get_by_role("button", name="Cancel").click()
        page.get_by_role("tab", name="Recently deleted").click()
        page.wait_for_timeout(1500)
        shot(page, "12-myclips-recently-deleted", full=False)
        page.get_by_role("tab", name="Post & Submit").click()
        page.wait_for_timeout(2500)
        shot(page, "13-post-and-submit")
        kit = page.get_by_role("button", name="Post to Instagram")
        if kit.count():
            kit.first.click()
            page.wait_for_selector("[aria-label='Post Kit']", timeout=8000)
            shot(page, "14-post-kit", full=False)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        page.get_by_role("button", name="Send to phone").first.click()
        page.wait_for_selector(".qr-box svg", timeout=8000)
        shot(page, "15-send-to-phone", full=False)
    if page.locator("nav.side").get_by_role("button", name="My Clips").count():
        attempt("my clips", my_clips)

    def scaled():
        # Windows display scaling: 125 % and 150 % on a 1360x860 window = fewer CSS pixels, denser pixels
        for pct, (w, h) in ((125, (1088, 688)), (150, (907, 573))):
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=pct / 100)
            pg = ctx.new_page()
            pg.goto("http://localhost:1420")
            pg.wait_for_selector("nav.side", timeout=60000)
            pg.wait_for_timeout(5000)
            pg.screenshot(path=str(out / f"6-editor-{pct}pct.png"))
            print("saved", f"6-editor-{pct}pct")
            ctx.close()
    attempt("scaled", scaled)
    b.close()
