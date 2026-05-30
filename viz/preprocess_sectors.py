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
from datetime import datetime, timezone
from glob import glob

import numpy as np
from shapely import STRtree, points as shp_points
from shapely.geometry import shape

BUNDLE = os.path.join(os.path.dirname(__file__), "..", "hackathon_data_bundle")
OUT = os.path.join(os.path.dirname(__file__), "public", "data")
INTERVAL = 300  # seconds between demand samples

# ---- weather grid (from documentation/wx/FILE_FORMAT.md) — for storm-cap ----
LAT_MIN, LAT_MAX = 21.943, 55.7765
LON_MIN, LON_MAX = -135.0, -67.5
ROWS, COLS = 256, 358
REFC_HEAVY = 40.0   # dBZ; >= this reduces sector capacity (storm cell)
STORMCAP_NEAR_S = 8 * 60  # snap a timestep to a strip within this many seconds


def parse_iso(s):
    return datetime.fromisoformat(s).timestamp()


def parse_strip_name(fname):
    """'<based>_<from>_<to>.npz' (each YYYY-MM-DD_HH:MM:SS, UTC) -> (from, to) epoch."""
    p = os.path.basename(fname)[:-4].split("_")  # 6 tokens
    vf = datetime.strptime(f"{p[2]} {p[3]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    vt = datetime.strptime(f"{p[4]} {p[5]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return vf.timestamp(), vt.timestamp()


def compute_storm_boxes(snap_dir, coarse_deg=0.75, min_cell=2, min_total=4, cap=15):
    """Storm keep-out boxes per weather strip: cluster heavy (>=40 dBZ) cells onto
    a coarse lat/lon grid, connected-component them, and emit each cluster's padded
    bounding box [w, s, e, n]. The frontend reroutes flights around these. Returns
    a list indexed by strip k (same order as the snapshot's strips)."""
    files = sorted(glob(os.path.join(snap_dir, "wx", "refc", "*.npz")),
                   key=lambda p: parse_strip_name(p)[0])
    out = []
    for p in files:
        m = np.load(p)["matrix"]
        ii, jj = np.where(m >= REFC_HEAVY)
        grid = {}
        for i, j in zip(ii.tolist(), jj.tolist()):
            lat = LAT_MAX - (i + 0.5) * (LAT_MAX - LAT_MIN) / ROWS
            lon = LON_MIN + (j + 0.5) * (LON_MAX - LON_MIN) / COLS
            gr = int((LAT_MAX - lat) / coarse_deg)
            gc = int((lon - LON_MIN) / coarse_deg)
            grid[(gr, gc)] = grid.get((gr, gc), 0) + 1
        marked = {c for c, n in grid.items() if n >= min_cell}
        boxes, seen = [], set()
        for cell in marked:
            if cell in seen:
                continue
            comp, stack = [], [cell]
            seen.add(cell)
            while stack:
                gr, gc = stack.pop()
                comp.append((gr, gc))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nb = (gr + dr, gc + dc)
                    if nb in marked and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            total = sum(grid[c] for c in comp)
            if total < min_total:
                continue
            grs = [c[0] for c in comp]
            gcs = [c[1] for c in comp]
            n = LAT_MAX - min(grs) * coarse_deg + 0.2
            s = LAT_MAX - (max(grs) + 1) * coarse_deg - 0.2
            w = LON_MIN + min(gcs) * coarse_deg - 0.2
            e = LON_MIN + (max(gcs) + 1) * coarse_deg + 0.2
            boxes.append((total, [round(w, 2), round(s, 2), round(e, 2), round(n, 2)]))
        boxes.sort(key=lambda b: -b[0])
        out.append([b[1] for b in boxes[:cap]])
    return out


def compute_stormcap(snap_dir, ws, n_steps, bands, trees):
    """Per-sector, per-timestep capacity-reduction factor (0..1) from heavy
    precip, ported from App/build_data.py §4. A sector loses 30–100% capacity
    when refc >= 40 dBZ cells sit inside it, scaled by storm severity (intensity
    + coverage). Output shape mirrors sectors_demand: {band: {name: [f per step]}}."""
    files = sorted(glob(os.path.join(snap_dir, "wx", "refc", "*.npz")),
                   key=lambda p: parse_strip_name(p)[0])
    out = {"HIGH": {}, "LOW": {}}
    if not files:
        return out
    strips = [(parse_strip_name(p), p) for p in files]

    # aggregate heavy cells -> containing sector, once per strip
    strip_agg = {}  # path -> {(band, name): [heavy_count, max_dBZ]}
    for (_vf, _vt), p in strips:
        m = np.load(p)["matrix"]
        ii, jj = np.where(m >= REFC_HEAVY)
        agg = {}
        if len(ii):
            lats = LAT_MAX - (ii + 0.5) * (LAT_MAX - LAT_MIN) / ROWS
            lons = LON_MIN + (jj + 0.5) * (LON_MAX - LON_MIN) / COLS
            vals = m[ii, jj]
            for band in ("HIGH", "LOW"):
                pts = shp_points(np.column_stack([lons, lats]))
                pt_idx, sec_idx = trees[band].query(pts, predicate="within")
                for pk, sk in zip(pt_idx, sec_idx):
                    key = (band, bands[band][sk]["name"])
                    v = float(vals[pk])
                    a = agg.get(key)
                    if a is None:
                        agg[key] = [1, v]
                    else:
                        a[0] += 1
                        a[1] = max(a[1], v)
        strip_agg[p] = agg

    def reduction(cnt, maxv):
        intensity = max(0.0, min(1.0, (maxv - 40) / 15.0))  # 40 dBZ->0, 55->1
        coverage = max(0.0, min(1.0, cnt / 6.0))            # 6+ cells -> full
        sev = max(0.0, min(1.0, 0.5 * intensity + 0.5 * coverage))
        return round(0.3 + 0.7 * sev, 2)                    # 0.30 .. 1.00

    for k in range(n_steps):
        t = ws + k * INTERVAL
        chosen = next((p for (vf, vt), p in strips if vf <= t < vt), None)
        if chosen is None:  # mid-gap: snap to nearest strip start within 8 min
            best, bd = None, STORMCAP_NEAR_S
            for (vf, _vt), p in strips:
                if abs(vf - t) <= bd:
                    bd, best = abs(vf - t), p
            chosen = best
        if chosen is None:
            continue
        for (band, name), (cnt, maxv) in strip_agg.get(chosen, {}).items():
            arr = out[band].setdefault(name, [0.0] * n_steps)
            arr[k] = reduction(cnt, maxv)

    for band in ("HIGH", "LOW"):  # keep only sectors ever reduced
        out[band] = {k: v for k, v in out[band].items() if any(v)}
    return out


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

    # storm-driven capacity reduction (weather -> effective sector capacity)
    stormcap = compute_stormcap(snap_dir, ws, n_steps, bands, trees)
    n_storm = len(stormcap["HIGH"]) + len(stormcap["LOW"])
    with open(os.path.join(OUT, snap_id, "sectors_stormcap.json"), "w") as f:
        json.dump({"grid_start": ws, "interval": INTERVAL, "n_steps": n_steps,
                   "HIGH": stormcap["HIGH"], "LOW": stormcap["LOW"]},
                  f, separators=(",", ":"))

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
    return {"n_steps": n_steps, "peak": peak, "over_sectors": over,
            "storm_sectors": n_storm}


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
              f"over_demand_sectors={stats['over_sectors']} "
              f"storm_reduced_sectors={stats['storm_sectors']}")
    print("Done.")


if __name__ == "__main__":
    main()
