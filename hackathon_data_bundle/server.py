"""Localhost web server for the Hold-Cost Engine visualizer.

  .venv/bin/python server.py        # -> http://localhost:8000

Stdlib only (no Flask). Serves index.html and a /api/event endpoint that runs
holdcost.build_event with query params hub, aar, fuel, sev. Fuel-price re-pricing
is done client-side (linear), so only hub / AAR / weather changes hit the server.
"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import holdcost
import fuelfeed

SCEN = "asked_at_2025-07-01T21:30:00Z"   # summer day w/ storms near KATL
HERE = os.path.dirname(os.path.abspath(__file__))
HUBS = ["KATL", "KORD", "KDFW", "KDEN", "KCLT", "KLAX", "KIAH"]


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/api/hubs":
            return self._send(200, json.dumps({"hubs": HUBS, "scenario": SCEN}))
        if u.path == "/api/reroute":
            # demo endpoint: longest flight + a no-fly box on its path. Real
            # callers use autoroute.reroute(flight, zones, ...) directly.
            import autoroute
            from atm import haversine_nm
            q = parse_qs(u.query)
            half = float(q.get("half", ["3.0"])[0])
            fuel = float(q.get("fuel", [str(fuelfeed.get_price()["usd_per_gal"])])[0])
            _, flights = holdcost._load_cached(SCEN)
            f = max(flights, key=lambda x: haversine_nm(x.lats[0], x.lons[0],
                                                        x.lats[-1], x.lons[-1]))
            mlat = (f.lats[0]+f.lats[-1])/2; mlon = (f.lons[0]+f.lons[-1])/2
            box = [[mlat-half, mlon-half], [mlat-half, mlon+half],
                   [mlat+half, mlon+half], [mlat+half, mlon-half]]
            out = autoroute.reroute(f, [box], fuel_usd_per_gal=fuel,
                                    concurrent_flights=flights[:1200])
            out["flight"] = {"fn": f.fn, "orig": f.orig, "dest": f.dest}
            return self._send(200, json.dumps(out))
        if u.path == "/reroute":
            with open(os.path.join(HERE, "reroute_viewer.html"), "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        if u.path == "/api/fuel":
            try:
                return self._send(200, json.dumps(fuelfeed.get_price()))
            except Exception as e:
                return self._send(200, json.dumps({"usd_per_gal": 2.50,
                    "source": f"error: {e}", "live": False, "as_of": "—"}))
        if u.path == "/api/event":
            q = parse_qs(u.query)
            hub = q.get("hub", ["KATL"])[0]
            aar = float(q.get("aar", ["55"])[0])
            fuel = float(q.get("fuel", ["2.50"])[0])
            sev = float(q.get("sev", ["1.0"])[0])
            try:
                ev = holdcost.build_event(SCEN, hub, base_aar=aar,
                                          fuel_usd_per_gal=fuel,
                                          weather_severity=sev)
                return self._send(200, json.dumps(ev))
            except Exception as e:
                import traceback; traceback.print_exc()
                return self._send(500, json.dumps({"error": str(e)}))
        return self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *a):   # quieter console
        pass


if __name__ == "__main__":
    port = 8000
    print(f"Hold-Cost Engine  ->  http://localhost:{port}   (scenario {SCEN})")
    print("warming flight cache...")
    holdcost.build_event(SCEN, "KATL")   # prime the cache so first click is fast
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
