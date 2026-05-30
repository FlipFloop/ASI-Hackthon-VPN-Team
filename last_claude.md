There's no folder literally named /docs, but the repo's documentation directory is hackathon_data_bundle/documentation/, which holds three FILE_FORMAT.md files (sectors,
weather, routes). These are the data-format specs for the hackathon dataset your viz is
built on. Here's a full decode of each — every feature, field, and piece of jargon.

---

1. sectors/FILE_FORMAT.md — the airspace map

What it describes: A GeoJSON file of synthetic Air Traffic Control sectors covering the
continental US (CONUS). This is the "how crowded is the sky" layer — it's what your viz uses
for over-capacity sector counting.

Core concepts / wording

- NAS (National Airspace) — all US airspace.
- Sector — a chunk of airspace defined by a 2D boundary (polygon) + an altitude band + a
capacity. Controllers manage flights as they pass through.
- Capacity — max number of flights that should be in a sector at once. An integer per
polygon.
- Over-demand — when a sector holds more flights than its capacity. This is the "unsafe /
cascades into delays" state. (In your app.js this is the overBefore → overAfter count.)
- ARTCC — Air Route Traffic Control Center, the real FAA facility that owns en-route
sectors. Mentioned only as background — it is NOT in the data.
- Facilities — generic term for ATC centers like ARTCCs. Again, background only.

The two altitude bands (important feature)

The airspace is sliced into two vertical layers over the same map footprint:

- HIGH — [35,000 ft, 60,000 ft) — cruising jets
- LOW — [0 ft, 35,000 ft) — climb/descent, regional traffic

Brackets matter: [ = inclusive, ) = exclusive. So 35,000 ft belongs to HIGH, not LOW.

File shape

- Filename: sectors.geojson.gz (gzipped, ~3.4 MB decompressed).
- Standard GeoJSON FeatureCollection. Each feature = one sector polygon.
- properties carries only four fields: name, altitude_from_ft, altitude_to_ft, capacity. No
facility codes or internal IDs are exposed.
- Geometry is always a single Polygon (any MultiPolygon was collapsed to its largest piece).
Coordinates are [longitude, latitude] in WGS84 degrees — lon first, GeoJSON order. Altitude
is not in the coordinates; it lives in the properties.

Naming convention

<BAND>_<NNN> → e.g. HIGH_042, LOW_042.

- BAND = HIGH or LOW.
- NNN = zero-padded number, unique within its band. The same NNN appears in both bands for
the same ground footprint (so HIGH_042 and LOW_042 sit on top of each other). Names are
arbitrary identifiers — not tied to any real facility.

Geographic coverage

The polygons partition CONUS within each band: every point falls in exactly one sector per
band — no gaps, no overlaps (except trivial shared edges). The doc includes Python examples
for (a) finding which sector covers a lat/lon at a given altitude, and (b) plotting all HIGH
sectors colored by capacity.

---

1. wx/FILE_FORMAT.md — the weather layer

What it describes: Weather forecast grids over CONUS — the data behind your "Weather!"
commits. Two complementary products give a rough 3D picture of storms.

The two products (key feature)

- refc — composite reflectivity (top-down view): how intense precipitation is in a vertical
column, in dBZ. "Composite" = the max across all altitudes, so one number per location.
Think "heavy rain here, light rain there."
- retop — echo top (side view): the altitude of the top of the precipitation column, in
feet. "Echo" is the radar bounce-back; tall echo tops = tall storms. Think "the storm here
goes up to 30,000 ft."

When weather actually blocks a flight (the rule that matters for routing)

Weather only affects a flight if both:

1. the flight's altitude is below the local echo top (retop), AND
2. the local reflectivity (refc) is high enough — the doc's threshold is ≥ 40 dBZ ("< 40 dBZ
is fine").

Wording / glossary

- Forecast — a prediction; the data says what weather will be at a future moment.
- dBZ ("decibels of Z") — logarithmic radar reflectivity unit. Rule of thumb: <0 clear, ~20
light rain, ~40 heavy rain, 50–60+ severe / hail.
- UTC — all timestamps are UTC.
- HRRR — High-Resolution Rapid Refresh, the NOAA model these come from. Runs hourly,
forecasts ~18 hours out.

File shape & the timestamp triplet

Filename: {based_at}_{valid_from}_{valid_to}.npz, all UTC YYYY-MM-DD_HH:MM:SS.

- based_at — when the forecast was computed.
- valid_from / valid_to — the 15-minute window the data describes.

Each .npz holds one 2D array under key 'matrix', shape (256, 358), float64.

- Nodata masking: refc → m <= -50; retop → m < 0.

Grid orientation (easy to get wrong)

- Rows go north → south (row 0 = northernmost).
- Columns go west → east (col 0 = westernmost).
- Regular equirectangular lat/lon grid. Corners: lat 21.943°N → 55.7765°N, lon −135.0°E →
−67.5°E.
- The bounding box is wider than the actual data — cells outside the forecast footprint
carry the nodata sentinel (that's why a plotted map has slanted data edges; it's not a bug).
A pixel_top_left_latlon(i, j) helper is provided to convert grid indices → coordinates.

Directory layout (asked_at vs based_at — a subtle distinction)

- asked_at — directory name; the timestamp of each task in the challenge.
- based_at — filename; when the forecast was produced. Always based_at ≤ asked_at (usually
asked_at floored to the hour).
- Each asked_at_…/ folder holds all consecutive 15-min strips of one forecast, extending ~18
h forward. Sort by valid_from to walk time forward.

---

1. routes/FILE_FORMAT.md — the flights

What it describes: A single-moment snapshot of US flights and their planned waypoint paths —
this is the flight list your viz reroutes around no-fly zones and meters at the AAR.

Snapshot semantics (the framing)

Everything reflects only what was known at the snapshot moment, using the latest plan known
then. Flights already landed, cancelled, or with no filed route yet are excluded. All times
UTC ISO 8601; all coords decimal degrees WGS84.

File shape

- Gzipped JSON: asked_at_<…>Z/routes.json.gz — snapshot moment encoded in the parent dir
name.
- Top level: asked_at (the "as-of" time), window_start / window_end (half-open [start, end)
interval — every flight has a scheduled gate departure inside it), and flights[].

Per-flight fields

- flight_number — airline ID, e.g. UAL2367 = United 2367. Not unique alone — uniqueness is
the triple (flight_number, take_off_time, origin_airport_icao).
- take_off_time — departure from origin.
- scheduled_landing_time — scheduled touchdown.
- origin_airport_icao / destination_airport_icao — 4-letter ICAO codes (e.g. KDEN, KSFO).
All CONUS, so all start with K.
- cruise_altitude_ft — cruise altitude in feet.
- cruise_speed_kt — cruise speed in knots (1 kt ≈ 1.151 mph ≈ 1.852 km/h).
- lats / lons — parallel arrays of waypoints in flight order: first pair = origin, last =
destination, middle = nav fixes.
- is_airborne — true if already departed by asked_at, else still on the ground
(pre-departure). This is exactly the air vs gate distinction that drives the
air-hold/gate-hold cost split we discussed in your app.js.

Modelling assumptions (the simplification you rely on)

- scheduled_landing_time — scheduled touchdown.
- origin_airport_icao / destination_airport_icao — 4-letter ICAO codes (e.g. KDEN, KSFO). All CONUS, so all start with K.
- cruise_altitude_ft — cruise altitude in feet.
- cruise_speed_kt — cruise speed in knots (1 kt ≈ 1.151 mph ≈ 1.852 km/h).
- lats / lons — parallel arrays of waypoints in flight order: first pair = origin, last = destination, middle = nav fixes.
- is_airborne — true if already departed by asked_at, else still on the ground (pre-departure). This is exactly the air vs gate distinction that drives the air-hold/gate-hold cost split
we discussed in your app.js.

Modelling assumptions (the simplification you rely on)

Each aircraft is assumed to fly at constant cruise_altitude_ft and cruise_speed_kt the entire way — no climb, no descent, no en-route changes. take_off_time puts it at the origin
waypoint; scheduled_landing_time puts it at the destination. This is what makes straight-line ETA math (and your reroute delay calc) valid.

---
How the three tie together in your viz

- routes gives you the flights (and is_airborne → air vs gate holding).
- sectors gives you capacity → the overBefore → overAfter over-demand counts when you reroute.
- wx gives you the storm mask (refc ≥ 40 dBZ below retop) that, like a no-fly zone, forces reroutes.

So the air-hold/gate-hold and "zone saves $X" numbers you asked about earlier are the downstream cost of disrupting the routes as they pass through sectors and weather defined by these
three docs.

---
Note: there are also higher-level project docs outside this folder — hackathon_data_bundle/GUIDE.md, README_holdcost.md, README_toolkit.md, AlgorithmHandoff.md, HANDOFF.md, and
viz/README.md — which describe the algorithm and toolkit rather than the data format. Want me to walk through those too?
