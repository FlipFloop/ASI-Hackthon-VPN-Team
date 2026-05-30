# Routes — file format

This dataset is a snapshot of US flights and their planned paths, taken at
a single point in time. It contains every flight that was scheduled to
depart in a window around that moment, along with the sequence of
geographic waypoints (latitude / longitude) each flight was planned to
follow from origin airport to destination airport.

Everything in the file reflects only what was known at the snapshot
moment, and uses the latest plan known at that moment. Flights that had
already landed, that were cancelled, or for which no route had been
filed yet are not in the dataset.

All times are UTC ISO 8601. All coordinates are decimal degrees, WGS84
(the standard "GPS" coordinate system).

## File

Gzipped JSON. One file per snapshot, located at
`asked_at_<YYYY-MM-DD>T<HH:MM:SS>Z/routes.json.gz` — the snapshot moment
is encoded in the parent directory name.

## Top-level shape

```json
{
  "asked_at": "2026-04-01T14:00:00+00:00",
  "window_start": "2026-04-01T12:00:00+00:00",
  "window_end":   "2026-04-02T06:00:00+00:00",
  "flights": [ /* one entry per flight */ ]
}
```

- `asked_at` — the "as-of" timestamp this snapshot reflects.
- `window_start` / `window_end` — the half-open time interval used to
  select flights. Every flight in the dataset has a scheduled gate
  departure inside `[window_start, window_end)`.

## Per-flight record

```json
{
  "flight_number": "UAL2367",
  "take_off_time": "2026-04-01T11:48:00+00:00",
  "scheduled_landing_time": "2026-04-01T14:04:23+00:00",
  "origin_airport_icao": "KDEN",
  "destination_airport_icao": "KSFO",
  "cruise_altitude_ft": 38000,
  "cruise_speed_kt": 460,
  "lats": [39.8617, /* ... */, 37.6188],
  "lons": [-104.6732, /* ... */, -122.3754],
  "is_airborne": true
}
```

Field meanings:

- `flight_number` — the airline's identifier for the flight (e.g. `UAL2367`
  is United flight 2367). Not unique on its own — the same airline+number
  can appear more than once in a snapshot (e.g. multiple legs through the
  day, or different origins sharing a number). A flight is uniquely
  identified by `(flight_number, take_off_time, origin_airport_icao)`.
- `take_off_time` — when the flight departs the origin airport.
- `scheduled_landing_time` — when the flight is scheduled to touch down at
  the destination.
- `origin_airport_icao` / `destination_airport_icao` — 4-letter ICAO codes
  for the departure and arrival airports (e.g. `KDEN` for Denver, `KSFO`
  for San Francisco). All airports here are in the continental US, so
  every code starts with `K`.
- `cruise_altitude_ft` — cruise altitude in feet above sea level.
- `cruise_speed_kt` — cruise speed in knots (nautical miles per hour;
  1 knot ≈ 1.151 mph ≈ 1.852 km/h).
- `lats`, `lons` — parallel arrays of waypoints along the planned route,
  in flight order. The first pair is the origin airport, the last pair is
  the destination airport, and the points in between are the navigation
  fixes the flight is planned to fly over.
- `is_airborne` — `true` if the flight had already taken off by
  `asked_at`. Otherwise it's still on the ground waiting to depart
  (pre-departure).

## Modelling assumptions

To keep the dataset simple, you can assume that each aircraft flies at
`cruise_altitude_ft` and `cruise_speed_kt` from departure to destination:
there is no climb, no descent, and no en-route altitude or speed change.
`take_off_time` places the aircraft at the origin waypoint;
`scheduled_landing_time` places it at the destination waypoint.
