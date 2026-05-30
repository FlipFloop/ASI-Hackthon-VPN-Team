import glob, bisect, time, numpy as np
from collections import defaultdict
import atm

sec = atm.Sectors()
DT = 180
print(f"{'scenario':30} {'flights':>7} {'air':>5} {'wx%':>5} {'impact%':>7} "
      f"{'over_sec':>8} {'maxover':>7} {'peakratio':>9}")
print("-"*92)

for SCEN in sorted(glob.glob("asked_at_*")):
    meta, flights = atm.load_flights(SCEN)
    refc_s = atm.strip_times(SCEN, "refc"); retop_s = atm.strip_times(SCEN, "retop")
    vf = [s[0] for s in refc_s]; vt = [s[1] for s in refc_s]
    REFC = [np.load(p)["matrix"] for *_, p in refc_s]
    RETOP = [np.load(p)["matrix"] for *_, p in retop_s]
    h0, h1 = vf[0], vt[-1]
    def sidx(t): return min(max(bisect.bisect_right(vf, t)-1, 0), len(REFC)-1)

    air = sum(1 for f in flights if f.airborne)
    # weather impact
    impacted = crossed = enroute = 0
    for f in flights:
        a, b = max(f.t0, h0), min(f.t1, h1)
        if b <= a: continue
        enroute += 1
        ts = np.arange(a, b, DT)
        la = np.array([f.position(t)[0] for t in ts]); lo = np.array([f.position(t)[1] for t in ts])
        ri, cj, ok = atm.latlon_to_rc(la, lo)
        hc = hi = False
        for n, t in enumerate(ts):
            if not ok[n]: continue
            s = sidx(t); rv = REFC[s][ri[n], cj[n]]
            if rv >= atm.REFC_THRESH:
                hc = True
                tv = RETOP[s][ri[n], cj[n]]
                if tv >= 0 and f.alt <= tv: hi = True; break
        crossed += hc; impacted += hi
    # demand
    peak = defaultdict(int); over_sec = set(); maxover = 0
    for v0, v1 in zip(vf, vt):
        t = (v0+v1)/2; load = defaultdict(int)
        for f in flights:
            p = f.position(t)
            if p is None: continue
            idx = sec.find(p[0], p[1], f.alt)
            if idx is not None: load[idx] += 1
        for idx, n in load.items():
            peak[idx] = max(peak[idx], n)
            if n > sec.cap[idx]:
                over_sec.add(idx); maxover = max(maxover, n-sec.cap[idx])
    peakratio = max(pk/sec.cap[i] for i, pk in peak.items())
    name = SCEN.replace("asked_at_","").replace("Z","")
    print(f"{name:30} {len(flights):7d} {air:5d} {100*crossed/enroute:5.1f} "
          f"{100*impacted/enroute:7.1f} {len(over_sec):8d} {maxover:7d} {peakratio:9.2f}")
