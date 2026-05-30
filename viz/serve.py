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
import json
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "public")
# let us import fuelfeed.py from the data bundle next door (optional)
sys.path.insert(0, os.path.join(HERE, "..", "hackathon_data_bundle"))


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/api/fuel":
            return self._fuel()
        return super().do_GET()

    def _fuel(self):
        try:
            import fuelfeed
            body = json.dumps(fuelfeed.get_price()).encode()
        except Exception as e:  # bundle missing / offline -> let the UI fall back
            body = json.dumps({"usd_per_gal": 2.50, "source": f"unavailable: {e}",
                               "live": False, "as_of": "—"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # quieter console
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Serving viz + live fuel feed on http://localhost:{port}/index.html  (Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port),
                        partial(Handler, directory=PUBLIC)).serve_forever()
