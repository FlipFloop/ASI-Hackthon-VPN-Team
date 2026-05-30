"""Hold-Cost Engine — cost-based merit-order allocation of arrival delay.

The gap: flow management meters a congested airport for *throughput* (minimize
total delay MINUTES) and treats a delay minute as fungible. In live fuel $ and
CO2 it is not -- a held widebody burns ~3x a regional jet, a flight at the gate
burns ~nothing. So *who* absorbs a forced delay and *where* (gate vs air) is a
cost decision the throughput optimizer never makes.

FAA grounding (real Traffic Management Initiative mechanics):
  - Metering = a Ground Delay Program (GDP): arrivals are issued landing SLOTS at
    the airport Acceptance Rate (AAR). Storms cut the AAR (refc >= 40 dBZ near
    the field) -- that's how the weather data drives cost here.
  - The FAA allocates slots by RATION-BY-SCHEDULE (RBS): earliest scheduled ETA
    gets earliest slot. RBS is deliberately *equity-based, not cost-based* -- it
    is fair across carriers but blind to who is expensive to hold. That blindness
    is exactly the white space.
  - Under Collaborative Decision Making (CDM), a carrier may legally reshuffle
    ITS OWN flights within ITS OWN RBS slots ("slot substitution / compression").
    Cost-optimizing that substitution with a LIVE fuel price is deployable today.

Four allocation tiers over the SAME GDP slots / SAME total delay minutes:
  all_air  -- every delayed flight loiters in the air (why GDPs exist; bound).
  rbs      -- FAA today: RBS slots, grounded->gate, airborne->air.
  intra_sub-- CDM intra-airline substitution, cost-optimized per carrier. The
              FAA-LEGAL, deployable product. <-- headline policy.
  sysopt   -- cross-airline cost-optimal reallocation. Theoretical ceiling; NOT
              FAA-legal today (breaks inter-carrier equity). Shown as the bound.

Headline (all carriers) = rbs - intra_sub. Per-carrier view = that carrier's
rbs - intra_sub. Prices use a live (stooq) or tunable fuel price.
"""
import os
import numpy as np
from atm import load_flights, Sectors, strip_times, latlon_to_rc

_FLIGHT_CACHE = {}
def _load_cached(scenario):
    if scenario not in _FLIGHT_CACHE:
        _FLIGHT_CACHE[scenario] = load_flights(scenario)
    return _FLIGHT_CACHE[scenario]

# ---- physical / economic constants (documented, tunable) ----
CO2_PER_KG_FUEL = 3.16          # kg CO2 per kg Jet-A burned (ICAO)
JETA_KG_PER_GAL = 3.04          # Jet-A density
GATE_BURN_KGHR  = 120.0         # APU burn while gate-held (vs ~0 air; honest)
AIR_RESERVE_MIN = 45.0          # typical holding fuel reserve -> diversion flag
DIVERSION_PENALTY_MIN = 90.0    # extra burn-equivalent minutes if reserve blown

# ICAO airline codes seen in the bundle -> display names
AIRLINES = {
    "SWA": "Southwest", "DAL": "Delta", "AAL": "American", "UAL": "United",
    "SKW": "SkyWest", "ENY": "Envoy", "RPA": "Republic", "ASA": "Alaska",
    "JIA": "PSA", "NKS": "Spirit", "EDV": "Endeavor", "JBU": "JetBlue",
    "PDT": "Piedmont", "FFT": "Frontier", "EJA": "NetJets", "AAY": "Allegiant",
    "LXJ": "Flexjet", "QXE": "Horizon", "ASH": "Mesa", "UPS": "UPS",
    "UCA": "CommutAir", "GJS": "GoJet", "MXY": "Breeze", "FDX": "FedEx",
}
def airline_of(fn):
    import re
    if re.match(r"^N\d", fn):
        return "GA"            # tail number -> general aviation / private
    m = re.match(r"^([A-Z]+)", fn)
    return m.group(1) if m else "OTH"
def airline_name(code):
    return AIRLINES.get(code, "General aviation" if code == "GA" else code)


def burn_kghr(spd_kt):
    """Cruise/hold fuel burn proxy from cruise speed (only size signal we have).
    BADA-ish: turboprop ~350, RJ ~1400, narrowbody ~2400, widebody ~3300 kg/hr."""
    return float(np.interp(spd_kt, [120, 200, 300, 400, 460, 510],
                                   [350, 650, 1400, 2300, 2700, 3300]))

def dollars_per_kg(fuel_usd_per_gal):
    return fuel_usd_per_gal / JETA_KG_PER_GAL


# -------------------------------------------------------------------------
def airport_pos(flights, icao):
    for f in flights:
        if f.dest == icao:
            return float(f.lats[-1]), float(f.lons[-1])
    return None


def weather_aar_profile(scenario, lat, lon, base_aar, times, severity_mult=1.0):
    """AAR(t): base_aar cut by storm intensity near the field. severity =
    fraction of nearby cells >= 40 dBZ; full storm cuts acceptance to 40%."""
    strips = strip_times(scenario, "refc")
    i, j, _ = latlon_to_rc(np.array([lat]), np.array([lon]))
    ii, jj = int(i[0]), int(j[0])
    prof = []
    for t in times:
        sev = 0.0
        for vf, vt, p in strips:
            if vf <= t < vt:
                m = np.clip(np.load(p)["matrix"], -30, 60)
                sub = m[max(0, ii-6):ii+7, max(0, jj-6):jj+7]
                if sub.size:
                    sev = float((sub >= 40).mean())
                break
        prof.append(base_aar * (1.0 - 0.6 * min(1.0, sev * severity_mult)))
    return np.array(prof)


def meter_slots(etas, aar_at):
    """GDP single-server metering at time-varying AAR. Returns slot_time/flight
    in Ration-by-Schedule order (earliest ETA -> earliest slot)."""
    order = np.argsort(etas)
    slots = np.empty(len(etas))
    free = -np.inf
    for k in order:
        t = max(etas[k], free)
        free = t + 3600.0 / max(aar_at(t), 1.0)
        slots[k] = t
    return slots


def _assign(idxs, slot_times, etas, burn, airborne):
    """Cost-optimal flight<->slot assignment over a fixed slot set. Airborne
    flights (must burn fuel to hold) get PRIORITY for the earliest feasible slots,
    in ETA order -- this guarantees no airborne flight is pushed past its RBS slot,
    so substitution never adds a diversion risk (safety is a hard constraint, not a
    cost trade). Grounded flights (gate ~ free) absorb the latest slots. The fuel
    $/CO2 savings come from moving delay to the gate + pricing each hold by burn.
    Returns {idx: delay_min}."""
    slots = sorted(slot_times)
    used = [False] * len(slots)
    air = sorted([k for k in idxs if airborne[k]], key=lambda k: etas[k])
    gnd = sorted([k for k in idxs if not airborne[k]], key=lambda k: -etas[k])

    def grab(eta, rev):
        rng = range(len(slots)-1, -1, -1) if rev else range(len(slots))
        for s in rng:
            if not used[s] and slots[s] >= eta:
                used[s] = True; return slots[s]
        for s in rng:                       # fallback: any free slot
            if not used[s]:
                used[s] = True; return slots[s]
        return eta

    res = {}
    for k in air:
        res[k] = max(0.0, (grab(etas[k], False) - etas[k]) / 60.0)
    for k in gnd:
        res[k] = max(0.0, (grab(etas[k], True) - etas[k]) / 60.0)
    return res


def _fuel_kg(delay_min, airborne, burn, force_air=False):
    """Fuel burned absorbing `delay_min`, incl. diversion tail if an air hold
    exceeds reserve. Grounded -> gate (APU) unless force_air (all-air policy)."""
    if airborne or force_air:
        kg = delay_min / 60.0 * burn
        if delay_min > AIR_RESERVE_MIN:
            kg += DIVERSION_PENALTY_MIN / 60.0 * burn
        return kg
    return delay_min / 60.0 * GATE_BURN_KGHR


# -------------------------------------------------------------------------
def build_event(scenario, hub, base_aar=55.0, fuel_usd_per_gal=2.50,
                weather_severity=1.0, window_hours=6.0):
    r, flights = _load_cached(scenario)
    asked = _ts(r["asked_at"])
    lat, lon = airport_pos(flights, hub)

    horizon = asked + window_hours * 3600
    inbound = [f for f in flights if f.dest == hub and asked < f.t1 <= horizon]
    inbound.sort(key=lambda f: f.t1)
    n = len(inbound)
    etas = np.array([f.t1 for f in inbound])
    burn = np.array([burn_kghr(f.spd) for f in inbound])
    airborne = np.array([f.airborne for f in inbound])
    codes = [airline_of(f.fn) for f in inbound]

    # GDP metering -> RBS slots
    grid_t = np.arange(asked, horizon + 300, 300)
    aar_prof = weather_aar_profile(scenario, lat, lon, base_aar, grid_t,
                                   severity_mult=weather_severity)
    def aar_at(t):
        return aar_prof[int(np.clip((t - asked) / 300, 0, len(aar_prof) - 1))]
    slot_time = meter_slots(etas, aar_at)
    rbs_delay = np.maximum(0.0, (slot_time - etas) / 60.0)

    all_idx = list(range(n))
    # intra-airline substitution: reassign within each carrier's own RBS slots
    sub_delay = np.zeros(n)
    for code in set(codes):
        g = [k for k in all_idx if codes[k] == code]
        d = _assign(g, [slot_time[k] for k in g], etas, burn, airborne)
        for k, v in d.items():
            sub_delay[k] = v
    # system-optimal: reassign across ALL carriers (theoretical ceiling)
    sys_delay = np.zeros(n)
    for k, v in _assign(all_idx, list(slot_time), etas, burn, airborne).items():
        sys_delay[k] = v

    # per-flight fuel (kg) under each tier -> $ and CO2 derived client-side
    def fuel_vec(delays, force_air=False):
        return np.array([_fuel_kg(delays[k], airborne[k], burn[k], force_air)
                         for k in all_idx])
    fk = {
        "all_air":   fuel_vec(rbs_delay, force_air=True),
        "rbs":       fuel_vec(rbs_delay),
        "intra_sub": fuel_vec(sub_delay),
        "sysopt":    fuel_vec(sys_delay),
    }

    dkg = dollars_per_kg(fuel_usd_per_gal)
    def totals(v, delays, force_air=False):
        air_min = np.array([delays[k] if (airborne[k] or force_air) else 0.0
                            for k in all_idx])
        gate_min = np.array([0.0 if (airborne[k] or force_air) else delays[k]
                             for k in all_idx])
        return {
            "usd": round(float(v.sum()) * dkg, 0),
            "co2_kg": round(float(v.sum()) * CO2_PER_KG_FUEL, 0),
            "fuel_kg": round(float(v.sum()), 0),
            "air_hold_min": round(float(air_min.sum()), 0),
            "gate_hold_min": round(float(gate_min.sum()), 0),
            "diversion_flags": int((air_min > AIR_RESERVE_MIN).sum()),
        }
    policies = {
        "all_air":   totals(fk["all_air"], rbs_delay, force_air=True),
        "rbs":       totals(fk["rbs"], rbs_delay),
        "intra_sub": totals(fk["intra_sub"], sub_delay),
        "sysopt":    totals(fk["sysopt"], sys_delay),
    }

    # per-airline aggregates (for the dropdown + per-carrier headline)
    by_airline = {}
    for code in sorted(set(codes), key=lambda c: -codes.count(c)):
        g = [k for k in all_idx if codes[k] == code]
        by_airline[code] = {
            "name": airline_name(code), "n": len(g),
            "n_airborne": int(sum(airborne[k] for k in g)),
            "rbs_fuel_kg":   round(float(sum(fk["rbs"][k] for k in g)), 0),
            "sub_fuel_kg":   round(float(sum(fk["intra_sub"][k] for k in g)), 0),
            "allair_fuel_kg":round(float(sum(fk["all_air"][k] for k in g)), 0),
        }

    rows = []
    for k, f in enumerate(inbound):
        p = f.position(asked)
        rows.append({
            "fn": f.fn, "airline": codes[k], "airline_name": airline_name(codes[k]),
            "orig": f.orig, "spd": int(f.spd), "airborne": bool(airborne[k]),
            "burn_kghr": round(float(burn[k]), 0),
            "eta_min": round((f.t1 - asked) / 60.0, 1),
            "lat": None if p is None else round(p[0], 3),
            "lon": None if p is None else round(p[1], 3),
            "rbs_delay_min": round(float(rbs_delay[k]), 1),
            "sub_delay_min": round(float(sub_delay[k]), 1),
            "sub_where": ("air" if airborne[k] and sub_delay[k] > 0.1 else
                          ("gate" if sub_delay[k] > 0.1 else "on-time")),
            "sub_diversion": bool(airborne[k] and sub_delay[k] > AIR_RESERVE_MIN),
            "rbs_diversion": bool(airborne[k] and rbs_delay[k] > AIR_RESERVE_MIN),
            # per-policy fuel so the client can re-price any airline subset live
            "fuel_kg": {p_: round(float(fk[p_][k]), 1) for p_ in fk},
        })

    storm = _storm_cells(scenario, lat, lon, asked + window_hours * 1800)
    return {
        "hub": hub, "scenario": scenario, "asked_at": r["asked_at"],
        "lat": lat, "lon": lon, "base_aar": base_aar,
        "fuel_usd_per_gal": fuel_usd_per_gal, "weather_severity": weather_severity,
        "n_inbound": n, "n_airborne": int(airborne.sum()),
        "n_delayed": int((rbs_delay > 0.5).sum()),
        "total_delay_min": round(float(rbs_delay.sum()), 0),
        "min_aar": round(float(aar_prof.min()), 1),
        "max_aar": round(float(aar_prof.max()), 1),
        "policies": policies,
        "by_airline": by_airline,
        "savings_usd": round(policies["rbs"]["usd"] - policies["intra_sub"]["usd"], 0),
        "savings_co2_t": round((policies["rbs"]["co2_kg"] -
                                policies["intra_sub"]["co2_kg"]) / 1000.0, 2),
        "ceiling_usd": round(policies["rbs"]["usd"] - policies["sysopt"]["usd"], 0),
        "flights": rows, "storm_cells": storm,
        "aar_profile": [round(float(a), 1) for a in aar_prof],
    }


def _storm_cells(scenario, lat, lon, t, box=3.0):
    strips = strip_times(scenario, "refc")
    chosen = next((p for vf, vt, p in strips if vf <= t < vt), None)
    if chosen is None and strips:
        chosen = strips[len(strips)//2][2]
    if chosen is None:
        return []
    from atm import LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, ROWS, COLS
    m = np.clip(np.load(chosen)["matrix"], -30, 60)
    cells = []
    for ri in range(ROWS):
        clat = LAT_MAX - (ri + 0.5) / ROWS * (LAT_MAX - LAT_MIN)
        if abs(clat - lat) > box:
            continue
        for cj in range(COLS):
            clon = LON_MIN + (cj + 0.5) / COLS * (LON_MAX - LON_MIN)
            if abs(clon - lon) > box:
                continue
            if m[ri, cj] >= 30:
                cells.append([round(clat, 3), round(clon, 3), int(m[ri, cj])])
    return cells


def _ts(s):
    from atm import parse_ts
    return parse_ts(s).timestamp()


if __name__ == "__main__":
    import sys
    scen = "asked_at_2025-07-01T21:30:00Z"
    hub = sys.argv[1] if len(sys.argv) > 1 else "KATL"
    ev = build_event(scen, hub)
    print(f"Hub {ev['hub']}  inbound={ev['n_inbound']} (airborne {ev['n_airborne']})"
          f"  delayed={ev['n_delayed']}  total_delay={ev['total_delay_min']:.0f} min"
          f"  AAR {ev['min_aar']}-{ev['max_aar']}/hr")
    for name in ("all_air", "rbs", "intra_sub", "sysopt"):
        p = ev["policies"][name]
        print(f"  {name:10s} ${p['usd']:>9,.0f}  {p['co2_kg']/1000:6.1f} t  "
              f"air {p['air_hold_min']:>6.0f}m gate {p['gate_hold_min']:>6.0f}m  div {p['diversion_flags']}")
    print(f"SAVINGS (FAA-legal CDM substitution vs RBS): ${ev['savings_usd']:,.0f} "
          f"+ {ev['savings_co2_t']:.2f} t CO2   | sysopt ceiling ${ev['ceiling_usd']:,.0f}")
    print("top carriers:", [(c, v["n"]) for c, v in list(ev["by_airline"].items())[:6]])
