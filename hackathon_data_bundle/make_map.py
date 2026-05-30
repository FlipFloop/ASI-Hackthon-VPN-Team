import sys, bisect, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPoly
from matplotlib.collections import PatchCollection
from collections import defaultdict
from datetime import datetime, timezone
from shapely.geometry import shape
import atm

SCEN = sys.argv[1] if len(sys.argv) > 1 else "asked_at_2025-07-08T22:00:00Z"
meta, flights = atm.load_flights(SCEN)
sec = atm.Sectors()
refc_s = atm.strip_times(SCEN, "refc"); retop_s = atm.strip_times(SCEN, "retop")
vf = [s[0] for s in refc_s]; vt = [s[1] for s in refc_s]
def sidx(t): return min(max(bisect.bisect_right(vf, t)-1, 0), len(refc_s)-1)

# pick the most congested instant
peakloads = {}
best_t, best_over = vf[0], -1
for v0, v1 in zip(vf, vt):
    t=(v0+v1)/2; load=defaultdict(int)
    for f in flights:
        p=f.position(t)
        if p is None: continue
        idx=sec.find(p[0],p[1],f.alt)
        if idx is not None: load[idx]+=1
    nover=sum(1 for i,n in load.items() if n>sec.cap[i])
    if nover>best_over: best_over,best_t,peakloads=nover,t,dict(load)

s = sidx(best_t)
refc = np.where(np.load(refc_s[s][2])["matrix"]<=-50, np.nan, np.load(refc_s[s][2])["matrix"])
tstr = datetime.fromtimestamp(best_t, timezone.utc).strftime("%Y-%m-%d %H:%MZ")

fig, ax = plt.subplots(figsize=(14,8))
ax.imshow(refc, extent=[atm.LON_MIN,atm.LON_MAX,atm.LAT_MIN,atm.LAT_MAX],
          origin="upper", vmin=-20, vmax=60, cmap="turbo", alpha=0.55)

# LOW sectors colored by load/capacity at this instant
patches, ratios = [], []
for i, f in enumerate(sec.feat):
    if not sec.name[i].startswith("LOW_"): continue
    g = sec.geoms[i]
    if g.geom_type!="Polygon": continue
    patches.append(MplPoly(list(g.exterior.coords)))
    ratios.append(peakloads.get(i,0)/sec.cap[i])
pc = PatchCollection(patches, cmap="Reds", alpha=0.45, edgecolor="grey", linewidth=0.3)
pc.set_array(np.array(ratios)); pc.set_clim(0,1.5)
ax.add_collection(pc)
plt.colorbar(pc, label="LOW sector load / capacity", shrink=0.7)

# overlay flight tracks that hit weather (impacted)
n_imp=0
for f in flights:
    a,b=max(f.t0,vf[0]),min(f.t1,vt[-1])
    if b<=a: continue
    ts=np.arange(a,b,180)
    la=np.array([f.position(t)[0] for t in ts]); lo=np.array([f.position(t)[1] for t in ts])
    ri,cj,ok=atm.latlon_to_rc(la,lo)
    imp=False
    for nn,t in enumerate(ts):
        if not ok[nn]: continue
        ss=sidx(t); rv=np.load(refc_s[ss][2])["matrix"][ri[nn],cj[nn]]
        if rv>=40:
            tv=np.load(retop_s[ss][2])["matrix"][ri[nn],cj[nn]]
            if tv>=0 and f.alt<=tv: imp=True; break
    if imp:
        ax.plot(f.lons, f.lats, color="black", lw=0.4, alpha=0.5)
        n_imp+=1

ax.set_xlim(-126,-66); ax.set_ylim(22,50)
ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
ax.set_title(f"{SCEN}\nWeather (turbo) + LOW sector load/cap (reds) at {tstr}; "
             f"{best_over} sectors over capacity, {n_imp} weather-impacted routes (black)")
fig.savefig("scenario_map.png", dpi=110, bbox_inches="tight")
print("wrote scenario_map.png |", tstr, "| sectors_over=",best_over,"| impacted_routes=",n_imp)
