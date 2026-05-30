#!/usr/bin/env python3
"""
Precompute ATC sector demand over time for the visualization.

For each asked_at snapshot, samples every flight's position on a regular time
grid, assigns it to a sector (point-in-polygon, per altitude band), and counts
flights per sector per time step. Output per snapshot:

  public/data/<snap>/sectors_demand.json
    { "grid_start": <epoch>, "interval": <s>, "n_steps": N,
      "HIGH": { "HIGH_006": [c0, c1, ...], ... },
      "LOW":  { "LOW_006":  [c0, c1, ...], ... } }   # only sectors that ever > 0

Also writes a slimmed sectors file the frontend loads once:
  public/data/sectors.json   (geometry + name + band + capacity)

Band rule: a flight at cruise altitude `alt` is in HIGH if alt >= 35000 else LOW
(HIGH sectors cover [35000,60000), LOW cover [0,35000)).
"""
import gzip
import json
import os
import sys
from datetime import datetime
from glob import glob

import numpy as np
from shapely import STRtree, points as shp_points
from shapely.geometry import shape

BUNDLE = os.path.join(os.path.dirname(__file__), "..", "hackathon_data_bundle")
OUT = os.path.join(os.path.dirname(__file__), "public", "data")
INTERVAL = 300  # seconds between demand samples


def parse_iso(s):
    return datetime.fromisoformat(s).timestamp()


def open_maybe_gz(path_base):
    if os.path.exists(path_base):
        return open(path_base, "rt")
    if os.path.exists(path_base + ".gz"):
        return gzip.open(path_base + ".gz", "rt")
    raise FileNotFoundError(path_base)


def load_sectors():
    with open_maybe_gz(os.path.join(BUNDLE, "sectors.geojson")) as f:
        gj = json.load(f)
    bands = {"HIGH": [], "LOW": []}
    for feat in gj["features"]:
        name = feat["properties"]["name"]
        band = "HIGH" if name.startswith("HIGH_") else "LOW"
        bands[band].append({
            "name": name,
            "capacity": feat["properties"]["capacity"],
            "geom": shape(feat["geometry"]),
            "feature": feat,
        })
    return bands


def write_frontend_sectors(bands):
    """Slimmed FeatureCollection with rounded coords for the GeoJsonLayer."""
    feats = []
    for band in ("HIGH", "LOW"):
        for s in bands[band]:
            f = s["feature"]
            geom = f["geometry"]
            rings = [[[round(x, 3), round(y, 3)] for x, y in ring]
                     for ring in geom["coordinates"]]
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": rings},
                "properties": {"name": s["name"], "band": band, "capacity": s["capacity"]},
            })
    with open(os.path.join(OUT, "sectors.json"), "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats},
                  f, separators=(",", ":"))


def flight_positions_on_grid(fl, grid):
    """Return (mask, lats, lons) for grid times where the flight is airborne."""
    t0 = parse_iso(fl["take_off_time"])
    t1 = parse_iso(fl["scheduled_landing_time"])
    if t1 <= t0 or len(fl["lats"]) < 2:
        return None
    active = (grid >= t0) & (grid <= t1)
    if not active.any():
        return None
    lat = np.asarray(fl["lats"], float)
    lon = np.asarray(fl["lons"], float)
    # cumulative great-circle distance for arc-length interpolation
    rad = np.pi / 180
    dlat = np.diff(lat) * rad
    dlon = np.diff(lon) * rad
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:-1] * rad) * \
        np.cos(lat[1:] * rad) * np.sin(dlon / 2) ** 2
    seg = 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    frac = (grid[active] - t0) / (t1 - t0)
    target = frac * (total if total > 0 else 1.0)
    plat = np.interp(target, cum, lat)
    plon = np.interp(target, cum, lon)
    return active, plat, plon


def process_snapshot(snap_dir, snap_id, bands, trees):
    with open_maybe_gz(os.path.join(snap_dir, "routes.json")) as f:
        routes = json.load(f)
    ws = parse_iso(routes["window_start"])
    we = parse_iso(routes["window_end"])
    n_steps = int(np.ceil((we - ws) / INTERVAL)) + 1
    grid = ws + np.arange(n_steps) * INTERVAL

    # accumulate per-step coordinate lists, split by band
    step_lon = {"HIGH": [list() for _ in range(n_steps)], "LOW": [
        list() for _ in range(n_steps)]}
    step_lat = {"HIGH": [list() for _ in range(n_steps)], "LOW": [
        list() for _ in range(n_steps)]}
    step_idx = np.arange(n_steps)

    for fl in routes["flights"]:
        res = flight_positions_on_grid(fl, grid)
        if res is None:
            continue
        active, plat, plon = res
        band = "HIGH" if fl["cruise_altitude_ft"] >= 35000 else "LOW"
        idxs = step_idx[active]
        for k, la, lo in zip(idxs, plat, plon):
            step_lat[band][k].append(la)
            step_lon[band][k].append(lo)

    demand = {"HIGH": {}, "LOW": {}}
    for band in ("HIGH", "LOW"):
        tree = trees[band]
        names = [s["name"] for s in bands[band]]
        counts = np.zeros((len(names), n_steps), dtype=np.int32)
        for k in range(n_steps):
            if not step_lon[band][k]:
                continue
            pts = shp_points(np.column_stack(
                [step_lon[band][k], step_lat[band][k]]))
            # query returns [input(point) indices, tree(sector) indices];
            # predicate is point.within(sector)
            _pt_idx, sec_idx = tree.query(pts, predicate="within")
            if len(sec_idx):
                np.add.at(counts[:, k], sec_idx, 1)
        for i, name in enumerate(names):
            row = counts[i]
            if row.any():
                demand[band][name] = row.tolist()

    out = {"grid_start": ws, "interval": INTERVAL, "n_steps": n_steps,
           "HIGH": demand["HIGH"], "LOW": demand["LOW"]}
    os.makedirs(os.path.join(OUT, snap_id), exist_ok=True)
    with open(os.path.join(OUT, snap_id, "sectors_demand.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))

    # quick stats
    peak = 0
    over = 0
    cap = {s["name"]: s["capacity"] for b in bands.values() for s in b}
    for band in ("HIGH", "LOW"):
        for name, row in demand[band].items():
            m = max(row)
            peak = max(peak, m)
            if m > cap[name]:
                over += 1
    return {"n_steps": n_steps, "peak": peak, "over_sectors": over}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    bands = load_sectors()
    print(f"sectors: HIGH={len(bands['HIGH'])} LOW={len(bands['LOW'])}")
    write_frontend_sectors(bands)
    trees = {b: STRtree([s["geom"] for s in bands[b]])
             for b in ("HIGH", "LOW")}

    for d in sorted(glob(os.path.join(BUNDLE, "asked_at_*"))):
        snap_id = os.path.basename(d)
        if only and only not in snap_id:
            continue
        stats = process_snapshot(d, snap_id, bands, trees)
        print(f"  {snap_id}: steps={stats['n_steps']} peak_in_sector={stats['peak']} "
              f"over_demand_sectors={stats['over_sectors']}")
    print("Done.")


if __name__ == "__main__":
    main()
