# NAS Traffic & Weather Visualization

Interactive map of US flight traffic, weather (radar reflectivity / echo top),
and weather conflicts, built from the hackathon data bundle.

- **MapLibre GL** dark basemap
- **deck.gl** layers: animated flights, weather raster overlay, sector demand,
  route trails
- Flight positions are interpolated **live in the browser** along each route
- Weather conflicts (flight in `>= 40 dBZ` at/above its altitude) are
  **precomputed** per flight and highlighted in red
- Sector demand (flights-in-sector vs capacity) is **precomputed** on a 5-min
  grid; over-capacity sectors are drawn red

## Run

```bash
# 1. (once) build the web assets from the data bundle
../.venv/bin/python preprocess.py           # flights + weather  (~25s)
../.venv/bin/python preprocess_sectors.py   # sector demand      (~13s)
# pass a date substring to either for a single snapshot, e.g. ... preprocess.py 2025-07-14

# 2. serve it (file:// won't work — fetch is CORS-blocked)
./serve.sh                                  # http://localhost:8765/index.html
```

## AI chatbot

A docked chat panel turns plain-English what-ifs — *"no-fly zone over Chicago,
what happens to flights?"* — into a real run of the engine, redraws the map, and
replies with an accessible, key-impact-first summary. The AI only **extracts the
parameters** and **narrates the real numbers**; it never invents a figure.

It's client-driven: the tools execute in the browser against the in-page engine
(`window.AIRSPACE` in `app.js`, over `applyNFZ` / `meterArrivals`), and `serve.py`
exposes a thin, stateless Claude proxy at `POST /api/chat` (model `claude-opus-4-8`,
prompt caching on the system prompt + tool schemas).

**Requires an Anthropic API key in the environment** — start the server with it set:

```bash
ANTHROPIC_API_KEY=sk-... ./serve.sh         # the key never reaches the browser
```

`serve.sh` runs the data-bundle venv's Python, which has the `anthropic` package.
Without a key the map still works; only the chat is disabled (it returns a clear
message saying so).

See **[AIChatbotGUIDE.md](AIChatbotGUIDE.md)** for the full workflow, the two tools
(`simulate_no_fly_zone`, `analyze_hub`), the system-prompt rules, accessibility
notes, and how to add a new tool.

## Deep links

`?snap=<i>&t=<0..1>&sectors=HIGH|LOW&arrows=1&trails=1` — pick snapshot, set time
as a fraction of the window, turn on a sector band, and/or enable heading arrows
and motion trails. Example:
`index.html?snap=9&t=0.13&sectors=LOW&arrows=1`

## Controls

- **Snapshot** dropdown — switch between the 11 `asked_at` snapshots
- **Play / time slider** — scrub/animate through the ~18h window; weather
  advances in sync (15-min forecast strips)
- **Speed** — sim-seconds per real second
- **Layers** — flights, reflectivity (refc), echo top (retop), conflicts-only,
  hovered-flight route
- **Direction arrows** — draw planes as chevrons rotated to their heading
  (instead of dots)
- **Plane trails** — fading motion trail behind every plane (deck.gl TripsLayer)
- **Sectors** dropdown — overlay LOW or HIGH band sectors colored by demand;
  red = over capacity; status bar shows how many are over capacity right now
- **Draw no-fly zone** — drag a keep-out box on the map; every affected flight is
  rerouted around it (or grounded if its origin/destination is inside), the
  sector-demand cascade is recomputed, and the detour is **priced in fuel / $ /
  CO₂** (see below)
- **Fuel $/gal** slider — the price used to value reroute detours; drag it and the
  cost readout updates live (the right decision moves with the fuel market). On
  load it auto-pulls the **live** market price via `/api/fuel` (served by
  `serve.py`, proxying the toolkit's `fuelfeed.py` — ULSD/NY-Harbor front-month);
  the chip shows `● live` vs `manual`, and **use live** resets to market. If the
  page is served by a plain static server (no `/api/fuel`), it falls back to the
  slider default.
- Hover a flight for callsign / route / altitude / conflict status, or a sector
  for its demand / capacity

### Reroute fuel cost (from the algorithm toolkit)

When a no-fly zone is active, the panel reports the fleet-wide cost of flying
around it: total extra distance, extra fuel (kg), extra fuel **$**, and extra
**CO₂** (kg), at the current `Fuel $/gal`. The cost model is ported directly from
the hackathon toolkit so the viz and the algorithm agree:

- burn curve = `holdcost.burn_kghr` (cruise-speed → kg/hr, piecewise-linear)
- cost chain = `autoroute._package`:
  `extra_kg = extra_nm/spd·burn`, `$ = kg/JETA_KG_PER_GAL·price`,
  `CO₂ = kg·CO2_PER_KG_FUEL` (constants 3.04 / 3.16)

It's computed in the browser per reroute — no backend — reusing the new-vs-filed
distance the reroute already calculates.

### Air-hold vs gate-hold, decided by the live fuel price

Rerouting also shifts when planes arrive, so the no-fly-zone panel meters each
**impacted** destination airport at the **Acceptance rate (arrivals/hr)** slider and
prices the resulting holds — this is where the live fuel price decides *circle vs
ground*. Ported from `holdcost.py`:

- **air-hold** (a plane already airborne circles the field) burns cruise
  `burn_kghr` at the live $/gal — expensive; past `AIR_RESERVE_MIN` (45 min) it
  flags a **diversion risk**.
- **gate-hold** (a plane not yet departed simply leaves later) burns only APU
  (`GATE_BURN_KGHR`) — cheap.
- **CDM substitution** reshuffles each carrier's *own* flights across its *own*
  RBS slots (`holdcost._assign`) so the cheap/gate-holdable planes absorb the wait
  and the expensive jets land on time — the panel shows the **$ + CO₂ it saves vs
  RBS** and how it cuts **diversion-risk** flights. Drag the **Acceptance rate** or
  **Fuel $/gal** sliders and these numbers move live.

## How it's built

`preprocess.py` reads `../hackathon_data_bundle/` and writes to `public/data/`:

- `snapshots.json` — master index: timing + per-snapshot weather strip table
- `<snapshot>/flights.json` — slimmed flights (waypoints, times, altitude) plus
  `cf`: weather-conflict intervals as strip-index ranges
- `<snapshot>/wx/refc_*.png`, `retop_*.png` — georeferenced RGBA rasters
  (turbo for refc, viridis for retop, nodata transparent)

`preprocess_sectors.py` adds:

- `sectors.json` — slimmed sector polygons (name, band, capacity) loaded once
- `<snapshot>/sectors_demand.json` — flights-per-sector counts on a 5-min grid
  (point-in-polygon via shapely STRtree, split by altitude band)

The frontend (`public/app.js`) does all per-frame flight interpolation and
weather-strip selection; the heavy point-sampling for conflicts is done once,
offline, in Python.

## Not yet included

- Per-flight A\* reroute via the Python `autoroute.reroute` (the in-browser
  reroute is a fast visibility-graph approximation; the cost model is the toolkit's)
- Per-sector demand timeline chart (click a sector to plot its curve)
