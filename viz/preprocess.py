#!/usr/bin/env python3
"""
Preprocess the hackathon data bundle into web-app-ready assets.

For each asked_at snapshot it produces:
  - data/<snap>/flights.json   slimmed flight list + per-flight weather conflict intervals
  - data/<snap>/wx/refc_<k>.png, retop_<k>.png  georeferenced weather rasters
  - an entry in data/snapshots.json (master index with timing + weather strip table)

Conflict model (per docs): a flight is "in weather" at a location/time when the
local composite reflectivity >= REFC_THRESHOLD dBZ AND the local echo top
(retop, ft) >= the flight's cruise altitude. Sampled at each 15-min weather
strip the flight is airborne for.
"""
import gzip
import io
import json
import os
import sys
from datetime import datetime, timezone
from glob import glob

import numpy as np
import matplotlib
from PIL import Image

# ---- grid / weather constants (from documentation/wx/FILE_FORMAT.md) ----
LAT_MIN, LAT_MAX = 21.943, 55.7765
LON_MIN, LON_MAX = -135.0, -67.5
ROWS, COLS = 256, 358
REFC_THRESHOLD = 40.0  # dBZ; >= this is impassable weather

BUNDLE = os.path.join(os.path.dirname(__file__), "..", "hackathon_data_bundle")
OUT = os.path.join(os.path.dirname(__file__), "public", "data")


def parse_iso(s: str) -> float:
    """ISO8601 -> unix epoch seconds."""
    return datetime.fromisoformat(s).timestamp()


def parse_strip_name(fname: str):
    """'<based>_<from>_<to>.npz' (each YYYY-MM-DD_HH:MM:SS) -> (from_epoch, to_epoch)."""
    stem = os.path.basename(fname)[:-4]  # drop .npz
    p = stem.split("_")  # 6 tokens
    vf = datetime.strptime(
        f"{p[2]} {p[3]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    vt = datetime.strptime(
        f"{p[4]} {p[5]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return vf.timestamp(), vt.timestamp()


def load_matrix(path: str) -> np.ndarray:
    return np.load(path)["matrix"]


def latlon_to_ij(lat: float, lon: float):
    i = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * ROWS)
    j = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * COLS)
    if 0 <= i < ROWS and 0 <= j < COLS:
        return i, j
    return None


def cumulative_distances(lats, lons):
    """Cumulative great-circle distance (km) along the polyline; returns array len N."""
    lat = np.radians(np.asarray(lats, dtype=float))
    lon = np.radians(np.asarray(lons, dtype=float))
    dlat = np.diff(lat)
    dlon = np.diff(lon)
    a = np.sin(dlat / 2) ** 2 + \
        np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2) ** 2
    seg = 2 * 6371.0 * np.arcsin(np.sqrt(a))
    return np.concatenate([[0.0], np.cumsum(seg)])


def position_at_fraction(lats, lons, cum, frac):
    """Position at arc-length fraction (0..1) along the route."""
    total = cum[-1]
    if total <= 0:
        return lats[0], lons[0]
    target = frac * total
    k = int(np.searchsorted(cum, target))
    if k <= 0:
        return lats[0], lons[0]
    if k >= len(cum):
        return lats[-1], lons[-1]
    span = cum[k] - cum[k - 1]
    t = 0.0 if span <= 0 else (target - cum[k - 1]) / span
    return lats[k - 1] + t * (lats[k] - lats[k - 1]), lons[k - 1] + t * (lons[k] - lons[k - 1])


def render_weather_png(matrix: np.ndarray, kind: str, out_path: str):
    """Render a (256,358) matrix to an RGBA PNG with nodata transparent."""
    if kind == "refc":
        nodata = matrix <= -50
        norm = np.clip((matrix - (-20)) / (60 - (-20)), 0, 1)
        cmap = matplotlib.colormaps["turbo"]
    else:  # retop
        nodata = matrix < 0
        norm = np.clip(matrix / 60000.0, 0, 1)
        cmap = matplotlib.colormaps["viridis"]
    rgba = (cmap(norm) * 255).astype(np.uint8)
    rgba[nodata] = (0, 0, 0, 0)
    # also drop very-low refc so the map isn't a solid blue wash
    if kind == "refc":
        rgba[(~nodata) & (matrix < 5)] = (0, 0, 0, 0)
    Image.fromarray(rgba, "RGBA").save(out_path)


def merge_strip_indices(idxs):
    """[2,3,4,7,8] -> [[2,4],[7,8]] (inclusive index intervals)."""
    if not idxs:
        return []
    idxs = sorted(idxs)
    out = [[idxs[0], idxs[0]]]
    for k in idxs[1:]:
        if k == out[-1][1] + 1:
            out[-1][1] = k
        else:
            out.append([k, k])
    return out


def open_maybe_gz(path_base):
    """Return a text stream for path_base or path_base+'.gz'."""
    if os.path.exists(path_base):
        return open(path_base, "rt")
    if os.path.exists(path_base + ".gz"):
        return gzip.open(path_base + ".gz", "rt")
    raise FileNotFoundError(path_base)


def process_snapshot(snap_dir: str, snap_id: str):
    print(f"  {snap_id}", flush=True)
    with open_maybe_gz(os.path.join(snap_dir, "routes.json")) as f:
        routes = json.load(f)

    # ---- weather strips: sort by valid_from, render PNGs, build time table ----
    refc_files = sorted(glob(os.path.join(snap_dir, "wx", "refc", "*.npz")),
                        key=lambda p: parse_strip_name(p)[0])
    wx_out = os.path.join(OUT, snap_id, "wx")
    os.makedirs(wx_out, exist_ok=True)

    strips = []
    refc_mats, retop_mats = [], []
    for k, rf in enumerate(refc_files):
        vf, vt = parse_strip_name(rf)
        rt = os.path.join(snap_dir, "wx", "retop", os.path.basename(rf))
        refc_m = load_matrix(rf)
        retop_m = load_matrix(rt) if os.path.exists(rt) else None
        render_weather_png(
            refc_m, "refc", os.path.join(wx_out, f"refc_{k}.png"))
        if retop_m is not None:
            render_weather_png(retop_m, "retop", os.path.join(
                wx_out, f"retop_{k}.png"))
        refc_mats.append(refc_m)
        retop_mats.append(retop_m)
        strips.append({"k": k, "from": vf, "to": vt,
                       "refc": f"wx/refc_{k}.png",
                       "retop": f"wx/retop_{k}.png" if retop_m is not None else None})

    strip_from = np.array([s["from"] for s in strips])
    strip_to = np.array([s["to"] for s in strips])

    # ---- flights: slim + conflict detection ----
    out_flights = []
    for fl in routes["flights"]:
        lats = fl["lats"]
        lons = fl["lons"]
        if len(lats) < 2:
            continue
        t0 = parse_iso(fl["take_off_time"])
        t1 = parse_iso(fl["scheduled_landing_time"])
        if t1 <= t0:
            continue
        alt = fl["cruise_altitude_ft"]
        cum = cumulative_distances(lats, lons)

        # which strips overlap the airborne window?
        lo = int(np.searchsorted(strip_to, t0))
        hi = int(np.searchsorted(strip_from, t1))
        conflict_strips = []
        for k in range(max(0, lo), min(len(strips), hi)):
            retop_m = retop_mats[k]
            if retop_m is None:
                continue
            tmid = 0.5 * (strip_from[k] + strip_to[k])
            if tmid < t0 or tmid > t1:
                tmid = min(max(tmid, t0), t1)
            frac = (tmid - t0) / (t1 - t0)
            lat, lon = position_at_fraction(lats, lons, cum, frac)
            ij = latlon_to_ij(lat, lon)
            if ij is None:
                continue
            i, j = ij
            refc_v = refc_mats[k][i, j]
            retop_v = retop_m[i, j]
            if refc_v >= REFC_THRESHOLD and retop_v >= alt:
                conflict_strips.append(k)

        out_flights.append({
            "fn": fl["flight_number"],
            "o": fl["origin_airport_icao"],
            "d": fl["destination_airport_icao"],
            "t0": round(t0),
            "t1": round(t1),
            "alt": alt,
            "spd": fl["cruise_speed_kt"],
            "air": bool(fl.get("is_airborne", False)),
            # rounded waypoints to shrink payload
            "la": [round(float(x), 4) for x in lats],
            "lo": [round(float(x), 4) for x in lons],
            # conflict strip-index intervals
            "cf": merge_strip_indices(conflict_strips),
        })

    os.makedirs(os.path.join(OUT, snap_id), exist_ok=True)
    with open(os.path.join(OUT, snap_id, "flights.json"), "w") as f:
        json.dump({"flights": out_flights}, f, separators=(",", ":"))

    n_conf = sum(1 for fl in out_flights if fl["cf"])
    return {
        "id": snap_id,
        "asked_at": parse_iso(routes["asked_at"]),
        "window_start": parse_iso(routes["window_start"]),
        "window_end": parse_iso(routes["window_end"]),
        "n_flights": len(out_flights),
        "n_conflict": n_conf,
        "strips": strips,
    }


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    snap_dirs = sorted(glob(os.path.join(BUNDLE, "asked_at_*")))
    os.makedirs(OUT, exist_ok=True)
    index = []
    print("Processing snapshots...")
    for d in snap_dirs:
        snap_id = os.path.basename(d)
        if only and only not in snap_id:
            continue
        index.append(process_snapshot(d, snap_id))
    # merge into existing index if doing a partial run
    idx_path = os.path.join(OUT, "snapshots.json")
    if only and os.path.exists(idx_path):
        existing = {s["id"]: s for s in json.load(open(idx_path))["snapshots"]}
        for s in index:
            existing[s["id"]] = s
        index = sorted(existing.values(), key=lambda s: s["asked_at"])
    with open(idx_path, "w") as f:
        json.dump({"snapshots": index,
                   "grid": {"lat_min": LAT_MIN, "lat_max": LAT_MAX,
                            "lon_min": LON_MIN, "lon_max": LON_MAX},
                   "refc_threshold": REFC_THRESHOLD}, f, indent=0)
    print(f"Done. {len(index)} snapshot(s) in index.")


if __name__ == "__main__":
    main()
