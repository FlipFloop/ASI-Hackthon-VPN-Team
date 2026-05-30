# Sectors data: file format

The FAA splits the National Airspace into many discrete "Sectors". While flights transit through these sectors, the Air Traffic Controllers in charge of that sector must monitor and manage that flight.

Each sector also has an associated "capacity". A sector's capacity is the maximum number of flights that should be in that sector at any given time. If a sector is "over-demand", i.e there are too many flights in that sector at once, that sector can become unsafe and lead to further delays across the National Airspace.

For this challenge, you are getting one file: a GeoJSON FeatureCollection of synthetic ATC sectors covering the continental United States, partitioned into two altitude bands. The provided sector geometries are generally fake, but based off public data available [here](https://www.weather.gov/zse/). Each synthetic sector polygon describes a piece of airspace and carries an integer `capacity` value.

- **HIGH Altitude** sectors cover [**35,000 ft, 60,000 ft).** *(lower bound inclusive and upper bound exclusive)*
- **LOW Altitude** sectors cover [**0 ft, 35,000 ft)**. *(lower bound inclusive and upper bound exclusive)*

## Glossary

- **National Airspace (NAS)** - The airspace over the United States.
- **Sector** — A discrete unit of the NAS consisting of a boundary, an altitude range, and a capacity.
- **Capacity** — The maximum number of flights that should be in a sector at any given time.
- **Over-demand** — A state where a sector has more flights in it at once than its capacity allows, which can become unsafe and lead to further delays across the NAS.
- **ARTCC** — *Air Route Traffic Control Center*, the FAA facility that manages en-route airspace. Sectors in the real National Airspace are grouped under ARTCCs.
- **Facilities** — The ATC facilities (such as ARTCCs) that, in the real National Airspace, own and operate sectors.

## Filename

```
sectors.geojson.gz
```

A single gzipped GeoJSON file (decompresses to ~3.4 MB).

Each feature’s `properties` object includes **only** `name`, `altitude_from_ft`, `altitude_to_ft`, and `capacity` (no ARTCC or internal ids).

## What's inside the file

Standard GeoJSON `FeatureCollection`:

```python
import gzip, json
data = json.load(gzip.open("sectors.geojson.gz", "rt"))
data["type"]         # "FeatureCollection"
# data["features"] contains HIGH and LOW sectors
```

Each feature:

```json
{
  "type": "Feature",
  "geometry": { "type": "Polygon", "coordinates": [...] },
  "properties": {
    "name": "HIGH_042",
    "altitude_from_ft": 35000,
    "altitude_to_ft": 60000,
    "capacity": 18
  }
}
```


| Property           | Type   | Description                                                           |
| ------------------ | ------ | --------------------------------------------------------------------- |
| `name`             | string | Unique identifier, format `HIGH_<NNN>` or `LOW_<NNN>` (see Naming convention) |
| `altitude_from_ft` | int    | Floor of the sector's altitude band, in feet (inclusive)              |
| `altitude_to_ft`   | int    | Ceiling of the sector's altitude band, in feet (exclusive)            |
| `capacity`         | int    | Capacity value                                                        |


Geometry is always a single `Polygon` (`MultiPolygon` parts from the internal build are collapsed to the largest footprint by area). Coordinates use `[longitude, latitude]` in WGS84 degrees (GeoJSON order). No altitude is encoded in the coordinates — altitude lives only in the properties.

## Naming convention

```
<BAND>_<NNN>
```

- **BAND** — `HIGH` ([35,000–60,000) ft) or `LOW` ([0–35,000) ft).
- **NNN** — Zero-padded numeric id, **unique within that band** across the whole dataset (three digits with the current sector counts). The same index `NNN` appears in both `HIGH_NNN` and `LOW_NNN` for the two altitude layers over the same footprint.

Names here are simple `HIGH_NNN` / `LOW_NNN` identifiers and are not tied to any real-world facility code. The numbering is stable for a given pipeline output but is not meaningful beyond identifying a row in `sectors.geojson`.

## Geographic coverage

The polygon set forms a partition of CONUS airspace within each band: any point inside CONUS falls inside exactly one sector per band (modulo trivial edge cases on shared boundaries). There are no gaps and no overlaps between sectors in the same band.

## Reading example

```python
import gzip, json
from shapely.geometry import shape, Point

data = json.load(gzip.open("sectors.geojson.gz", "rt"))

# Find the sector covering Seattle (47.45 N, 122.30 W) at 38,000 ft (HIGH band).
seattle = Point(-122.30, 47.45)
hits = [
    f for f in data["features"]
    if shape(f["geometry"]).contains(seattle)
    and f["properties"]["altitude_from_ft"] <= 38000 < f["properties"]["altitude_to_ft"]
]
for f in hits:
    print(f["properties"]["name"], "capacity =", f["properties"]["capacity"])
```

## Plotting example

```python
import gzip, json
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPoly
from matplotlib.collections import PatchCollection
from shapely.geometry import shape

data = json.load(gzip.open("sectors.geojson.gz", "rt"))
high = [f for f in data["features"] if f["properties"]["name"].startswith("HIGH_")]

fig, ax = plt.subplots(figsize=(12, 7))
patches, caps = [], []
for f in high:
    geom = shape(f["geometry"])
    patches.append(MplPoly(list(geom.exterior.coords)))
    caps.append(f["properties"]["capacity"])

pc = PatchCollection(patches, cmap="viridis", alpha=0.7, edgecolor="white", linewidth=0.4)
pc.set_array(caps)
ax.add_collection(pc)
plt.colorbar(pc, label="Capacity")
ax.autoscale(); ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
ax.set_title("HIGH band sectors, colored by capacity")
plt.show()
```

