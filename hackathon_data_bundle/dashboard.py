"""Time-animated ATM dashboard -> animated GIF.

Background = composite reflectivity (weather). LOW-band sector polygons are
shaded by load/capacity (white->red, red outline when over capacity). Flight
dots overlay current positions (black = normal, magenta = currently flying
through >=40 dBZ at/below echo top).
"""
import sys, bisect, time, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPoly
from matplotlib.collections import PatchCollection
from matplotlib.animation import FuncAnimation, PillowWriter
from collections import defaultdict
from datetime import datetime, timezone
import atm

SCEN = sys.argv[1] if len(sys.argv) > 1 else "asked_at_2025-07-08T22:00:00Z"
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 2     # use every STEPth strip as a frame
BAND = sys.argv[3] if len(sys.argv) > 3 else "LOW"      # LOW or HIGH band to shade

t0 = time.time()
meta, flights = atm.load_flights(SCEN)
sec = atm.Sectors()
refc_s = atm.strip_times(SCEN, "refc"); retop_s = atm.strip_times(SCEN, "retop")
frames = list(range(0, len(refc_s), STEP))
vf = [s[0] for s in refc_s]; vt = [s[1] for s in refc_s]

# band sector patches (built once)
band_idx = [i for i in range(len(sec.feat))
            if sec.name[i].startswith(BAND+"_") and sec.geoms[i].geom_type == "Polygon"]
patches = [MplPoly(list(sec.geoms[i].exterior.coords)) for i in band_idx]
caps = np.array([sec.cap[i] for i in band_idx])

# precompute per-frame state
print(f"precomputing {len(frames)} frames...")
FR = []
for fi in frames:
    t = (vf[fi] + vt[fi]) / 2.0
    raw = np.load(refc_s[fi][2])["matrix"]
    refc = np.where(raw < 5, np.nan, np.clip(raw, None, 60))  # show only real precip, clip outliers
    retop = np.load(retop_s[fi][2])["matrix"]
    load = defaultdict(int)
    fx, fy, fwx = [], [], []
    for f in flights:
        p = f.position(t)
        if p is None: continue
        la, lo = p
        idx = sec.find(la, lo, f.alt)
        if idx is not None: load[idx] += 1
        fx.append(lo); fy.append(la)
        ri, cj, ok = atm.latlon_to_rc(np.array([la]), np.array([lo]))
        wx = False
        if ok[0] and refc[ri[0], cj[0]] >= 40:
            tv = retop[ri[0], cj[0]]
            wx = tv >= 0 and f.alt <= tv
        fwx.append(wx)
    ratios = np.array([load.get(i, 0)/sec.cap[i] for i in band_idx])
    n_over = int(sum(1 for i in band_idx if load.get(i, 0) > sec.cap[i]))
    FR.append(dict(t=t, refc=refc, ratios=ratios,
                   fx=np.array(fx), fy=np.array(fy), fwx=np.array(fwx),
                   n_over=n_over, n_air=len(fx), n_wx=int(np.sum(fwx))))

# figure
fig, ax = plt.subplots(figsize=(13, 7.5))
ax.set_xlim(-126, -66); ax.set_ylim(23, 50)
ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
ext = [atm.LON_MIN, atm.LON_MAX, atm.LAT_MIN, atm.LAT_MAX]
im = ax.imshow(FR[0]["refc"], extent=ext, origin="upper", vmin=5, vmax=60,
               cmap="turbo", alpha=0.8, zorder=1)
pc = PatchCollection(patches, cmap="Reds", alpha=0.4, zorder=2)
pc.set_clim(0, 1.5); ax.add_collection(pc)
edge = PatchCollection(patches, facecolor="none", zorder=3)
ax.add_collection(edge)
sc_n = ax.scatter([], [], s=2, c="black", alpha=0.35, zorder=4)
sc_w = ax.scatter([], [], s=10, c="magenta", edgecolor="k", lw=0.2, zorder=5)
plt.colorbar(pc, label=f"{BAND} sector load / capacity", shrink=0.7)
title = ax.set_title("")

def update(k):
    d = FR[k]
    im.set_data(d["refc"])
    pc.set_array(d["ratios"])
    over = d["ratios"] > 1.0
    edge.set_edgecolor([("red" if o else "none") for o in over])
    edge.set_linewidth([1.2 if o else 0 for o in over])
    nrm = ~d["fwx"]
    sc_n.set_offsets(np.c_[d["fx"][nrm], d["fy"][nrm]] if nrm.any() else np.empty((0,2)))
    sc_w.set_offsets(np.c_[d["fx"][d["fwx"]], d["fy"][d["fwx"]]] if d["fwx"].any() else np.empty((0,2)))
    ts = datetime.fromtimestamp(d["t"], timezone.utc).strftime("%m-%d %H:%MZ")
    title.set_text(f"{SCEN}  |  {ts}  |  airborne={d['n_air']}  "
                   f"in-weather={d['n_wx']}  {BAND} sectors over-cap={d['n_over']}")
    return im, pc, edge, sc_n, sc_w, title

anim = FuncAnimation(fig, update, frames=len(FR), blit=False)
out = "dashboard.gif"
anim.save(out, writer=PillowWriter(fps=4))
print(f"wrote {out}  ({len(FR)} frames)  [{time.time()-t0:.1f}s]")
