#!/usr/bin/env python3
"""
Iterative global Ground Delay Program (GDP) via Ration-By-Schedule (RBS).

Determinism we exploit: a snapshot is the full forward schedule, and every plan
flies at constant speed, so each flight's entry/exit time into every sector is a
fixed function of its departure time. A ground delay of Δ shifts the whole
trajectory (and thus *every* sector interval) later by exactly Δ. We use that to
assign ground delays that keep sector occupancy <= capacity, minimizing total
ground delay with first-scheduled-first-served (RBS) fairness.

Algorithm (iterative, global):
  loop:
    recount occupancy of every sector from current (delayed) intervals
    pick the worst over-demand sector S (largest peak overshoot)
    RBS on S: airborne flights are fixed (can't be ground-delayed); pre-departure
      flights are admitted in original-entry order, each given the smallest extra
      delay so S never exceeds capacity
    if no controllable flight could help S (peak is driven by airborne traffic),
      mark S unresolvable and skip it
  until no resolvable over-demand sector remains (or caps hit)

Outputs per snapshot (into public/data/<snap>/):
  sectors_demand_gdp.json   post-GDP demand, same shape as sectors_demand.json
  gdp.json                  per-flight delays + before/after metrics
"""
import json
import os
import sys
from collections import defaultdict
from glob import glob

import numpy as np
from shapely import STRtree, points as shp_points

import preprocess_sectors as ps  # shared loaders, sectors, parse_iso, paths

SAMPLE_DT = 60          # s — trajectory sampling for entry/exit detection
GRID_DT = ps.INTERVAL   # 300 s — demand grid (matches sectors_demand.json)
MAX_ITERS = 400


def band_of(alt):
    return "HIGH" if alt >= 35000 else "LOW"


def compute_intervals(routes, bands, trees):
    """For every flight, the list of sector-occupancy segments (band, sec_idx,
    enter, exit) at zero delay. Uses one batched STRtree query per band."""
    flights = []
    band_pts = {b: {"x": [], "y": [], "fi": []} for b in ("HIGH", "LOW")}

    for fl in routes["flights"]:
        lats = np.asarray(fl["lats"], float)
        lons = np.asarray(fl["lons"], float)
        if len(lats) < 2:
            continue
        t0 = ps.parse_iso(fl["take_off_time"])
        t1 = ps.parse_iso(fl["scheduled_landing_time"])
        if t1 <= t0:
            continue
        rad = np.pi / 180
        dlat = np.diff(lats) * rad
        dlon = np.diff(lons) * rad
        a = np.sin(dlat / 2) ** 2 + np.cos(lats[:-1] * rad) * \
            np.cos(lats[1:] * rad) * np.sin(dlon / 2) ** 2
        seg = 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        total = cum[-1] or 1.0
        n = max(2, int((t1 - t0) // SAMPLE_DT) + 1)
        times = np.linspace(t0, t1, n)
        targets = (times - t0) / (t1 - t0) * total
        plat = np.interp(targets, cum, lats)
        plon = np.interp(targets, cum, lons)
        band = band_of(fl["cruise_altitude_ft"])

        fi = len(flights)
        bp = band_pts[band]
        bp["x"].extend(plon.tolist())
        bp["y"].extend(plat.tolist())
        bp["fi"].extend([fi] * n)
        flights.append({
            "fn": fl["flight_number"], "o": fl["origin_airport_icao"],
            "d": fl["destination_airport_icao"], "t0": t0, "t1": t1,
            "alt": fl["cruise_altitude_ft"], "spd": fl["cruise_speed_kt"],
            "air": bool(fl.get("is_airborne", False)),
            "band": band, "times": times, "segs": [],
        })

    # resolve each sample to a sector, then collapse consecutive samples into runs
    for band in ("HIGH", "LOW"):
        bp = band_pts[band]
        if not bp["x"]:
            continue
        pts = shp_points(np.column_stack([bp["x"], bp["y"]]))
        pt_idx, sec_idx = trees[band].query(pts, predicate="within")
        secof = np.full(len(bp["x"]), -1, dtype=np.int64)
        secof[pt_idx] = sec_idx  # boundary ties: last write wins (negligible)
        fis = np.asarray(bp["fi"])

        i, N = 0, len(fis)
        while i < N:  # samples are grouped per flight, in time order
            fi = fis[i]
            j = i
            while j < N and fis[j] == fi:
                j += 1
            secs = secof[i:j]
            times = flights[fi]["times"]
            k, m = 0, len(secs)
            while k < m:
                s = secs[k]
                if s < 0:
                    k += 1
                    continue
                kk = k
                while kk < m and secs[kk] == s:
                    kk += 1
                enter = float(times[k])
                exit_ = float(times[kk - 1]) + SAMPLE_DT
                flights[fi]["segs"].append((band, int(s), enter, exit_))
                k = kk
            i = j
    return flights


def sector_intervals(flights, delays):
    """(band, sec_idx) -> list of (enter, exit, flight_idx, airborne), delayed."""
    sec = defaultdict(list)
    for fi, fl in enumerate(flights):
        dl = delays[fi]
        air = fl["air"]
        for (band, si, enter, exit_) in fl["segs"]:
            sec[(band, si)].append((enter + dl, exit_ + dl, fi, air))
    return sec


def peak(intervals):
    ev = []
    for (s, e, _fi, _air) in intervals:
        ev.append((s, 1))
        ev.append((e, -1))
    ev.sort()
    cur = mx = 0
    for _t, d in ev:
        cur += d
        if cur > mx:
            mx = cur
    return mx


def over_demand_minutes(sec, caps):
    """Sum over sectors of integral of max(0, occupancy - capacity) dt, in minutes."""
    total = 0.0
    for key, iv in sec.items():
        cap = caps[key]
        ev = []
        for (s, e, _fi, _air) in iv:
            ev.append((s, 1))
            ev.append((e, -1))
        ev.sort()
        cur = 0
        prev = None
        for t, d in ev:
            if prev is not None and cur > cap:
                total += (t - prev) * (cur - cap)
            cur += d
            prev = t
    return total / 60.0


def earliest_start(admitted, e_i, d_i, C):
    """Smallest start s >= e_i such that adding [s, s+d_i] keeps occupancy < C
    everywhere in the window (so total stays <= C). admitted: list of (start,end)."""
    s = e_i
    while True:
        checkpoints = [s] + [a for (a, b) in admitted if s <= a < s + d_i]
        bad = False
        for cp in checkpoints:
            cnt = 0
            for (a, b) in admitted:
                if a <= cp < b:
                    cnt += 1
            if cnt >= C:
                bad = True
                break
        if not bad:
            return s
        later_ends = [b for (a, b) in admitted if b > s]
        if not later_ends:
            return s
        s = min(later_ends)


def run_gdp(flights, caps):
    delays = [0.0] * len(flights)
    skip = set()
    iters = 0
    for it in range(MAX_ITERS):
        sec = sector_intervals(flights, delays)
        worst, worst_over = None, 0
        for key, iv in sec.items():
            if key in skip:
                continue
            over = peak(iv) - caps[key]
            if over > worst_over:
                worst_over, worst = over, key
        if worst is None:
            break
        iters = it + 1
        C = caps[worst]
        iv = sec[worst]
        admitted = [(s, e) for (s, e, _fi, air) in iv if air]      # immovable
        ctrl = sorted(((s, e, fi) for (s, e, fi, air) in iv if not air),
                      key=lambda x: x[0])                          # RBS order
        added = 0.0
        for (enter, exit_, fi) in ctrl:
            d_i = exit_ - enter
            ns = earliest_start(admitted, enter, d_i, C)
            add = ns - enter
            if add > 1e-6:
                delays[fi] += add
                added += add
            admitted.append((ns, ns + d_i))
        if added <= 1e-6:
            skip.add(worst)  # peak driven by airborne traffic — GDP can't fix it
    return delays, iters, skip


def demand_on_grid(flights, delays, gs, n_steps, bands):
    out = {"HIGH": {}, "LOW": {}}
    sec = sector_intervals(flights, delays)
    for (band, si), iv in sec.items():
        name = bands[band][si]["name"]
        row = np.zeros(n_steps, dtype=int)
        for (s, e, _fi, _air) in iv:
            lo = int(np.ceil((s - gs) / GRID_DT))
            hi = int(np.floor((e - 1e-6 - gs) / GRID_DT))
            lo = max(lo, 0)
            hi = min(hi, n_steps - 1)
            if hi >= lo:
                row[lo:hi + 1] += 1
        if row.any():
            out[band][name] = row.tolist()
    return out


def count_over_exact(sec, caps):
    """Sectors whose instantaneous peak occupancy exceeds capacity — the true
    safety condition the RBS loop targets (the 300s grid is only for the viz)."""
    return sum(1 for key, iv in sec.items() if peak(iv) > caps[key])


def process_snapshot(snap_dir, snap_id, bands, trees, caps):
    with ps.open_maybe_gz(os.path.join(snap_dir, "routes.json")) as f:
        routes = json.load(f)
    gs = ps.parse_iso(routes["window_start"])
    we = ps.parse_iso(routes["window_end"])
    n_steps = int(np.ceil((we - gs) / GRID_DT)) + 1

    flights = compute_intervals(routes, bands, trees)
    zero = [0.0] * len(flights)

    before_demand = demand_on_grid(flights, zero, gs, n_steps, bands)
    before_sec = sector_intervals(flights, zero)
    before_over = count_over_exact(before_sec, caps)
    before_min = over_demand_minutes(before_sec, caps)

    delays, iters, skip = run_gdp(flights, caps)

    after_demand = demand_on_grid(flights, delays, gs, n_steps, bands)
    after_sec = sector_intervals(flights, delays)
    after_over = count_over_exact(after_sec, caps)
    after_min = over_demand_minutes(after_sec, caps)

    held = [(fi, d) for fi, d in enumerate(delays) if d > 1e-6]
    total_delay = sum(d for _fi, d in held)
    max_delay = max((d for _fi, d in held), default=0.0)

    out_dir = os.path.join(ps.OUT, snap_id)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "sectors_demand_gdp.json"), "w") as f:
        json.dump({"grid_start": gs, "interval": GRID_DT, "n_steps": n_steps,
                   "HIGH": after_demand["HIGH"], "LOW": after_demand["LOW"]},
                  f, separators=(",", ":"))

    summary = {
        "snapshot": snap_id,
        "iterations": iters,
        "n_flights": len(flights),
        "n_held": len(held),
        "total_delay_min": round(total_delay / 60.0, 1),
        "mean_delay_min": round((total_delay / len(held) / 60.0), 1) if held else 0.0,
        "max_delay_min": round(max_delay / 60.0, 1),
        "over_demand_sectors_before": before_over,
        "over_demand_sectors_after": after_over,
        "over_demand_minutes_before": round(before_min, 1),
        "over_demand_minutes_after": round(after_min, 1),
        "unresolvable_sectors": sorted(bands[b][i]["name"] for (b, i) in skip),
        "delays": sorted(
            [{"fn": flights[fi]["fn"], "o": flights[fi]["o"], "d": flights[fi]["d"],
              "t0": round(flights[fi]["t0"]), "delay_min": round(d / 60.0, 1)}
             for fi, d in held],
            key=lambda x: -x["delay_min"]),
    }
    with open(os.path.join(out_dir, "gdp.json"), "w") as f:
        json.dump(summary, f, separators=(",", ":"))

    print(f"  {snap_id}: iters={iters} held={len(held)} "
          f"total_delay={summary['total_delay_min']}min "
          f"max={summary['max_delay_min']}min | "
          f"over-demand sectors {before_over}->{after_over} | "
          f"over-demand min {summary['over_demand_minutes_before']}"
          f"->{summary['over_demand_minutes_after']}"
          + (f" | unresolvable={len(skip)}" if skip else ""))
    return summary


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    bands = ps.load_sectors()
    trees = {b: STRtree([s["geom"] for s in bands[b]]) for b in ("HIGH", "LOW")}
    caps = {(b, i): bands[b][i]["capacity"]
            for b in ("HIGH", "LOW") for i in range(len(bands[b]))}

    print("Running iterative global GDP (RBS, min total delay)...")
    for d in sorted(glob(os.path.join(ps.BUNDLE, "asked_at_*"))):
        snap_id = os.path.basename(d)
        if only and only not in snap_id:
            continue
        process_snapshot(d, snap_id, bands, trees, caps)
    print("Done.")


if __name__ == "__main__":
    main()
