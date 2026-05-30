# Hold-Cost Engine — merit-order delay allocation

A localhost visualizer for the project angle: **flow management meters a congested
airport for *throughput* (delay minutes). Nobody allocates that same delay to
minimize live fuel $ + CO₂.** A held widebody burns ~3× a regional jet; a flight
still at the gate burns ~nothing. *Who* absorbs a forced delay and *where* (gate
vs air) is a pure cost decision the throughput optimizer never makes.

## Run

```bash
.venv/bin/python server.py        # -> http://localhost:8000
```

(Needs the existing `.venv` with numpy + shapely. Stdlib HTTP server, no Flask.)

CLI sanity check without the browser:

```bash
.venv/bin/python holdcost.py KATL      # stormy hub
.venv/bin/python holdcost.py KORD      # clear, pure-demand overload
```

## What it does

For one congested hub it builds the arrival demand, meters it against an
**Acceptance Rate** (storms near the field cut it — that's how the weather data
wires in), then allocates the forced delay three ways over the **same slots /
same total delay minutes**:

| policy | what it models |
|---|---|
| **All-air** (`status_quo`) | every delayed flight loiters in the air — why Ground Delay Programs exist |
| **Throughput** (`gate_fifo`) | FIFO slots; grounded→gate, airborne→air. ≈ good current practice / a throughput tool |
| **Cost-merit** (`cost_merit`) | reassign slots to minimize live $+CO₂: dump delay onto grounded (gate ≈ free) + lowest-burn jets; protect high-burn airborne flights |

**Headline = Throughput − Cost-merit** (savings from merit order alone). All-air
is the dramatic bound.

## Controls
- **Fuel price** — re-prices instantly client-side (cost is linear in $/kg). The
  ASI-differentiated lever: the recommendation *moves with the market*.
- **Acceptance rate** — runway throughput. Lower it to watch diversion-risk
  flags appear, then watch merit-order erase them.
- **Weather severity** — scales how hard storms (refc ≥ 40 dBZ near the field)
  cut the acceptance rate.

## Honest assumptions (defensible, not hidden)
- **No AAR in the bundle** → acceptance rate is an explicit, tunable parameter
  (standard for FAA GDP work). The metering math is real; the number is yours.
- **No aircraft type** → burn rate is a monotonic proxy off `cruise_speed_kt`,
  anchored to BADA-ish ranges (turboprop ~350 → widebody ~3300 kg/hr).
- **Constant-cruise model has no holding in it** → this is a *decision/accounting*
  tool ("given this congestion, here's the cost-optimal way to absorb it"), **not**
  a forecaster. No ground-truth claim is made.
- Fuel prices here are **fake/tunable**; swap in a live EIA feed for the real pitch.

## Files
- `holdcost.py` — the engine (`build_event`). Pure functions, JSON-serializable.
- `server.py` — stdlib server + `/api/event?hub=&aar=&fuel=&sev=`.
- `index.html` — Leaflet UI: map (flights colored by allocation, storm cells),
  cost bars, live sliders, sortable flight board.
