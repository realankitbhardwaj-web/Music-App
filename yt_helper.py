#!/usr/bin/env python3
"""
yt_helper.py - tiny local "YouTube audio helper" for the music app.

It turns a YouTube video id into a real audio stream that the app can play in a
normal <audio> element, which is what keeps music playing with the screen
locked. Run it on the SAME phone as the app (Termux) and nothing else is needed.

    python yt_helper.py            # listens on http://localhost:8787
    python yt_helper.py 9000       # other port
    python yt_helper.py 8787 --host 0.0.0.0   # reachable from other devices (put HTTPS in front!)

Endpoints
    GET /health             -> {"ok": true}
    GET /resolve?id=VIDEO   -> {"url": "...", "ext": "m4a"}   (direct URL, same-IP only)
    GET /stream?id=VIDEO    -> the audio itself (supports Range / seeking)

Needs:  yt-dlp (and Node.js, which current yt-dlp uses to solve YouTube's checks)
    pkg install python nodejs
    pip install -U yt-dlp yt-dlp-ejs
Keep yt-dlp up to date (pip install -U yt-dlp) - YouTube changes break old versions.
"""
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")   # only real video ids are accepted (no open proxy)
CACHE = {}                                       # id -> (expires_at, url, headers, ext)
TTL = 20 * 60


def extract(video_id):
    """Ask yt-dlp for the best audio-only stream. Returns (url, headers, ext)."""
    hit = CACHE.get(video_id)
    if hit and hit[0] > time.time():
        return hit[1:]
    cmd = ["yt-dlp", "-f", "bestaudio[ext=m4a]/bestaudio", "--dump-single-json",
           "--no-playlist", "--no-warnings"]
    last_err = ""
    # newer yt-dlp wants a JS runtime for YouTube; older versions don't know the flag
    for extra in (["--js-runtimes", "node"], []):
        try:
            r = subprocess.run(cmd + extra + ["https://www.youtube.com/watch?v=" + video_id],
                               capture_output=True, text=True, timeout=75)
        except FileNotFoundError:
            raise RuntimeError("yt-dlp is not installed (pip install -U yt-dlp)")
        if r.returncode == 0:
            info = json.loads(r.stdout)
            url = info.get("url")
            if not url and info.get("requested_formats"):
                url = info["requested_formats"][0].get("url")
            if not url:
                raise RuntimeError("yt-dlp returned no audio url")
            headers = info.get("http_headers") or {}
            ext = info.get("ext") or "m4a"
            CACHE[video_id] = (time.time() + TTL, url, headers, ext)
            return url, headers, ext
        last_err = (r.stderr or r.stdout or "yt-dlp failed").strip().splitlines()[-1][:300]
    raise RuntimeError(last_err)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")   # https site -> localhost
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            return self._json({"ok": True})
        if u.path not in ("/resolve", "/stream"):
            return self._json({"error": "not found"}, 404)
        vid = (q.get("id") or [""])[0]
        if not ID_RE.match(vid):
            return self._json({"error": "bad id"}, 400)
        try:
            url, headers, ext = extract(vid)
        except Exception as e:                       # noqa: BLE001
            return self._json({"error": str(e)}, 502)
        if u.path == "/resolve":
            return self._json({"url": url, "ext": ext})
        self._stream(vid, url, headers, ext)

    def _stream(self, vid, url, headers, ext, retry=True):
        req_headers = {k: v for k, v in headers.items() if k.lower() not in ("range", "accept-encoding")}
        req_headers["Range"] = self.headers.get("Range") or "bytes=0-"
        try:
            up = urllib.request.urlopen(urllib.request.Request(url, headers=req_headers), timeout=30)
        except urllib.error.HTTPError as e:
            CACHE.pop(vid, None)                     # stale/blocked link: extract again once
            if retry:
                try:
                    url, headers, ext = extract(vid)
                    return self._stream(vid, url, headers, ext, retry=False)
                except Exception:                    # noqa: BLE001
                    pass
            return self._json({"error": "upstream %s" % e.code}, 502)
        except Exception as e:                       # noqa: BLE001
            return self._json({"error": str(e)}, 502)
        with up:
            self.send_response(up.status)
            self._cors()
            self.send_header("Content-Type", "audio/mp4" if ext == "m4a" else (up.headers.get("Content-Type") or "audio/webm"))
            self.send_header("Accept-Ranges", "bytes")
            for h in ("Content-Length", "Content-Range"):
                if up.headers.get(h):
                    self.send_header(h, up.headers[h])
            self.end_headers()
            try:
                while True:
                    chunk = up.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass                                  # player seeked/stopped - normal

    def log_message(self, fmt, *args):               # keep the console quiet
        pass


def main():
    port, host = 8787, "127.0.0.1"
    args = sys.argv[1:]
    if args and args[0].isdigit():
        port = int(args.pop(0))
    if "--host" in args:
        host = args[args.index("--host") + 1]
    srv = ThreadingHTTPServer((host, port), Handler)
    print("YouTube audio helper running on http://%s:%d  (Ctrl+C to stop)" % (host, port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
