import sys, time, bisect, numpy as np
from collections import defaultdict
import atm

SCEN = sys.argv[1] if len(sys.argv) > 1 else "asked_at_2025-07-08T22:00:00Z"

t_start = time.time()
meta, flights = atm.load_flights(SCEN)
sec = atm.Sectors()

# ---- preload all weather grids ----
refc_strips = atm.strip_times(SCEN, "refc")
retop_strips = atm.strip_times(SCEN, "retop")
vf_list = [s[0] for s in refc_strips]
vt_list = [s[1] for s in refc_strips]
REFC = [np.load(p)["matrix"] for _, _, p in refc_strips]
RETOP = [np.load(p)["matrix"] for _, _, p in retop_strips]
horizon0, horizon1 = vf_list[0], vt_list[-1]
print(f"scenario {SCEN}")
print(f"flights={len(flights)} sectors={len(sec.feat)} weather strips={len(REFC)} "
      f"(15-min, ~{(horizon1-horizon0)/3600:.1f}h horizon)\n")

def strip_idx(t):
    i = bisect.bisect_right(vf_list, t) - 1
    return min(max(i, 0), len(REFC)-1)

# ============================================================
# PASS 1 — weather conflict by tracing each flight through time
# ============================================================
DT = 180  # 3-min steps along each route
in_wx = 0          # flights that touch >=40dBZ at/below echo top
near_wx = 0        # flights that pass over >=40dBZ regardless of altitude
enroute_in_window = 0
for f in flights:
    a, b = max(f.t0, horizon0), min(f.t1, horizon1)
    if b <= a:
        continue
    enroute_in_window += 1
    ts = np.arange(a, b, DT)
    pts = [f.position(t) for t in ts]
    lats = np.array([p[0] for p in pts]); lons = np.array([p[1] for p in pts])
    ri, cj, ok = atm.latlon_to_rc(lats, lons)
    hit_any = False; hit_alt = False
    for n, t in enumerate(ts):
        if not ok[n]:
            continue
        si = strip_idx(t)
        rv = REFC[si][ri[n], cj[n]]
        if rv >= atm.REFC_THRESH:
            hit_any = True
            tv = RETOP[si][ri[n], cj[n]]
            if tv >= 0 and f.alt <= tv:
                hit_alt = True
                break
    near_wx += hit_any
    in_wx += hit_alt

print(f"En-route during forecast horizon: {enroute_in_window} flights")
print(f"Routes crossing heavy weather (>=40dBZ) at any altitude: {near_wx} "
      f"({100*near_wx/enroute_in_window:.1f}%)")
print(f"Routes actually impacted (>=40dBZ AND cruise alt <= echo top): {in_wx} "
      f"({100*in_wx/enroute_in_window:.1f}%)\n")

# ============================================================
# PASS 2 — sector demand at full 15-min resolution
# ============================================================
peak_load = defaultdict(int)
overdemand_events = 0; total_cells = 0
overdemand_sectors = set()
worst_overload = 0
timeline = []  # (time, n_sectors_over)
for si_t, (vf, vt) in enumerate(zip(vf_list, vt_list)):
    t = (vf + vt) / 2.0
    load = defaultdict(int)
    for f in flights:
        pos = f.position(t)
        if pos is None:
            continue
        idx = sec.find(pos[0], pos[1], f.alt)
        if idx is not None:
            load[idx] += 1
    n_over = 0
    for idx, n in load.items():
        peak_load[idx] = max(peak_load[idx], n)
        total_cells += 1
        if n > sec.cap[idx]:
            overdemand_events += 1
            overdemand_sectors.add(idx)
            worst_overload = max(worst_overload, n - sec.cap[idx])
            n_over += 1
    timeline.append((t, n_over))

print(f"Sector-time cells occupied: {total_cells}")
print(f"Over-demand cells (load>capacity): {overdemand_events} "
      f"({100*overdemand_events/max(total_cells,1):.1f}%)")
print(f"Distinct sectors ever over capacity: {len(overdemand_sectors)} / {len(sec.feat)}")
print(f"Worst single overload: +{worst_overload} flights over capacity")

# busiest moment
busiest = max(timeline, key=lambda x: x[1])
from datetime import datetime, timezone
bt = datetime.fromtimestamp(busiest[0], timezone.utc).strftime("%H:%M")
print(f"Most-congested instant: {bt}Z with {busiest[1]} sectors simultaneously over capacity\n")

rows = sorted(((pk/sec.cap[i], sec.name[i], pk, sec.cap[i]) for i, pk in peak_load.items()),
              reverse=True)
print("Top 12 most-stressed sectors (peak_load / capacity):")
print(f"  {'sector':10} {'peak':>5} {'cap':>4} {'ratio':>6}")
for ratio, name, pk, cap in rows[:12]:
    print(f"  {name:10} {pk:5d} {cap:4d} {ratio:6.2f}")

print(f"\n[done in {time.time()-t_start:.1f}s]")
