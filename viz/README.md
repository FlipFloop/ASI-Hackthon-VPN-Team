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
- Hover a flight for callsign / route / altitude / conflict status, or a sector
  for its demand / capacity

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

- Conflict resolution (rerouting / delay suggestions)
- Per-sector demand timeline chart (click a sector to plot its curve)
