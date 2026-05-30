#!/usr/bin/env python3
"""Static file server for the viz + a live jet-fuel price endpoint.

Plain `python3 -m http.server` can't price fuel live (and stooq has no CORS), so
this thin wrapper serves ./public AND exposes /api/fuel, which proxies the
toolkit's fuelfeed.get_price() (ULSD/NY-Harbor front-month -> $/gal, with crude
and offline fallbacks). The frontend fetches it on load and falls back to the
manual slider if this server isn't used.

    ./serve.sh                 # uses this; http://localhost:8765/index.html
    python3 serve.py 8765
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import mktime_tz, parsedate_tz
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "public")
# let us import fuelfeed.py from the data bundle next door (optional)
sys.path.insert(0, os.path.join(HERE, "..", "hackathon_data_bundle"))

# RSS/Atom feeds proxied by /api/feed (browser can't read these — no CORS).
FEED_SOURCES = [
    ("https://alerts.weather.gov/cap/us.php?x=0", "NWS", "weather"),
]
FEED_TTL = 120       # seconds — rapid polling is served from cache
FEED_TIMEOUT = 6     # per-feed network timeout
_feed_cache = {"t": 0.0, "items": None}


def _strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()  # namespace-agnostic element name


def _parse_ts(s):
    if not s:
        return int(time.time())
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        pass
    try:
        return int(mktime_tz(parsedate_tz(s)))   # RFC822 (RSS pubDate)
    except Exception:
        return int(time.time())


def _entry_to_item(entry, source, category):
    title = link = body = ts_raw = ident = ""
    for c in entry:
        name = _local(c.tag)
        if name == "title":
            title = _strip_tags(c.text)
        elif name in ("summary", "description", "content"):
            body = _strip_tags(c.text)
        elif name in ("updated", "published", "pubdate", "date"):
            ts_raw = (c.text or "").strip()
        elif name == "id" or name == "guid":
            ident = (c.text or "").strip()
        elif name == "link":
            link = c.get("href") or (c.text or "").strip() or link
    ts = _parse_ts(ts_raw)
    if not ident:
        ident = hashlib.sha1((title + str(ts)).encode()).hexdigest()[:16]
    return {"id": ident, "category": category, "title": title or "(untitled)",
            "body": body[:280], "source": source, "ts": ts, "link": link}


def _fetch_feed(url, source, category):
    req = urllib.request.Request(url, headers={"User-Agent": "NAS-LiveFeed/1.0"})
    with urllib.request.urlopen(req, timeout=FEED_TIMEOUT) as r:
        root = ET.fromstring(r.read())
    out = []
    for e in root.iter():
        if _local(e.tag) in ("entry", "item"):
            out.append(_entry_to_item(e, source, category))
    return out


def _get_feed_items():
    now = time.time()
    if _feed_cache["items"] is not None and now - _feed_cache["t"] < FEED_TTL:
        return _feed_cache["items"]
    items = []
    for url, source, category in FEED_SOURCES:
        try:
            items.extend(_fetch_feed(url, source, category))
        except Exception:
            continue
    if not items:   # offline / sandbox -> graceful single system note
        items = [{"id": "system-offline", "category": "system",
                  "title": "Live feed unavailable",
                  "body": "No network access to upstream RSS/Atom feeds.",
                  "source": "local", "ts": int(now), "link": ""}]
    items.sort(key=lambda x: x["ts"], reverse=True)
    items = items[:60]
    _feed_cache.update(t=now, items=items)
    return items


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/fuel":
            return self._fuel()
        if path == "/api/feed":
            return self._feed()
        return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] == "/api/chat":
            return self._chat()
        self.send_error(404)

    def _chat(self):
        # browser owns the tool-use loop; we run ONE model turn and return it raw.
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            import chat_backend
            out = chat_backend.run_turn(req.get("messages", []))
        except Exception as e:
            out = {"error": f"{type(e).__name__}: {e}"}
        self._send_json(out)

    def _send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fuel(self):
        try:
            import fuelfeed
            self._send_json(fuelfeed.get_price())
        except Exception as e:  # bundle missing / offline -> let the UI fall back
            self._send_json({"usd_per_gal": 2.50, "source": f"unavailable: {e}",
                             "live": False, "as_of": "—"})

    def _feed(self):
        try:
            self._send_json({"items": _get_feed_items()})
        except Exception as e:
            self._send_json({"items": [{"id": "system-error", "category": "system",
                             "title": "Feed error", "body": str(e),
                             "source": "local", "ts": int(time.time()), "link": ""}]})

    def log_message(self, *a):  # quieter console
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Serving viz + live fuel feed on http://localhost:{port}/index.html  (Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port),
                        partial(Handler, directory=PUBLIC)).serve_forever()
