# ATM data toolkit

Analysis + visualization scripts built on top of the data bundle.

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install shapely matplotlib numpy
```

## Files
| file | what it does |
|---|---|
| `atm.py` | Core primitives. `Flight.position(t)` interpolates a plane along its planned waypoints (constant-cruise model); `Sectors.find(lat,lon,alt)` does point-in-polygon + altitude band; `strip_times()` / `latlon_to_rc()` sample the weather grid. Import this from your own code. |
| `analyze.py` | Full congestion + weather-conflict report for one scenario. `… analyze.py <scenario_dir>` |
| `survey.py` | Cross-scenario comparison table (all 11 scenarios). |
| `make_map.py` | Static map of one congested instant (weather + sector load + impacted routes). |
| `dashboard.py` | **Time-animated GIF** over the 18h forecast: `… dashboard.py <scenario_dir> <strip_step> <LOW|HIGH>` |

## Dashboard legend (`dashboard.gif`)
- **turbo background** — composite reflectivity (storms), masked below 5 dBZ, clipped at 60.
- **red shading** — LOW (or HIGH) sector load ÷ capacity; **red outline** = currently over capacity.
- **black dots** — airborne flights; **magenta dots** — flights currently inside ≥40 dBZ at/below echo top.
- Title shows time, airborne count, in-weather count, and # sectors over capacity.

## Key conventions baked in
- Weather "impact" = `refc ≥ 40 dBZ` AND `cruise_altitude_ft ≤ retop` (echo top).
- `refc` has a handful of anomalous pixels > 60 dBZ per stormy scenario — clip them.
- Sector membership needs BOTH polygon containment AND the altitude band (HIGH ≥35k ft, LOW <35k ft).
- Flight time-along-route is proportional to great-circle distance (no climb/descent).

## Extending toward an interactive (browser) dashboard
`dashboard.py` precomputes per-frame state into the `FR` list — positions, sector
ratios, weather. Swap the matplotlib render loop for a Plotly/Folium/deck.gl frontend
and you get pan/zoom/hover + a time slider. The per-frame dicts are already the data model.
