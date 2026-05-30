# Reroute Algorithm — Integration Handoff

**What this is:** a self-contained, cost-aware flight rerouting algorithm (`autoroute.py`).
Give it a flight's path + a no-fly polygon and it returns a new route that flies *around*
the zone, plus the extra fuel / $ / CO₂ that detour costs, plus a **ready-to-render GeoJSON**
you can drop straight onto a Leaflet or Mapbox map.

It's a **loose, parameterized plan**, not a black box: every cost term has a tunable weight,
and the "no-fly zone" input is really just "any keep-out region" — so the same code handles
**weather volumes** or **temporary flight restrictions** with no changes.

> Verified working: on `DAL506 KMIA→KSEA` with a 6°×6° no-fly box on its path, it returns a
> reroute that provably clears the zone (every segment checked), costing **+60 nm / +$427 /
> +1,119 kg CO₂** at a live $3.67/gal. Runs in ~0.7 s including loading 14k concurrent flights.

---

## 1. The algorithm in one paragraph

It lays a lat/lon **grid** over the region, marks grid cells inside the no-fly polygon as
**blocked**, and runs **A\*** (optimal shortest-path search) from origin to destination. The
cost of moving between cells isn't just distance — it blends **distance (→ fuel)**, a
**traffic-congestion** penalty from concurrent flights, a **safety buffer** around the zone
edge, and a **turn penalty** for smoothness. The raw grid path is then **string-pulled** into
a clean polyline. Cost is measured against a **baseline** (the same A\* with the zone removed),
so the reported extra fuel/$/CO₂ is exactly *what the zone cost* — nothing else.

---

## 2. Files

| file | role |
|---|---|
| `autoroute.py` | **the algorithm.** One public function: `reroute(...)`. No web framework needed. |
| `reroute_viewer.html` | a ~15-line **reference Leaflet renderer** — copy its `draw()` function. |
| `server.py` | optional: exposes `GET /api/reroute` (demo) and serves the viewer at `/reroute`. |
| `fuelfeed.py` | optional: live jet-fuel price (used if you don't pass `fuel_usd_per_gal`). |
| `atm.py`, `holdcost.py` | optional reuse (haversine + burn model). `autoroute.py` has fallbacks if absent. |

`autoroute.py` only *needs* the Python standard library. Everything else is optional reuse.

---

## 3. Quickstart

```bash
python autoroute.py                      # runs the built-in demo, prints stats + writes GeoJSON
# or, with the server running:
python server.py                         # then open http://localhost:8000/reroute
```

---

## 4. The function contract (this is what you call)

```python
from autoroute import reroute

out = reroute(
    flight,                       # see "flight" below
    no_fly_zones,                 # list of polygons; each = [[lat, lon], [lat, lon], ...]
    fuel_usd_per_gal=3.67,        # optional; if omitted, pulls live price
    concurrent_flights=others,    # optional; list of other flights -> congestion avoidance
    params={...},                 # optional; cost-weight overrides (see §6)
)
```

### Inputs

**`flight`** — either an object with attributes or a dict, with at minimum:
```python
{
  "lats": [25.79, 30.1, ...],          # waypoints, parallel arrays. lats[0]=origin, lats[-1]=dest
  "lons": [-80.29, -85.4, ...],
  "cruise_speed_kt": 461,              # used for time/fuel; defaults to 450 if missing
  "cruise_altitude_ft": 35000,         # informational for now; defaults 35000
}
```
Only `lats`/`lons` are strictly required (first point = origin, last = destination).

**`no_fly_zones`** — a list of polygons. Each polygon is a list of `[lat, lon]` points
(ordered around the ring; don't repeat the first point). Example — a box:
```python
[ [[36,-104],[36,-100],[40,-100],[40,-104]] ]
```
GeoJSON-style polygons (`{"coordinates": [[[lon,lat],...]]}`) are also accepted.

**`concurrent_flights`** *(optional)* — a list of other `flight`-shaped objects. Used to build a
coarse traffic-density field so the reroute avoids already-crowded airspace. Omit it to ignore traffic.

### Output (a plain dict — JSON-serializable)

```jsonc
{
  "rerouted": true,
  "note": "rerouted around 1 zone(s)",
  "original":  { "distance_nm": 2487.0, "duration_min": 323.6,
                 "waypoints": [[lat,lon], ...] },     // the FILED route (for the gray line)
  "baseline_direct_nm": 2327.0,                       // best path if the zone didn't exist
  "reroute":   { "distance_nm": 2387.0, "duration_min": 310.8,
                 "extra_nm": 60.2,                    // <-- cost of the zone, vs baseline
                 "extra_min": 7.8,
                 "waypoints": [[lat,lon], ...] },      // the NEW route (for the green line)
  "cost": { "fuel_price_usd_gal": 3.669,
            "extra_fuel_kg": 367, "extra_fuel_usd": 427, "extra_co2_kg": 1119,
            "burn_kghr": 2700 },
  "geojson": { "type": "FeatureCollection", "features": [ /* see §5 */ ] }
}
```

`waypoints` are `[lat, lon]` (human order). The **`geojson` block uses `[lon, lat]`** (the
GeoJSON/Leaflet/Mapbox standard) so it renders with zero conversion.

---

## 5. Rendering on a JavaScript map

The `geojson` field is a standard `FeatureCollection` with three kinds of features, tagged by
`properties.role`: **`original`** (filed route), **`reroute`** (new route), **`no_fly`** (the zone).

### Leaflet (copy-paste)
```js
L.geoJSON(out.geojson, {
  style: f => ({
    original: { color:'#7a8aa0', weight:2, dashArray:'5,6' },  // gray dashed = filed
    reroute : { color:'#39d98a', weight:4 },                   // green = new route
    no_fly  : { color:'#ff5470', weight:1, fillColor:'#ff5470', fillOpacity:.25 }, // red zone
  }[f.properties.role])
}).addTo(map);
```
(That's exactly the `draw()` function in `reroute_viewer.html` — a working reference.)

### Mapbox GL JS
`out.geojson` is a valid source. Add it once, then three layers filtered on `['get','role']`:
```js
map.addSource('reroute', { type:'geojson', data: out.geojson });
map.addLayer({ id:'route-new', type:'line', source:'reroute',
  filter:['==',['get','role'],'reroute'], paint:{'line-color':'#39d98a','line-width':4} });
map.addLayer({ id:'route-old', type:'line', source:'reroute',
  filter:['==',['get','role'],'original'], paint:{'line-color':'#7a8aa0','line-width':2,'line-dasharray':[2,2]} });
map.addLayer({ id:'nofly', type:'fill', source:'reroute',
  filter:['==',['get','role'],'no_fly'], paint:{'fill-color':'#ff5470','fill-opacity':0.25} });
```

---

## 6. Tunable parameters (the "personality")

Pass any of these in `params={...}`:

| param | default | what it does |
|---|---|---|
| `grid_res_nm` | 22 | grid cell size in nautical miles. Smaller = finer routes, slower. |
| `pad_deg` | 2.0 | how far beyond the origin/dest/zone box to extend the search grid. |
| `buffer_nm` | 35 | soft "stay clear" margin around the zone edge (routes won't hug the boundary). |
| `w_congestion` | 0.9 | weight on avoiding crowded airspace (0 = ignore traffic). |
| `w_buffer` | 1.4 | weight on the safety buffer. |
| `w_turn` | 0.15 | penalty on heading changes → smoother routes. |
| `diagonal` | True | allow 8-direction moves (False = only N/S/E/W). |

Cranking `w_congestion` spreads traffic out; cranking `w_buffer` flies more conservatively.
**Exposing these as UI sliders is a great demo** — the route visibly changes as you drag them.

---

## 7. How to extend it (designed-in hooks)

- **Weather zones instead of no-fly:** weather is just another keep-out polygon. Convert a storm
  cell cluster (`refc ≥ 40 dBZ`) into a polygon and pass it in `no_fly_zones`. For altitude-aware
  weather (fly *over* a storm whose echo-top `retop` is below cruise), gate the block on
  `flight.cruise_altitude_ft > retop` when marking cells — see `_Grid._mark_zones`.
- **Real-time / sector congestion:** `_Grid.add_congestion` currently bins concurrent-flight
  waypoints into a density grid. Swap it for time-and-altitude-aware **sector load** (load/capacity)
  for the production version — the function is isolated and labeled `HOOK`.
- **Reroute-vs-delay decision:** rerouting is only one option. To also consider "wait at the gate
  until the zone clears, then fly the original route," price a delay with the **Hold-Cost Engine**
  (`holdcost.py`) and return the cheaper of the two. (The cost model is shared.)
- **Whole-fleet rerouting:** call `reroute` per flight in sequence, feeding each result's path back
  into the congestion field, so later flights see the crowding earlier reroutes created.

---

## 8. Assumptions & limitations (state these honestly)

- 2-D horizontal routing at cruise; altitude is informational (the `retop` over-fly hook is noted but off by default).
- Fuel/time use a **constant-cruise** model (no climb/descent profile) and a burn estimate from
  cruise speed — there's no aircraft-type data. Numbers are directional, not certified.
- Congestion is a coarse density proxy, not real-time sector occupancy (see the hook).
- The grid resolution (`grid_res_nm`) trades accuracy for speed; 22 nm is fine for CONUS-scale routes.

---

## 9. Give this to Claude (paste-ready prompt for your colleague)

> I have a Python rerouting module `autoroute.py` with one function:
> `reroute(flight, no_fly_zones, fuel_usd_per_gal=None, concurrent_flights=None, params=None)`.
> It returns a dict whose `geojson` field is a FeatureCollection where each feature has
> `properties.role` of `original`, `reroute`, or `no_fly` (see AlgorithmHandoff.md §4–5 for the
> exact shape). I want to integrate it into my existing [Leaflet / Mapbox] map visualizer so that:
> (1) when a user draws a no-fly polygon on the map, I POST its `[lat,lon]` points to a small
> endpoint that calls `reroute(...)` and returns the result; (2) the map then draws the filed
> route (gray dashed), the new route (green), and the zone (red) using `properties.role` for
> styling; (3) a side panel shows `out.reroute.extra_nm`, `out.cost.extra_fuel_usd`, and
> `out.cost.extra_co2_kg`. Here are my current map files: [paste]. Please wire it up, reusing the
> styling snippet from AlgorithmHandoff.md §5, and add the server endpoint if I don't have one.

Attach `autoroute.py`, this file, and `reroute_viewer.html` when you send that.
