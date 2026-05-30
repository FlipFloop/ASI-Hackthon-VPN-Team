# Handoff — ASI Hackathon (ATM data bundle)

_Last updated: 2026-05-30. Purpose: recap of work so far + current focus, so you can recenter._

---

## 0. TL;DR — where we landed

We pivoted the "Gate or Wait" idea from a per-flight engine to a **system-level
merit-order allocator** and **BUILT IT** as a working localhost visualizer (the
**Hold-Cost Engine**). Thesis: *flow management meters a congested airport for
throughput (delay MINUTES); nobody allocates that same delay to minimize live
fuel $ + CO₂.* Held widebody ≈ 3× a regional jet; gate ≈ free. **Who/where absorbs
the forced delay is a cost decision the throughput optimizer never makes.**

→ See **§6** for the built tool and how to run it. Run: `.venv/bin/python server.py`
→ http://localhost:8000. (Earlier per-flight framing kept below in §4 for context.)

---

## 1. The data bundle (what's in it)

US air-traffic snapshot: flights, the airspace sectors they fly through, and weather.
All UTC, continental US. **Files ship decompressed** (`sectors.geojson`, `routes.json`)
despite docs saying `.gz`. **11 self-contained scenarios** `asked_at_<ts>Z/`, May 2025 → Apr 2026.

- **Flights** (`<scenario>/routes.json`, ~14 MB): ~14–18k flights/snapshot, ~2.8–3.6k airborne.
  Each: flight_number, takeoff/landing times, origin/dest ICAO, cruise alt/speed, parallel
  `lats[]`/`lons[]` waypoints, `is_airborne`. **Constant-cruise model** (no climb/descent;
  time along route ∝ great-circle distance). Unique key = (flight_number, take_off_time, origin).
- **Sectors** (`sectors.geojson`, shared, 712 features): 356 HIGH [35k–60k ft) + 356 LOW
  [0–35k ft), partition CONUS per band. Each has integer `capacity` (20–60). Coords `[lon,lat]`.
- **Weather** (`<scenario>/wx/{refc,retop}/`, 73 strips each): `refc` = composite reflectivity
  (dBZ, storm intensity), `retop` = echo top (ft, storm height). Each `.npz` → `matrix` (256×358)
  on equirect grid (lat 21.943→55.7765°N row0=N; lon −135→−67.5°E col0=W). 15-min strips, ~18h fwd.
  **Impact rule:** `refc ≥ 40 dBZ` AND `flight alt ≤ retop`.

### Data-quality caveats (found, important)
- `refc` has a handful of **anomalous pixels >60 dBZ** per stormy scenario (up to ~480). Clip.
- Weather is **bimodal by season**: summer days ~4–6% flights impacted; winter/clear days (Jan-13,
  Apr-8) ~0%. Use summer scenarios for weather work.
- **Congestion is structural, not weather-driven**: 23–36 sectors over capacity even on clear days.
- No airport acceptance rates (only sector capacity). No aircraft type (only cruise speed/alt).

---

## 2. Toolkit we built (working, in this dir)

Setup: `python3 -m venv .venv && .venv/bin/pip install shapely matplotlib numpy`

| file | what it does |
|---|---|
| `atm.py` | Core primitives: `Flight.position(t)` (interpolate along waypoints), `Sectors.find(lat,lon,alt)` (STRtree PIP + altitude band), weather grid sampling (`strip_times`, `latlon_to_rc`). |
| `analyze.py` | Full congestion + weather-conflict report for one scenario (~2.5s). |
| `survey.py` | Cross-scenario comparison table (all 11). |
| `make_map.py` | Static map of one congested instant → `scenario_map.png`. |
| `dashboard.py` | **Time-animated GIF** over the 18h horizon → `dashboard.gif`. Args: `<scenario> <strip_step> <LOW|HIGH>`. |
| `README_toolkit.md` | Setup, dashboard legend, conventions. |

### Key measured results (cross-scenario)
- Weather impact: 0% (Jan-13, Apr-8) → 6.2% (Jul-01). Summer ~5%.
- Over-capacity sectors: 16–36 of 712 per scenario; peak load/capacity up to **2.05×**;
  worst single overload **+21 flights** over cap. Mostly LOW-band, clustered around hubs.

---

## 3. Project ideas screened against ASI's real products

**ASI = Airspace Intelligence** (`airspace-intelligence.com`). One platform (**Prescience**),
two solution skins: **Flyways AI** (air traffic management) and **PRESCIENCE** (airline ops).
Platform pillars: Data Fusion, Domain Modeling (4D twin), Prediction, Optimization,
Communications, Replay. NOTE: **no Apify integration in this environment** — used WebFetch/WebSearch.

ASI white-space pattern: everything they sell is **enterprise, operational, decision-recommending**
for an airline/ATC/defense buyer. Gaps = public-facing/explanatory, novel scientific framing,
post-hoc/accountability, or externality layers (equity, climate, passenger experience).

### Earlier 3 ideas (verdicts)
| Idea | ASI overlap | Verdict |
|---|---|---|
| 🦠 Delay Contagion Map (epidemiology/R₀ framing) | 🟡 Low-Med (they model cascades under the hood, no fragility framing) | ✅ Build — framing novel |
| 🌊 Airspace Stress Topology (fluid-pressure viz) | 🟡 Low-Med (they predict imbalances, present as numbers/TMIs) | ✅ Build — physics viz is white space |
| 🔄 Controller's Counterfactual (what-if + replay) | 🔴 HIGH — this *is* Flyways/PRESCIENCE | ⚠️ Pivot to public/explanatory |

### 5 additional white-space ideas (generated, not chosen)
NAS Fragility Index · Weather Toll Ledger (equity) · Carbon Cost of Storms · Sector Fragility Genome.
(See conversation for details — set aside in favor of the focus below.)

---

## 4. CURRENT FOCUS — "Gate or Wait" (Hold Cost Engine)

**Concept:** For each not-yet-departed flight at decision time `asked_at`, predict expected
**airborne holding** at its destination around its ETA; if a queue/weather block is likely,
recommend **holding at the gate** instead of launching into a hold. Output a live **$ + CO₂**
recommendation. Rooted in the oldest ATFM principle (FAA Ground Delay Programs): absorb delay
on the ground, not in the air.

**Why the bundle fits well:** `asked_at` snapshot + 18h forecast = a real pre-departure decision
horizon. `is_airborne` splits committed (flying) vs. grounded candidates (~11–14k/snapshot).
Each grounded flight has a destination + scheduled ETA inside the forecast window → predict the
conditions it will actually meet. Destination LOW-band sector over-capacity at ETA + weather near
destination = holding proxy.

**Economics (anchored):**
- Jet A ≈ **$4.19/gal** (EIA Gulf Coast, May 2026) ≈ $1.32/kg.
- Holding narrowbody burn ~2,000–2,800 kg/hr ≈ **~$50/min in fuel** circling; gate ≈ $0 fuel.
- Airborne delay costs > ground delay per minute (Eurocontrol/Westminster reference values).
- **Diversion tail risk** (holding exhausts reserves) is the strongest safety argument.
- Decision = expected-cost / real-option under forecast uncertainty; **live fuel price moves the
  hold/release threshold** — the memorable, ASI-differentiated lever.

**ASI overlap:** mechanism (ground vs air delay) is textbook + inside Flyways' TMI optimizer →
**don't win on mechanism.** Differentiate on: live commodity-price coupling ($ not time);
per-flight explanatory output; CO₂ co-benefit; audience pivot (single-airline dispatcher or public).

**Honest gaps / risks:**
- No airport AAR in bundle (proxy via sector over-demand). No aircraft type (parameterize burn).
- Constant-cruise model → no holding is *in* the data; this is inherently **predictive/what-if**.
- One snapshot per scenario → **no ground-truth** on held/not-held; validate the congestion
  sub-model instead.

**External data to wire in:** EIA jet-fuel spot price (API) · Eurocontrol/Westminster cost-of-delay
coefficients · BADA/ICAO holding+diversion burn rates · (ideally) FAA AAR.

**MVP demo:** stormy hub scenario → ranked board of inbound grounded flights with predicted hold
min, live-priced circle-vs-gate cost, HOLD/RELEASE call, aggregate "$X + Y t CO₂ saved today,"
plus a **fuel-price slider** that re-prices the board live.

**Open questions to decide before coding:**
1. Airline-dispatcher tool vs. public explainer (changes whole UX)?
2. Single-airline scope vs. whole-NAS?
3. How hard to lean on diversion tail risk (most compelling, hardest to quantify)?

---

## 5. Suggested next steps
- [ ] Decide the 3 open questions above.
- [ ] Pressure-test economics on one sample flight (real per-min ground vs air numbers).
- [ ] Scope buildable-from-bundle vs. needs-external-feed.
- [ ] Then: prototype holding-likelihood model on a summer scenario using `atm.py`.

## 6. BUILT — Hold-Cost Engine (merit-order allocator + web visualizer)

**The gap (option 2, chosen):** ASI/Flyways optimize congested airspace for *time/
throughput* — a delay minute is treated as fungible. In live $ + CO₂ it isn't.
We allocate the SAME forced delay by **merit order** (congestion-pricing / grid
dispatch logic) to minimize cost. Differentiators ASI structurally lacks:
(1) live fuel-price coupling — the answer **moves with the market**; (2) CO₂
externality; (3) diversion-tail safety. Don't claim to beat their *mechanism*
(ground-vs-air is textbook); win on the **pricing layer** on top.

**Model.** Pick a hub → arrival demand (ETAs) metered against an **Acceptance Rate**;
**storms near the field (refc≥40) cut the AAR** = how the weather data wires in.
Metering issues landing slots → forced delay. Three policies over the *same slots*:
`status_quo` (all-air loiter) · `gate_fifo` (FIFO, grounded→gate / ≈ throughput tool)
· `cost_merit` (reassign slots: dump delay on grounded gate-holds + low-burn jets,
protect high-burn airborne). **Headline = gate_fifo − cost_merit.**

**Measured (scenario Jul-01, KATL hub, AAR 55, $2.50/gal fake):** ~213 inbound,
~180 metered. Merit vs throughput ≈ **$12k + 48 t CO₂** saved; vs all-air ≈ **$46k**.
KORD (clear, pure-demand overload): **$50k+** and at low AAR cuts diversion-risk
flights ~**32→1**. Numbers scale with the fuel-price & AAR sliders.

**Files (this dir):** `holdcost.py` (engine, `build_event`, ~0.4s) · `server.py`
(stdlib HTTP, `/api/event?hub=&aar=&fuel=&sev=`) · `index.html` (Leaflet map +
cost bars + live sliders + sortable board) · `README_holdcost.md` (full writeup).
Hubs wired: KATL (storms) default, KORD/KDFW (clear), KDEN/KCLT/KLAX/KIAH.

**Honest assumptions (stated, not hidden):** AAR is a tunable parameter (bundle
ships none — standard for GDP work); burn = monotonic proxy off `cruise_speed_kt`
(no aircraft type); constant-cruise model has no holding → this is a **decision/
accounting tool, NOT a forecaster** (no ground-truth claim); fuel prices are
fake/tunable (swap in live EIA feed for the real pitch).

**Obvious next steps if continued:** wire real EIA jet-fuel API · per-airline
filter (single-dispatcher scope) · time-scrub the AAR/weather across the 18h
window · validate the congestion sub-model (the one part that *is* checkable).

## 7. BUILT (2026-05-30) — FAA realism + live fuel feed + airline filter ✅

All three shipped and verified end-to-end on localhost:8000.

**FAA tiers (real TMI mechanics).** Metering = a Ground Delay Program; slots issued
by **Ration-by-Schedule (RBS)** — FAA's equity rule, cost-blind (the white-space foil).
Four tiers over the same slots: `all_air` (loiter bound) · `rbs` (FAA today) ·
`intra_sub` (**CDM intra-airline substitution — legal & deployable, the headline**) ·
`sysopt` (cross-airline ceiling, not FAA-legal). Headline = rbs − intra_sub.
Safety is a hard constraint: airborne flights get ETA-priority for early slots so
substitution never *adds* a diversion (KDEN diversion-risk flights 27→4).

**Live fuel feed** — `fuelfeed.py`, keyless stooq ULSD (NY-Harbor heating oil, the jet
hedge proxy) → Jet-A $/gal; crude fallback; offline default. Confirmed live ~$3.67/gal.
Server `/api/fuel`; UI shows a pulsing LIVE ticker and seeds the fuel slider.

**Airline filter** — flight_number prefix → ICAO carrier (24 named + GA). Dropdown
scopes map/board/headline to one carrier; per-flight `fuel_kg` lets fuel slider AND
airline filter re-price 100% client-side. Story: at KATL, **DAL captures $9.5k of the
$10.5k** total (substitution leverage scales with slots held at the hub).

**Verified results (KATL, AAR 55, live $3.67/gal):** rbs→intra_sub = **$10.5k + 27.5 t
CO₂**, ceiling $14.7k, all-air bound $59k. KORD $31k. KDEN $112k + 27→4 diversions.

**Files added/changed:** `fuelfeed.py` (new) · `holdcost.py` (4 tiers, airlines,
substitution, per-flight fuel) · `server.py` (+/api/fuel) · `index.html` (4 bars,
airline dropdown, live ticker). Bug fixed: AAR slider was off-grid (step 2, value 55)
and silently snapped → now min 20/step 1.

### Original design notes (kept for reference)

**(a) FAA mechanics to model** (using standard FAA TMI practice; user mentioned
"how the FAA handles delays/reroutes" — no doc attached, confirm if they have one):
- Baseline = **Ration-by-Schedule (RBS)**: FAA's real slot rule, by original ETA,
  deliberately *equity-based not cost-based* (this is the honest foil — explains why
  cost-merit is white space AND why full cross-airline cost-opt isn't FAA-legal).
- **Intra-airline substitution (CDM)**: airlines may legally reshuffle their OWN
  flights within their OWN RBS slots → THE deployable, FAA-legal merit-order. Makes
  the single-airline filter the hero, not just a view.
- Reroutes/CDR/playbook = OUT of scope (routes fixed in bundle) — note honestly in UI.
- New policy tiers: `status_quo` (all-air bound) · `rbs` (FAA today, rename of
  gate_fifo) · `intra_sub` (legal, per-airline cost-opt — HERO) · `sysopt` (cross-
  airline ceiling, rename of cost_merit, label "not FAA-legal today").
- Implement `_intra_airline_assign`: per-airline, reassign its flights among its OWN
  rbs slots via earliest(high-burn airborne)/latest(grounded) merit logic.

**(b) Live fuel feed** — `fuelfeed.py`. CONFIRMED WORKING no-key source:
`https://stooq.com/q/l/?s=ho.f&f=sd2t2ohlcvn&h&e=csv` → ULSD NY Harbor (heating oil,
the standard jet-fuel hedge proxy; got $3.49/gal). Also `cl.f` = WTI crude. Plan:
fetch front-month, convert to Jet-A $/gal (HO ≈ jet + small spread), cache w/ ttl,
fall back to static default offline. UI: "LIVE FUEL" ticker (price+source+time);
slider becomes live-default with manual override.

**(c) Airline filter** — flight_number prefix = ICAO airline code. Top in data:
SWA 2356 (Southwest), DAL 1573 (Delta), AAL 1556 (American), UAL 1299 (United),
SKW 1245 (SkyWest), N… 1060 (GA/tail), ENY 467 (Envoy), RPA 393 (Republic),
ASA 346 (Alaska), JIA 325 (PSA), NKS 319 (Spirit), EDV 295 (Endeavor),
JBU 269 (JetBlue), FFT 195 (Frontier). 210 distinct prefixes. Filter restricts
map/board/headline to one carrier; headline = that carrier's rbs→intra_sub savings.

## 8. BUILT — Reroute autorouter (handoff module for the larger project) ✅

A* grid rerouting around no-fly/hazard polygons, cost-aware (distance→fuel +
traffic congestion + safety buffer + turn smoothness), with baseline-isolated
cost (A* with zone − A* without) so "extra" = exactly what the zone cost.
String-pulled to clean polylines. Returns waypoints + fuel/$/CO₂ delta +
ready-to-render GeoJSON ([lon,lat], role-tagged original/reroute/no_fly).

**Verified:** DAL506 KMIA→KSEA, 6° box → reroute provably clears zone,
+60 nm / +$427 / +1119 kg CO₂ @ live $3.67/gal, ~0.7s w/ 14k concurrent flights.
Scales sensibly with zone size. Rendered green-around-red box in a Leaflet viewer.

**Files:** `autoroute.py` (algorithm, stdlib-only, `reroute()` one entry point) ·
`reroute_viewer.html` (reference Leaflet renderer, served at `/reroute`) ·
`server.py` (+`GET /api/reroute` demo endpoint) · **`AlgorithmHandoff.md`**
(the colleague-facing integration doc: I/O contract, JS snippets for Leaflet+Mapbox,
tunable weights, extension hooks for weather/sector-load/reroute-vs-delay, and a
paste-ready Claude prompt).

This is the keystone that connects the cost engine + weather + no-fly modules:
weather/no-fly feed it obstacles, the Hold-Cost engine prices its delay option,
its output re-drives congestion. Extension hooks documented in AlgorithmHandoff §7.

## Reference links
- ASI ATM (Flyways): https://www.airspace-intelligence.com/solutions/air-traffic-management
- ASI Airline Ops (PRESCIENCE): https://www.airspace-intelligence.com/solutions/airline-operations
- EIA jet fuel spot: https://www.eia.gov/dnav/pet/hist/eer_epjk_pf4_rgc_dpgD.htm
- Eurocontrol/Westminster cost of delay: https://www.eurocontrol.int/publication/european-airline-delay-cost-reference-values
