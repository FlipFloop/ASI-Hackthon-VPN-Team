"""autoroute.py — cost-aware flight rerouting around no-fly / hazard zones.

Takes an original flight path + one or more no-fly polygons, runs A* over a
lat/lon grid whose edge costs blend DISTANCE (fuel), TRAFFIC CONGESTION (from
concurrent flights) and a SAFETY BUFFER around hazards, then string-pulls the
grid path into a clean route. Returns the new waypoints, the fuel/$/CO2 delta
vs the original, and a ready-to-render GeoJSON FeatureCollection.

Designed as a LOOSE, parameterized plan: every cost term has a weight you can
tune, and `blocked_zones` can carry no-fly polygons OR weather volumes (any
"keep out of here" region) — the algorithm doesn't care which.

    from autoroute import reroute
    out = reroute(flight, no_fly_zones=[[[lat,lon],...]], fuel_usd_per_gal=3.67,
                  concurrent_flights=others)
    geojson = out["geojson"]      # drop straight onto Leaflet / Mapbox

Coordinate conventions (read this!):
  * no_fly_zones  -> list of polygons, each a list of [lat, lon] points.
  * flight        -> object/dict with parallel lats[], lons[] (+ cruise_speed_kt,
                     cruise_altitude_ft, take_off_time, scheduled_landing_time).
  * GeoJSON OUT   -> standard [lon, lat] order, so map libraries render as-is.
"""
import heapq, math, json

# ---- optional reuse of project modules (graceful fallback if absent) --------
try:
    from atm import haversine_nm
except Exception:                                  # standalone fallback
    def haversine_nm(lat1, lon1, lat2, lon2):
        R = 3440.065
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return 2 * R * math.asin(math.sqrt(a))
try:
    from holdcost import burn_kghr            # kg/hr from cruise speed
except Exception:
    def burn_kghr(spd):
        return 350 + max(0.0, (spd - 150)) / 360.0 * 2950.0

CO2_PER_KG_FUEL = 3.16
JETA_KG_PER_GAL = 3.04

# default cost weights — the "personality" of the router (all tunable)
DEFAULTS = dict(
    grid_res_nm=22.0,      # grid cell size (smaller = finer/slower)
    pad_deg=2.0,           # how far outside the box to extend the grid
    buffer_nm=35.0,        # stay this far clear of a no-fly edge (soft)
    w_congestion=0.9,      # weight on traffic-density avoidance (0 = ignore)
    w_buffer=1.4,          # weight on the hazard safety buffer
    w_turn=0.15,           # penalty for heading changes (route smoothness)
    diagonal=True,         # 8-neighbour moves
)


# -------------------------------------------------------------------------
def _poly_contains(poly, lat, lon):
    """Ray-cast point-in-polygon. poly = [[lat,lon],...]."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i][0], poly[i][1]
        yj, xj = poly[j][0], poly[j][1]
        if ((yi > lat) != (yj > lat)) and \
           (lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _seg_hits_zone(lat1, lon1, lat2, lon2, zones, steps=24):
    for s in range(steps + 1):
        f = s / steps
        la = lat1 + f * (lat2 - lat1); lo = lon1 + f * (lon2 - lon1)
        if any(_poly_contains(z, la, lo) for z in zones):
            return True
    return False


def _path_waypoints(flight):
    if isinstance(flight, dict):
        return list(flight["lats"]), list(flight["lons"])
    return list(flight.lats), list(flight.lons)

def _attr(flight, name, default=None):
    if isinstance(flight, dict):
        return flight.get(name, default)
    return getattr(flight, {"cruise_speed_kt": "spd",
                            "cruise_altitude_ft": "alt"}.get(name, name), default)


# -------------------------------------------------------------------------
def reroute(flight, no_fly_zones, *, fuel_usd_per_gal=None,
            concurrent_flights=None, params=None):
    """Compute a cost-optimal reroute around `no_fly_zones`.

    Returns a dict: original / rerouted route stats, cost delta, and a GeoJSON
    FeatureCollection (original line, reroute line, no-fly polygons)."""
    P = {**DEFAULTS, **(params or {})}
    zones = [_norm_zone(z) for z in no_fly_zones]
    lats, lons = _path_waypoints(flight)
    o_lat, o_lon = lats[0], lons[0]
    d_lat, d_lon = lats[-1], lons[-1]

    spd = float(_attr(flight, "cruise_speed_kt", 450) or 450)
    alt = float(_attr(flight, "cruise_altitude_ft", 35000) or 35000)
    burn = burn_kghr(spd)
    fuel_price = fuel_usd_per_gal if fuel_usd_per_gal is not None else _live_price()

    orig_nm = _poly_len(list(zip(lats, lons)))

    # does the original path even hit a zone? if not, no reroute needed.
    hits = any(_seg_hits_zone(lats[i], lons[i], lats[i+1], lons[i+1], zones)
               for i in range(len(lats) - 1))
    if not hits:
        return _package(flight, list(zip(lats, lons)), list(zip(lats, lons)),
                        orig_nm, orig_nm, spd, burn, fuel_price, zones,
                        rerouted=False, note="original path clear of all zones")

    # ---- build the search grid -------------------------------------------
    grid = _Grid(o_lat, o_lon, d_lat, d_lon, zones, P)
    grid.add_congestion(concurrent_flights, P)

    cells = grid.astar()
    if cells is None:                       # boxed in -> fall back to original
        return _package(flight, list(zip(lats, lons)), list(zip(lats, lons)),
                        orig_nm, orig_nm, spd, burn, fuel_price, zones,
                        rerouted=False, note="no feasible path found (zone seals route)")

    pts = [grid.cell_latlon(r, c) for (r, c) in cells]
    pts = [(o_lat, o_lon)] + pts + [(d_lat, d_lon)]
    pts = _smooth(pts, zones)               # string-pull out the grid staircase
    new_nm = _poly_len(pts)

    # baseline = same grid/traffic but zones removed -> isolates the zone's cost
    base_cells = grid.astar(ignore_blocked=True)
    base_pts = [(o_lat, o_lon)] + [grid.cell_latlon(r, c) for r, c in base_cells] + [(d_lat, d_lon)]
    base_pts = _smooth(base_pts, [])
    base_nm = _poly_len(base_pts)

    return _package(flight, list(zip(lats, lons)), pts, base_nm, new_nm,
                    spd, burn, fuel_price, zones, rerouted=True,
                    note="rerouted around %d zone(s)" % len(zones),
                    filed_nm=orig_nm)


# -------------------------------------------------------------------------
class _Grid:
    def __init__(self, o_lat, o_lon, d_lat, d_lon, zones, P):
        pts = [(o_lat, o_lon), (d_lat, d_lon)] + [p for z in zones for p in z]
        la = [p[0] for p in pts]; lo = [p[1] for p in pts]
        self.lat0, self.lat1 = min(la) - P["pad_deg"], max(la) + P["pad_deg"]
        self.lon0, self.lon1 = min(lo) - P["pad_deg"], max(lo) + P["pad_deg"]
        midlat = (self.lat0 + self.lat1) / 2
        self.dlat = P["grid_res_nm"] / 60.0
        self.dlon = P["grid_res_nm"] / (60.0 * max(0.2, math.cos(math.radians(midlat))))
        self.nr = max(4, int((self.lat1 - self.lat0) / self.dlat) + 1)
        self.nc = max(4, int((self.lon1 - self.lon0) / self.dlon) + 1)
        self.zones = zones; self.P = P
        self.blocked = [[False]*self.nc for _ in range(self.nr)]
        self.buf = [[0.0]*self.nc for _ in range(self.nr)]
        self.cong = [[0.0]*self.nc for _ in range(self.nr)]
        self._mark_zones()
        self.start = self._nearest(o_lat, o_lon)
        self.goal = self._nearest(d_lat, d_lon)
        self.blocked[self.start[0]][self.start[1]] = False
        self.blocked[self.goal[0]][self.goal[1]] = False

    def cell_latlon(self, r, c):
        return (self.lat0 + r * self.dlat, self.lon0 + c * self.dlon)

    def _nearest(self, lat, lon):
        r = min(self.nr - 1, max(0, round((lat - self.lat0) / self.dlat)))
        c = min(self.nc - 1, max(0, round((lon - self.lon0) / self.dlon)))
        return (r, c)

    def _mark_zones(self):
        buf_cells = self.P["buffer_nm"] / self.P["grid_res_nm"]
        for r in range(self.nr):
            for c in range(self.nc):
                la, lo = self.cell_latlon(r, c)
                if any(_poly_contains(z, la, lo) for z in self.zones):
                    self.blocked[r][c] = True
        # soft buffer ring: distance (in cells) to nearest blocked cell
        for r in range(self.nr):
            for c in range(self.nc):
                if self.blocked[r][c]:
                    continue
                best = 999
                rad = int(buf_cells) + 1
                for dr in range(-rad, rad + 1):
                    for dc in range(-rad, rad + 1):
                        rr, cc = r + dr, c + dc
                        if 0 <= rr < self.nr and 0 <= cc < self.nc and self.blocked[rr][cc]:
                            best = min(best, math.hypot(dr, dc))
                if best <= buf_cells:
                    self.buf[r][c] = (buf_cells - best) / buf_cells   # 0..1

    def add_congestion(self, flights, P):
        """Coarse traffic-density field from concurrent flights' waypoints.
        HOOK: swap for time/altitude-aware sector load for the real system."""
        if not flights:
            return
        mx = 0
        for f in flights:
            fl, fo = _path_waypoints(f)
            for k in range(len(fl) - 1):           # sample along each leg
                for s in range(6):
                    la = fl[k] + s/6*(fl[k+1]-fl[k]); lo = fo[k] + s/6*(fo[k+1]-fo[k])
                    r = round((la - self.lat0) / self.dlat)
                    c = round((lo - self.lon0) / self.dlon)
                    if 0 <= r < self.nr and 0 <= c < self.nc:
                        self.cong[r][c] += 1; mx = max(mx, self.cong[r][c])
        if mx:
            for r in range(self.nr):
                for c in range(self.nc):
                    self.cong[r][c] /= mx                # normalise 0..1

    def _cell_cost_mult(self, r, c):
        P = self.P
        return 1.0 + P["w_buffer"] * self.buf[r][c] + P["w_congestion"] * self.cong[r][c]

    def astar(self, ignore_blocked=False):
        """ignore_blocked=True gives the BASELINE path (same traffic field, but
        zones removed) so the reroute's extra cost isolates the zone's impact."""
        P = self.P
        nbrs = [(-1,0),(1,0),(0,-1),(0,1)]
        if P["diagonal"]:
            nbrs += [(-1,-1),(-1,1),(1,-1),(1,1)]
        glat, glon = self.cell_latlon(*self.goal)
        def h(r, c):
            la, lo = self.cell_latlon(r, c); return haversine_nm(la, lo, glat, glon)
        start, goal = self.start, self.goal
        g = {start: 0.0}; came = {}; prevdir = {start: None}
        pq = [(h(*start), start)]
        while pq:
            _, cur = heapq.heappop(pq)
            if cur == goal:
                path = [cur]
                while cur in came:
                    cur = came[cur]; path.append(cur)
                return path[::-1]
            cr, cc = cur
            la0, lo0 = self.cell_latlon(cr, cc)
            for dr, dc in nbrs:
                nr, nc = cr + dr, cc + dc
                if not (0 <= nr < self.nr and 0 <= nc < self.nc):
                    continue
                if self.blocked[nr][nc] and not ignore_blocked:
                    continue
                la1, lo1 = self.cell_latlon(nr, nc)
                mult = 1.0 if ignore_blocked else self._cell_cost_mult(nr, nc)
                step = haversine_nm(la0, lo0, la1, lo1) * mult
                if prevdir[cur] is not None and prevdir[cur] != (dr, dc):
                    step += P["w_turn"] * self.P["grid_res_nm"]      # turn penalty
                ng = g[cur] + step
                nxt = (nr, nc)
                if ng < g.get(nxt, float("inf")):
                    g[nxt] = ng; came[nxt] = cur; prevdir[nxt] = (dr, dc)
                    heapq.heappush(pq, (ng + h(nr, nc), nxt))
        return None


# -------------------------------------------------------------------------
def _smooth(pts, zones):
    """String-pulling: keep a point only if the straight shortcut past it would
    clip a zone. Yields a clean polyline instead of a grid staircase."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]; i = 0
    while i < len(pts) - 1:
        j = len(pts) - 1
        while j > i + 1:
            if not _seg_hits_zone(pts[i][0], pts[i][1], pts[j][0], pts[j][1], zones):
                break
            j -= 1
        out.append(pts[j]); i = j
    return out


def _poly_len(pts):
    return sum(haversine_nm(pts[k][0], pts[k][1], pts[k+1][0], pts[k+1][1])
               for k in range(len(pts) - 1))


def _norm_zone(z):
    """Accept [[lat,lon],...] or a GeoJSON-style {'coordinates':[[[lon,lat],...]]}.
    Returns [[lat,lon],...]."""
    if isinstance(z, dict):
        ring = z["coordinates"][0]
        return [[p[1], p[0]] for p in ring]
    return [[p[0], p[1]] for p in z]


def _live_price():
    try:
        from fuelfeed import get_price
        return get_price()["usd_per_gal"]
    except Exception:
        return 2.50


def _package(flight, filed_pts, new_pts, baseline_nm, new_nm, spd, burn,
             fuel_price, zones, rerouted, note, filed_nm=None):
    filed_nm = filed_nm if filed_nm is not None else _poly_len(filed_pts)
    extra_nm = max(0.0, new_nm - baseline_nm)     # cost attributable to the zone
    extra_min = extra_nm / max(spd, 1) * 60.0
    extra_kg = extra_min / 60.0 * burn
    extra_usd = extra_kg / JETA_KG_PER_GAL * fuel_price
    extra_co2 = extra_kg * CO2_PER_KG_FUEL

    def line(pts):                          # GeoJSON wants [lon, lat]
        return [[round(lo, 4), round(la, 4)] for (la, lo) in pts]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"role": "original", "stroke": "#7a8aa0"},
         "geometry": {"type": "LineString", "coordinates": line(filed_pts)}},
        {"type": "Feature", "properties": {"role": "reroute", "stroke": "#39d98a"},
         "geometry": {"type": "LineString", "coordinates": line(new_pts)}},
    ] + [
        {"type": "Feature", "properties": {"role": "no_fly", "fill": "#ff5470"},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[p[1], p[0]] for p in z] +
                                      [[z[0][1], z[0][0]]]]}}
        for z in zones
    ]}

    return {
        "rerouted": rerouted, "note": note,
        "original":  {"distance_nm": round(filed_nm, 1),
                      "duration_min": round(filed_nm / max(spd,1) * 60, 1),
                      "waypoints": [[round(la,4), round(lo,4)] for la,lo in filed_pts]},
        "baseline_direct_nm": round(baseline_nm, 1),
        "reroute":   {"distance_nm": round(new_nm, 1),
                      "duration_min": round(new_nm / max(spd,1) * 60, 1),
                      "extra_nm": round(extra_nm, 1),
                      "extra_min": round(extra_min, 1),
                      "waypoints": [[round(la,4), round(lo,4)] for la,lo in new_pts]},
        "cost": {"fuel_price_usd_gal": round(fuel_price, 3),
                 "extra_fuel_kg": round(extra_kg, 0),
                 "extra_fuel_usd": round(extra_usd, 0),
                 "extra_co2_kg": round(extra_co2, 0),
                 "burn_kghr": round(burn, 0)},
        "geojson": fc,
    }


# -------------------------------------------------------------------------
if __name__ == "__main__":
    # demo: take a real transcon flight, drop a no-fly box on its path
    from atm import load_flights
    _, flights = load_flights("asked_at_2025-07-01T21:30:00Z")
    # find a long flight to make the detour visible
    f = max(flights, key=lambda x: haversine_nm(x.lats[0], x.lons[0],
                                                x.lats[-1], x.lons[-1]))
    midlat = (f.lats[0] + f.lats[-1]) / 2; midlon = (f.lons[0] + f.lons[-1]) / 2
    box = [[midlat-1.6, midlon-1.6], [midlat-1.6, midlon+1.6],
           [midlat+1.6, midlon+1.6], [midlat+1.6, midlon-1.6]]   # ~3°x3° no-fly
    out = reroute(f, [box], fuel_usd_per_gal=3.67,
                  concurrent_flights=flights[:1500])
    print(f"{f.fn}  {f.orig}->{f.dest}  {int(f.spd)}kt")
    print("note:", out["note"])
    print("original  %.0f nm" % out["original"]["distance_nm"])
    print("reroute   %.0f nm  (+%.0f nm, +%.1f min)" % (
        out["reroute"]["distance_nm"], out["reroute"]["extra_nm"],
        out["reroute"]["extra_min"]))
    print("cost: +%d kg fuel  +$%d  +%d kg CO2  @ $%.2f/gal" % (
        out["cost"]["extra_fuel_kg"], out["cost"]["extra_fuel_usd"],
        out["cost"]["extra_co2_kg"], out["cost"]["fuel_price_usd_gal"]))
    print("reroute waypoints:", len(out["reroute"]["waypoints"]),
          "| geojson features:", len(out["geojson"]["features"]))
    with open("/tmp/reroute_demo.geojson", "w") as fh:
        json.dump(out["geojson"], fh)
    print("wrote /tmp/reroute_demo.geojson")
