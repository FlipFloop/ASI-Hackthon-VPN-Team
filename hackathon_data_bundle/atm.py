"""Core analytics primitives for the ATM data bundle.

Three building blocks:
  1. Flight position interpolation along planned waypoints (constant cruise model)
  2. Sector demand counting (point-in-polygon + altitude band) vs capacity
  3. Weather conflict detection (sample refc/retop grid along the flight)
"""
import json, glob, os, re
from datetime import datetime, timezone
import numpy as np
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

# ---- weather grid geometry (from documentation/wx/FILE_FORMAT.md) ----
LAT_MIN, LAT_MAX = 21.943, 55.7765
LON_MIN, LON_MAX = -135.0, -67.5
ROWS, COLS = 256, 358
REFC_THRESH = 40.0  # dBZ; >= is "weather"

def parse_ts(s):
    return datetime.fromisoformat(s)

# -------------------------------------------------------------------------
# 1. Flights
# -------------------------------------------------------------------------
def haversine_nm(lat1, lon1, lat2, lon2):
    R = 3440.065  # nautical miles
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dl = np.radians(lon2 - lon1)
    a = np.sin(dphi/2)**2 + np.cos(p1)*np.cos(p2)*np.sin(dl/2)**2
    return 2*R*np.arcsin(np.sqrt(a))

class Flight:
    __slots__ = ("fn","t0","t1","orig","dest","alt","spd","lats","lons",
                 "airborne","cum","total","dur")
    def __init__(self, d):
        self.fn = d["flight_number"]
        self.t0 = parse_ts(d["take_off_time"]).timestamp()
        self.t1 = parse_ts(d["scheduled_landing_time"]).timestamp()
        self.orig = d["origin_airport_icao"]; self.dest = d["destination_airport_icao"]
        self.alt = d["cruise_altitude_ft"]; self.spd = d["cruise_speed_kt"]
        self.lats = np.asarray(d["lats"]); self.lons = np.asarray(d["lons"])
        self.airborne = d["is_airborne"]
        seg = haversine_nm(self.lats[:-1], self.lons[:-1], self.lats[1:], self.lons[1:])
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        self.total = self.cum[-1]
        self.dur = max(self.t1 - self.t0, 1e-9)

    def position(self, t):
        """(lat, lon) at unix time t, or None if not en route."""
        if t < self.t0 or t > self.t1 or self.total <= 0:
            return None
        frac = (t - self.t0) / self.dur
        d = frac * self.total
        i = np.searchsorted(self.cum, d) - 1
        i = min(max(i, 0), len(self.cum) - 2)
        seg = self.cum[i+1] - self.cum[i]
        f = 0.0 if seg <= 0 else (d - self.cum[i]) / seg
        lat = self.lats[i] + f*(self.lats[i+1]-self.lats[i])
        lon = self.lons[i] + f*(self.lons[i+1]-self.lons[i])
        return lat, lon

def load_flights(scenario):
    r = json.load(open(os.path.join(scenario, "routes.json")))
    return r, [Flight(d) for d in r["flights"]]

# -------------------------------------------------------------------------
# 2. Sectors
# -------------------------------------------------------------------------
class Sectors:
    def __init__(self, path="sectors.geojson"):
        data = json.load(open(path))
        self.feat = data["features"]
        self.geoms = [shape(f["geometry"]) for f in self.feat]
        self.tree = STRtree(self.geoms)
        self.name = [f["properties"]["name"] for f in self.feat]
        self.cap = [f["properties"]["capacity"] for f in self.feat]
        self.lo = np.array([f["properties"]["altitude_from_ft"] for f in self.feat])
        self.hi = np.array([f["properties"]["altitude_to_ft"] for f in self.feat])

    def find(self, lat, lon, alt):
        """Index of the sector containing (lat,lon) in the matching altitude band, or None."""
        p = Point(lon, lat)
        for idx in self.tree.query(p):
            if self.lo[idx] <= alt < self.hi[idx] and self.geoms[idx].contains(p):
                return idx
        return None

# -------------------------------------------------------------------------
# 3. Weather
# -------------------------------------------------------------------------
def strip_times(scenario, kind="refc"):
    """Return sorted list of (valid_from_unix, valid_to_unix, path)."""
    out = []
    for p in glob.glob(os.path.join(scenario, "wx", kind, "*.npz")):
        b = os.path.basename(p)[:-4]
        # based_at_validfrom_validto, each YYYY-MM-DD_HH:MM:SS  -> 6 underscore-split chunks
        parts = b.split("_")
        vf = datetime.strptime(parts[2]+"_"+parts[3], "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc)
        vt = datetime.strptime(parts[4]+"_"+parts[5], "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc)
        out.append((vf.timestamp(), vt.timestamp(), p))
    return sorted(out)

def latlon_to_rc(lat, lon):
    i = ((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * ROWS).astype(int)
    j = ((lon - LON_MIN) / (LON_MAX - LON_MIN) * COLS).astype(int)
    ok = (i >= 0) & (i < ROWS) & (j >= 0) & (j < COLS)
    return np.clip(i, 0, ROWS-1), np.clip(j, 0, COLS-1), ok
