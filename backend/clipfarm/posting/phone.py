"""Send to phone: ClipFarm serves one clip on your home Wi-Fi, behind a QR code.

  - a separate tiny web server (not the engine API), started on first use, on port 8766 (or the next free)
  - each link has a random 32-character token and expires after an hour
  - requests from outside your home network are refused: the phone must have a private address in the same
    subnet as this PC (loopback is allowed, for this PC itself)
  - the page has the video, a Download button and a Copy caption button
Windows asks once whether ClipFarm may use the network ("Allow on private networks"): say Allow.
"""
from __future__ import annotations

import html
import ipaddress
import secrets as pysecrets
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import db

TTL = 3600
PORT = 8766
_links: dict[str, dict] = {}
_server: ThreadingHTTPServer | None = None
_lock = threading.Lock()


def lan_ip() -> str:
    """This PC's address on the home network (no traffic is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def allowed(client: str, server_ip: str | None = None, prefix: int | None = None) -> bool:
    """Only this PC and devices on the same private network."""
    try:
        ip = ipaddress.ip_address(client.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True
    if not ip.is_private or ip.is_global or ip.is_unspecified:
        return False
    srv = ipaddress.ip_address(server_ip or lan_ip())
    if ip.version != srv.version:
        return False
    pfx = prefix or int(db.get_setting("posting", {}).get("phone_subnet_prefix", 24))
    return ip in ipaddress.ip_network(f"{srv}/{pfx}", strict=False)


def create(clip_id: int, path: Path, caption: str, title: str, now: float | None = None) -> dict:
    tok = pysecrets.token_urlsafe(24)
    now = now or time.time()
    with _lock:
        for k in [k for k, v in _links.items() if v["expires"] < now]:
            _links.pop(k)
        _links[tok] = {"clip_id": clip_id, "path": str(path), "caption": caption, "title": title,
                       "expires": now + TTL}
    port = start()
    url = f"http://{lan_ip()}:{port}/p/{tok}"
    from .qr import svg
    return {"url": url, "token": tok, "expires_in": TTL, "qr_svg": svg(url),
            "expires_at": time.strftime("%H:%M", time.localtime(now + TTL))}


def get_link(tok: str, now: float | None = None) -> dict | None:
    v = _links.get(tok)
    if not v or v["expires"] < (now or time.time()):
        return None
    return v


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>body{{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px}}
video{{width:100%;max-height:60vh;background:#000;border-radius:12px}}a,button{{display:block;width:100%;box-sizing:border-box;
margin:12px 0;padding:16px;border-radius:12px;border:0;font-size:18px;font-weight:700;text-align:center;text-decoration:none}}
a{{background:#ffd400;color:#000}}button{{background:#2a2f3a;color:#fff}}textarea{{width:100%;height:9em;background:#1b1f27;
color:#eee;border:1px solid #333;border-radius:8px;padding:8px;box-sizing:border-box}}</style></head><body>
<h3>{title}</h3><video src="{video}" controls playsinline></video>
<a href="{video}?dl=1" download="{fname}">Download video</a>
<button onclick="cp()">Copy caption</button><textarea id="c" readonly>{caption}</textarea>
<p style="color:#888;font-size:13px">This link stops working at {until}.</p>
<script>function cp(){{var t=document.getElementById('c');t.select();t.setSelectionRange(0,99999);
var ok=false;try{{ok=document.execCommand('copy')}}catch(e){{}}
if(!ok&&navigator.clipboard){{navigator.clipboard.writeText(t.value)}}
document.querySelector('button').textContent='Caption copied';}}</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "ClipFarm"

    def log_message(self, *a):          # quiet
        pass

    def _deny(self, code: int, msg: str) -> None:
        body = f"<h3>{html.escape(msg)}</h3>".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                  # noqa: N802
        if not allowed(self.client_address[0]):
            return self._deny(403, "Only devices on the same home Wi-Fi can open this link.")
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) < 2 or parts[0] != "p":
            return self._deny(404, "Not found.")
        link = get_link(parts[1])
        if not link:
            return self._deny(410, "This link has expired or isn't valid. Make a new one in ClipFarm.")
        p = Path(link["path"])
        if len(parts) == 2:
            body = PAGE.format(title=html.escape(link["title"] or "ClipFarm clip"), video=f"/p/{parts[1]}/video",
                               fname=html.escape(p.name), caption=html.escape(link["caption"] or ""),
                               until=time.strftime("%H:%M", time.localtime(link["expires"]))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parts[2] != "video" or not p.exists():
            return self._deny(404, "Not found.")
        size = p.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if a and b else size - 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if "dl=1" in self.path:
            self.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
        self.end_headers()
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                buf = f.read(min(1 << 20, left))
                if not buf:
                    break
                try:
                    self.wfile.write(buf)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(buf)


def start(port: int | None = None) -> int:
    global _server
    with _lock:
        if _server:
            return _server.server_address[1]
        want = port or int(db.get_setting("posting", {}).get("phone_port", PORT))
        for p in (want, 0):
            try:
                _server = ThreadingHTTPServer(("0.0.0.0", p), _Handler)
                break
            except OSError:
                continue
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, daemon=True, name="send-to-phone").start()
        return _server.server_address[1]


def stop() -> None:
    global _server
    with _lock:
        if _server:
            _server.shutdown()
            _server.server_close()
            _server = None


def to_sync_folder(path: Path, caption: str, folder: str | None = None) -> Path:
    """Copy the clip (and its caption as a .txt) into a folder you sync to your phone (Dropbox, Google Drive)."""
    folder = folder or db.get_setting("posting", {}).get("sync_folder")
    if not folder:
        raise ValueError("Choose a sync folder first (Settings > Posting > Phone sync folder).")
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / path.name
    shutil.copy2(path, dest)
    if caption:
        dest.with_suffix(".txt").write_text(caption, encoding="utf-8")
    return dest
