/* global maplibregl, deck */
const {
  MapboxOverlay,
  ScatterplotLayer,
  BitmapLayer,
  PathLayer,
  GeoJsonLayer,
  IconLayer,
  TripsLayer,
  TextLayer,
} = deck;

// small white chevron/arrow icon (points "up" = north at angle 0), tinted via mask
function makeArrowIcon() {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const x = c.getContext("2d");
  x.fillStyle = "#fff";
  x.beginPath();
  x.moveTo(32, 6);
  x.lineTo(54, 58);
  x.lineTo(32, 44);
  x.lineTo(10, 58);
  x.closePath();
  x.fill();
  return c.toDataURL();
}
const ARROW_ICON = makeArrowIcon();

// initial great-circle bearing in degrees clockwise from north
function bearingDeg(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const y = Math.sin((lon2 - lon1) * rad) * Math.cos(lat2 * rad);
  const x =
    Math.cos(lat1 * rad) * Math.sin(lat2 * rad) -
    Math.sin(lat1 * rad) * Math.cos(lat2 * rad) * Math.cos((lon2 - lon1) * rad);
  return (Math.atan2(y, x) * 180) / Math.PI;
}

const DATA = "data";
const state = {
  index: null,
  grid: null,
  snap: null, // current snapshot meta
  flights: [], // prepared flight objects
  t: 0, // current time (epoch s)
  playing: false,
  speed: 900, // sim seconds per real second
  lastFrame: null,
  hovered: null,
  sectorsByBand: null, // { HIGH: [features], LOW: [features] } loaded once
  demand: null, // current snapshot's sector demand timeseries (baseline)
  gdpDemand: null, // post-GDP sector demand timeseries
  gdp: null, // GDP summary (delays + before/after metrics)
  opts: {
    flights: true,
    refc: true,
    retop: false,
    conflictsOnly: false,
    trails: false, // route of hovered flight
    motionTrails: false, // fading TripsLayer trails behind every plane
    arrows: false, // draw planes as heading arrows instead of dots
    sectorBand: "off",
    scenario: "baseline", // "baseline" | "gdp"
    airports: "off", // "off" | "hubs" | "active" — markers from route endpoints
    wxOpacity: 0.7,
    horizon: 0, // forecast preview, minutes ahead (0 | 30 | 60 | 90)
    stormCap: true, // weather cuts sector capacity (storm-reduced capacity)
    wxAffected: false, // highlight every flight whose route crosses a storm
  },
  stormcap: null, // { HIGH:{name:[red/step]}, LOW:{...} } — capacity reduction
  wxImpact: null, // per-snapshot weather impact tally (affected flights, minutes)
  trips: null, // static TripsLayer data for current snapshot
  dock: "sectors", // active tab: "conflicts" | "sectors" (dock is always present)
  dockCollapsed: false,
  dockBand: "LOW", // which band the sectors panel lists
  dockSort: "demand", // "demand" | "load" | "cap" | "name"
  selected: null, // clicked flight — drives the spotlight/fade
  focused: null, // flight being located
  focusedSector: null, // sector feature being located
  conflictPts: [], // current in-weather flights (for the dock list)
  frame: 0, // render counter (for throttling dock list rebuilds)
  nfz: null, // active no-fly zone {w,s,e,n}
  nfzPreview: null, // box being dragged
  nfzStats: null, // { rerouted, grounded, addedDelayMin, overBefore, overAfter, + fuel cost }
  nfzDemand: null, // sector demand recomputed from rerouted routes
  fuelUsdGal: 3.67, // jet-fuel price used to price reroute detours (live default)
  aar: 55, // airport acceptance rate (arrivals/hr) for arrival-hold metering
  baselineDemandJS: null, // baseline demand via the same JS method (for honest before/after)
  drawing: false, // no-fly-zone draw mode active
  sectorIndex: null, // { HIGH, LOW } spatial grid index for point-in-sector
  capByName: null, // sector name -> capacity
};

// ---------- map ----------
const map = new maplibregl.Map({
  container: "map",
  style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  center: [-98, 39],
  zoom: 3.6,
});
const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
map.addControl(overlay);

// ---------- helpers ----------
function fmtClock(epoch) {
  const d = new Date(epoch * 1000);
  return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

// great-circle cumulative distance (km) for interpolation weighting
function buildCumulative(la, lo) {
  const R = 6371,
    rad = Math.PI / 180;
  const cum = new Float64Array(la.length);
  for (let i = 1; i < la.length; i++) {
    const dlat = (la[i] - la[i - 1]) * rad;
    const dlon = (lo[i] - lo[i - 1]) * rad;
    const a =
      Math.sin(dlat / 2) ** 2 +
      Math.cos(la[i - 1] * rad) *
        Math.cos(la[i] * rad) *
        Math.sin(dlon / 2) ** 2;
    cum[i] = cum[i - 1] + 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }
  return cum;
}

function positionAt(f, frac) {
  const cum = f.cum,
    total = cum[cum.length - 1];
  if (total <= 0) return [f.lo[0], f.la[0]];
  const target = frac * total;
  // linear scan is fine (routes have ~30 points); binary search for safety
  let k = 1;
  while (k < cum.length && cum[k] < target) k++;
  if (k >= cum.length) return [f.lo[f.lo.length - 1], f.la[f.la.length - 1]];
  const span = cum[k] - cum[k - 1];
  const t = span <= 0 ? 0 : (target - cum[k - 1]) / span;
  return [
    f.lo[k - 1] + t * (f.lo[k] - f.lo[k - 1]),
    f.la[k - 1] + t * (f.la[k] - f.la[k - 1]),
  ];
}

// ---------- no-fly-zone geometry + rerouting ----------
const R_KM = 6371;
function haversineKm(lon1, lat1, lon2, lat2) {
  const rad = Math.PI / 180;
  const dlat = (lat2 - lat1) * rad,
    dlon = (lon2 - lon1) * rad;
  const a =
    Math.sin(dlat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dlon / 2) ** 2;
  return 2 * R_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}
// box = {w, s, e, n}  (lon/lat bounds)
function pointInBox(lon, lat, b) {
  return lon > b.w && lon < b.e && lat > b.s && lat < b.n;
}
// does the open segment pass through the box interior? (Liang-Barsky clip)
function segHitsBox(x1, y1, x2, y2, b) {
  const dx = x2 - x1,
    dy = y2 - y1;
  const p = [-dx, dx, -dy, dy];
  const q = [x1 - b.w, b.e - x1, y1 - b.s, b.n - y1];
  let t0 = 0,
    t1 = 1;
  for (let i = 0; i < 4; i++) {
    if (Math.abs(p[i]) < 1e-12) {
      if (q[i] < 0) return false; // parallel and outside this slab
    } else {
      const r = q[i] / p[i];
      if (p[i] < 0) {
        if (r > t1) return false;
        if (r > t0) t0 = r;
      } else {
        if (r < t0) return false;
        if (r < t1) t1 = r;
      }
    }
  }
  if (t1 <= t0) return false;
  const tm = (t0 + t1) / 2,
    mx = x1 + tm * dx,
    my = y1 + tm * dy,
    m = 1e-6;
  return mx > b.w + m && mx < b.e - m && my > b.s + m && my < b.n - m;
}
// shortest path A->B avoiding the box, via inflated corners (visibility + Dijkstra)
function shortestAround(A, B, b) {
  const m = 0.12; // degrees of clearance for routing corners
  const nodes = [
    A,
    B,
    [b.w - m, b.n + m],
    [b.e + m, b.n + m],
    [b.e + m, b.s - m],
    [b.w - m, b.s - m],
  ];
  const N = nodes.length;
  const adj = Array.from({ length: N }, () => []);
  for (let i = 0; i < N; i++)
    for (let j = i + 1; j < N; j++) {
      if (!segHitsBox(nodes[i][0], nodes[i][1], nodes[j][0], nodes[j][1], b)) {
        const d = haversineKm(
          nodes[i][0],
          nodes[i][1],
          nodes[j][0],
          nodes[j][1],
        );
        adj[i].push([j, d]);
        adj[j].push([i, d]);
      }
    }
  const dist = Array(N).fill(Infinity),
    prev = Array(N).fill(-1),
    done = Array(N).fill(false);
  dist[0] = 0;
  for (let it = 0; it < N; it++) {
    let u = -1;
    for (let i = 0; i < N; i++)
      if (!done[i] && (u < 0 || dist[i] < dist[u])) u = i;
    if (u < 0 || dist[u] === Infinity) break;
    done[u] = true;
    for (const [v, w] of adj[u])
      if (dist[u] + w < dist[v]) {
        dist[v] = dist[u] + w;
        prev[v] = u;
      }
  }
  if (dist[1] === Infinity) return null;
  const path = [];
  for (let v = 1; v >= 0; v = prev[v]) {
    path.unshift(nodes[v]);
    if (v === 0) break;
  }
  return path;
}
// reroute a flight's waypoints around the box; returns {la, lo} or null if unaffected
function rerouteAround(la, lo, b) {
  const n = la.length;
  // quick bbox reject
  let mnx = Infinity,
    mxx = -Infinity,
    mny = Infinity,
    mxy = -Infinity;
  for (let i = 0; i < n; i++) {
    if (lo[i] < mnx) mnx = lo[i];
    if (lo[i] > mxx) mxx = lo[i];
    if (la[i] < mny) mny = la[i];
    if (la[i] > mxy) mxy = la[i];
  }
  if (mxx < b.w || mnx > b.e || mxy < b.s || mny > b.n) return null;
  // find the span of the route that interacts with the box
  let first = -1,
    last = -1;
  for (let i = 0; i < n; i++) {
    const inside = pointInBox(lo[i], la[i], b);
    const cross =
      i < n - 1 && segHitsBox(lo[i], la[i], lo[i + 1], la[i + 1], b);
    if (inside || cross) {
      if (first < 0) first = i;
      last = cross ? i + 1 : i;
    }
  }
  if (first < 0) return null;
  let aIdx = first;
  while (aIdx > 0 && pointInBox(lo[aIdx], la[aIdx], b)) aIdx--;
  let bIdx = last;
  while (bIdx < n - 1 && pointInBox(lo[bIdx], la[bIdx], b)) bIdx++;
  const A = [lo[aIdx], la[aIdx]],
    B = [lo[bIdx], la[bIdx]];
  const detour = shortestAround(A, B, b);
  if (!detour) return null;
  const outLo = [],
    outLa = [];
  for (let i = 0; i <= aIdx; i++) {
    outLo.push(lo[i]);
    outLa.push(la[i]);
  }
  for (let j = 1; j < detour.length - 1; j++) {
    outLo.push(detour[j][0]);
    outLa.push(detour[j][1]);
  }
  for (let i = bIdx; i < n; i++) {
    outLo.push(lo[i]);
    outLa.push(la[i]);
  }
  return { la: outLa, lo: outLo };
}

// ---------- point-in-sector index + live demand recompute ----------
const SECTOR_GRID = 1.0; // degrees per spatial-index cell
function buildSectorIndex(feats) {
  const cells = new Map();
  feats.forEach((f, idx) => {
    const ring = f.geometry.coordinates[0];
    let mnx = Infinity,
      mxx = -Infinity,
      mny = Infinity,
      mxy = -Infinity;
    for (const [x, y] of ring) {
      if (x < mnx) mnx = x;
      if (x > mxx) mxx = x;
      if (y < mny) mny = y;
      if (y > mxy) mxy = y;
    }
    f._bbox = [mnx, mny, mxx, mxy];
    f._ring = ring;
    for (
      let ix = Math.floor(mnx / SECTOR_GRID);
      ix <= Math.floor(mxx / SECTOR_GRID);
      ix++
    )
      for (
        let iy = Math.floor(mny / SECTOR_GRID);
        iy <= Math.floor(mxy / SECTOR_GRID);
        iy++
      ) {
        const key = ix + "," + iy;
        (cells.get(key) || cells.set(key, []).get(key)).push(idx);
      }
  });
  return { cells, feats };
}
function pointInRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0],
      yi = ring[i][1],
      xj = ring[j][0],
      yj = ring[j][1];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi)
      inside = !inside;
  }
  return inside;
}
function findSector(lon, lat, band) {
  const idx = state.sectorIndex[band];
  const cand = idx.cells.get(
    Math.floor(lon / SECTOR_GRID) + "," + Math.floor(lat / SECTOR_GRID),
  );
  if (!cand) return null;
  for (const si of cand) {
    const f = idx.feats[si],
      b = f._bbox;
    if (lon < b[0] || lon > b[2] || lat < b[1] || lat > b[3]) continue;
    if (pointInRing(lon, lat, f._ring)) return f.properties.name;
  }
  return null;
}
// recompute the sector demand timeseries from each flight's CURRENT route/timing
function computeDemandFromRoutes() {
  const base = state.demand;
  const gs = base.grid_start,
    iv = base.interval,
    ns = base.n_steps;
  const out = { HIGH: {}, LOW: {} };
  for (const f of state.flights) {
    if (f.blocked) continue; // grounded — contributes nothing
    const band = f.alt >= 35000 ? "HIGH" : "LOW";
    const dur = f.t1 - f.t0;
    if (dur <= 0) continue;
    const k0 = Math.max(0, Math.ceil((f.t0 - gs) / iv));
    const k1 = Math.min(ns - 1, Math.floor((f.t1 - gs) / iv));
    for (let k = k0; k <= k1; k++) {
      const frac = (gs + k * iv - f.t0) / dur;
      const [lon, lat] = positionAt(f, frac);
      const name = findSector(lon, lat, band);
      if (!name) continue;
      const rows = out[band][name] || (out[band][name] = new Array(ns).fill(0));
      rows[k]++;
    }
  }
  return {
    grid_start: gs,
    interval: iv,
    n_steps: ns,
    HIGH: out.HIGH,
    LOW: out.LOW,
  };
}
// how many sectors ever exceed (storm-adjusted) capacity across the window
function peakOverCount(demand) {
  let n = 0;
  for (const band of ["HIGH", "LOW"])
    for (const name in demand[band]) {
      const row = demand[band][name];
      let over = false;
      for (let step = 0; step < row.length; step++)
        if (row[step] > effCap(band, name, step)) {
          over = true;
          break;
        }
      if (over) n++;
    }
  return n;
}

// forecast horizon: preview weather + sector demand this many minutes ahead of
// the real clock (flights stay at "now"). 0 = live. Mirrors App's Now/+30/+60/+90.
function dispTime() {
  return state.t + (state.opts.horizon || 0) * 60;
}

// weather strip index valid at time t
function stripAt(t) {
  const s = state.snap.strips;
  for (let k = 0; k < s.length; k++) {
    if (t >= s[k].from && t < s[k].to) return k;
  }
  if (t < s[0].from) return 0;
  return s.length - 1;
}
function currentStrip() {
  return stripAt(state.t);
}

function inConflict(f, k) {
  for (const [a, b] of f.cf) if (k >= a && k <= b) return true;
  return false;
}

// demand/storm step index for the forecast-shifted display time
function currentStep() {
  const d = state.demand;
  const k = Math.floor((dispTime() - d.grid_start) / d.interval);
  return Math.max(0, Math.min(d.n_steps - 1, k));
}

// demand source: no-fly-zone reroute demand wins, then GDP, then baseline
function demandSource() {
  if (state.nfz && state.nfzDemand) return state.nfzDemand;
  if (state.opts.scenario === "gdp" && state.gdpDemand) return state.gdpDemand;
  return state.demand;
}

function sectorDemand(band, name, step) {
  const row = demandSource()[band][name];
  return row ? row[step] : 0;
}

// storm capacity-reduction factor (0..1) for a sector at a step (0 if none / off)
function stormRed(band, name, step) {
  if (!state.opts.stormCap || !state.stormcap) return 0;
  const row = state.stormcap[band] && state.stormcap[band][name];
  return row ? row[step] || 0 : 0;
}
// effective capacity = static capacity cut by any storm overhead at that step
function effCap(band, name, step) {
  const cap = state.capByName[name] || 0;
  const red = stormRed(band, name, step);
  return red > 0 ? Math.max(0, Math.round(cap * (1 - red))) : cap;
}

// green -> yellow -> orange -> red as demand approaches/exceeds capacity
function sectorFill(count, cap) {
  if (count === 0) return [90, 100, 120, 18];
  const r = count / cap;
  let c;
  if (r < 0.5) c = [70, 211, 154];
  else if (r < 0.8) c = [240, 210, 70];
  else if (r < 1.0) c = [240, 140, 40];
  else c = [255, 50, 50];
  const alpha = r >= 1 ? 175 : 70 + Math.min(r, 1) * 70;
  return [c[0], c[1], c[2], alpha];
}

function updateMeta() {
  const meta = state.snap;
  let html =
    `as-of <b>${fmtClock(meta.asked_at)}</b><br>` +
    `window ${fmtClock(meta.window_start)}<br>→ ${fmtClock(meta.window_end)}`;
  // overall weather effects for this snapshot
  const wi = state.wxImpact;
  if (wi) {
    const pct = wi.total ? Math.round((wi.affected / wi.total) * 100) : 0;
    const stormSecs = state.stormcap
      ? Object.keys(state.stormcap.HIGH).length +
        Object.keys(state.stormcap.LOW).length
      : 0;
    const hrs = (wi.wxMin / 60).toLocaleString("en-US", {
      maximumFractionDigits: 0,
    });
    html +=
      `<div class="wx-box"><b>Weather impact</b><br>` +
      `<b>${wi.affected.toLocaleString()}</b> / ${wi.total.toLocaleString()} flights ` +
      `(${pct}%) route through storms<br>` +
      `~${hrs} flight-hours in ≥40 dBZ` +
      (stormSecs
        ? `<br><b>${stormSecs}</b> sectors lose capacity to storms`
        : "") +
      `</div>`;
  }
  if (state.opts.scenario === "gdp" && state.gdp) {
    const g = state.gdp;
    html +=
      `<div class="gdp-box"><b>GDP</b> (ground delays)<br>` +
      `${g.n_held} flights held · ${g.total_delay_min} min total` +
      ` (max ${g.max_delay_min})<br>` +
      `over-demand sectors <b>${g.over_demand_sectors_before} → ${g.over_demand_sectors_after}</b><br>` +
      `over-demand min ${g.over_demand_minutes_before} → ${g.over_demand_minutes_after}` +
      `<br><span class="ghost-note">faint dots = baseline (no-GDP) positions</span></div>`;
  }
  if (state.nfzStats) {
    const s = state.nfzStats;
    const n = (x) => x.toLocaleString("en-US");
    html +=
      `<div class="nfz-box"><b>No-fly zone</b><br>` +
      `${s.rerouted} rerouted · ${s.grounded} grounded<br>` +
      `+${s.addedDelayMin} min total reroute delay · +${n(s.extraNm)} nm<br>` +
      `<span class="nfz-cost">+$${n(s.extraFuelUsd)} fuel · ` +
      `+${n(s.extraFuelKg)} kg · +${n(s.extraCo2Kg)} kg CO₂</span><br>` +
      `<span class="ghost-note">@ $${s.fuelUsdGal.toFixed(2)}/gal</span><br>` +
      `over-capacity sectors <b>${s.overBefore} → ${s.overAfter}</b><br>` +
      `airports: ${s.airportsClosed} closed · ${s.airportsAffected} affected` +
      `<div class="aar-line">arrivals metered @ <b>${s.aar}/hr</b> · ` +
      `air-hold ${n(s.airHoldMin)}m / gate ${n(s.gateHoldMin)}m<br>` +
      (s.aarHoldUsdExtra !== 0
        ? `zone ${s.aarHoldUsdExtra > 0 ? "adds" : "saves"} ` +
          `<span class="nfz-cost">$${n(Math.abs(s.aarHoldUsdExtra))}</span> arrival-hold fuel<br>`
        : "") +
      `CDM substitution saves <span class="cdm-save">$${n(s.cdmSaveUsd)}` +
      ` · ${n(s.cdmSaveCo2Kg)} kg CO₂</span><br>` +
      `<span class="ghost-note" title="planes circling so long they risk a fuel diversion">` +
      `diversion risk ${s.divRbs} → ${s.divSub} flights</span></div></div>`;
  }
  document.getElementById("meta").innerHTML = html;
}

// ---------- data loading ----------
async function loadIndex() {
  const idx = await (await fetch(`${DATA}/snapshots.json`)).json();
  state.index = idx.snapshots;
  state.grid = idx.grid;
  const sel = document.getElementById("snapshot");
  sel.innerHTML = state.index
    .map(
      (s, i) =>
        `<option value="${i}">${s.id.replace("asked_at_", "").replace("Z", "")} — ${s.n_flights} flt</option>`,
    )
    .join("");
  sel.onchange = () => loadSnapshot(+sel.value);

  // sector geometries load once and are reused across snapshots
  const sectors = await (await fetch(`${DATA}/sectors.json`)).json();
  state.sectorsByBand = { HIGH: [], LOW: [] };
  for (const f of sectors.features)
    state.sectorsByBand[f.properties.band].push(f);
  // spatial index + capacity lookup for live point-in-sector demand recompute
  state.sectorIndex = {
    HIGH: buildSectorIndex(state.sectorsByBand.HIGH),
    LOW: buildSectorIndex(state.sectorsByBand.LOW),
  };
  state.capByName = {};
  for (const f of sectors.features)
    state.capByName[f.properties.name] = f.properties.capacity;

  const p = new URLSearchParams(location.search);
  const snapIdx = Math.max(
    0,
    Math.min(state.index.length - 1, +(p.get("snap") ?? 0) || 0),
  );
  sel.value = snapIdx;
  await loadSnapshot(snapIdx);
  applyUrlParams(p);
}

// Optional deep-link params: ?snap=<i>&t=<0..1>&sectors=HIGH|LOW&arrows=1&trails=1
// Drives the real UI controls (dispatches change) so URL and menu share one path.
function applyUrlParams(p) {
  const sec = p.get("sectors");
  if (sec === "HIGH" || sec === "LOW") {
    const el = document.getElementById("sector-band");
    el.value = sec;
    el.dispatchEvent(new Event("change"));
  }
  const flag = (name, el) => {
    if (p.get(name) === "1") {
      const c = document.getElementById(el);
      c.checked = true;
      c.dispatchEvent(new Event("change"));
    }
  };
  flag("arrows", "ly-arrows");
  flag("trails", "ly-motion-trails");
  for (const [name, el] of [
    ["flights", "ly-flights"],
    ["refc", "ly-refc"],
    ["retop", "ly-retop"],
    ["conf", "ly-conflicts-only"],
  ]) {
    const v = p.get(name);
    if (v !== null) {
      const c = document.getElementById(el);
      c.checked = v === "1";
      c.dispatchEvent(new Event("change"));
    }
  }
  const setSelect = (name, el, allowed) => {
    const v = p.get(name);
    if (v === null || !allowed.includes(v)) return;
    const c = document.getElementById(el);
    c.value = v;
    c.dispatchEvent(new Event("change"));
  };
  setSelect("scen", "scenario", ["baseline", "gdp"]);
  setSelect("airports", "airport-mode", ["off", "hubs", "active"]);
  const view = p.get("view");
  if (view) {
    const [lng, lat, z] = view.split(",").map(Number);
    if (isFinite(lng) && isFinite(lat))
      map.jumpTo({ center: [lng, lat], zoom: isFinite(z) ? z : map.getZoom() });
  }
  const tf = p.get("t");
  if (tf !== null) {
    const frac = Math.max(0, Math.min(1, +tf || 0));
    state.t =
      state.snap.window_start +
      frac * (state.snap.window_end - state.snap.window_start);
  }
  const nfz = p.get("nfz");
  if (nfz) {
    const [w, s, e, n] = nfz.split(",").map(Number);
    if ([w, s, e, n].every(isFinite)) applyNFZ({ w, s, e, n });
  }
  render();
  const dock = p.get("dock");
  if (dock === "conflicts" || dock === "sectors") openDock(dock);
  if (p.get("play") === "1") play();
}

async function loadSnapshot(i) {
  stop();
  const meta = state.index[i];
  state.snap = meta;
  const data = await (await fetch(`${DATA}/${meta.id}/flights.json`)).json();
  state.flights = data.flights.map((f) => ({
    ...f,
    cum: buildCumulative(f.la, f.lo),
  }));
  // overall weather impact for this snapshot (static — cf is route-wide).
  // affected = route crosses impassable weather; wxMin ≈ flight-minutes in storms
  // (each conflict strip is a 15-min window).
  let wxAffectedN = 0,
    wxMin = 0;
  for (const f of state.flights) {
    if (f.cf && f.cf.length) {
      wxAffectedN++;
      for (const [a, b] of f.cf) wxMin += (b - a + 1) * 15;
    }
  }
  state.wxImpact = {
    total: state.flights.length,
    affected: wxAffectedN,
    wxMin,
  };
  // airport locations derived from route endpoints (origin = first waypoint,
  // destination = last waypoint), keyed by ICAO, with traffic counts
  const ap = new Map();
  const addAirport = (icao, lon, lat, isDep) => {
    let a = ap.get(icao);
    if (!a) {
      a = { icao, lon, lat, dep: 0, arr: 0 };
      ap.set(icao, a);
    }
    if (isDep) a.dep++;
    else a.arr++;
  };
  for (const f of state.flights) {
    addAirport(f.o, f.lo[0], f.la[0], true);
    addAirport(f.d, f.lo[f.lo.length - 1], f.la[f.la.length - 1], false);
  }
  state.airports = [...ap.values()].map((a) => ({
    ...a,
    n: a.dep + a.arr,
    cancelled: 0,
    closed: false,
  }));
  state.airportByIcao = new Map(state.airports.map((a) => [a.icao, a]));
  state.nfz = null;
  state.nfzStats = null;
  state.nfzDemand = null;
  state.baselineDemandJS = null; // routes differ per snapshot
  state.focusedSector = null;
  rebuildTrips();
  state.demand = await (
    await fetch(`${DATA}/${meta.id}/sectors_demand.json`)
  ).json();
  // storm-reduced capacity (optional — only if preprocess_sectors.py wrote it)
  state.stormcap = await fetch(`${DATA}/${meta.id}/sectors_stormcap.json`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  // GDP scenario data (optional — only present if gdp.py has been run)
  state.gdpDemand = await fetch(`${DATA}/${meta.id}/sectors_demand_gdp.json`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  state.gdp = await fetch(`${DATA}/${meta.id}/gdp.json`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  // attach each flight's assigned ground delay (seconds), keyed by fn|origin|t0
  for (const f of state.flights) f.gdpDelay = 0;
  if (state.gdp && state.gdp.delays) {
    const byKey = new Map();
    for (const f of state.flights) byKey.set(`${f.fn}|${f.o}|${f.t0}`, f);
    for (const d of state.gdp.delays) {
      const f = byKey.get(`${d.fn}|${d.o}|${d.t0}`);
      if (f) f.gdpDelay = d.delay_min * 60;
    }
  }
  state.t = meta.window_start;
  updateMeta();

  const slider = document.getElementById("time");
  slider.min = meta.window_start;
  slider.max = meta.window_end;
  slider.step = 60;
  slider.value = meta.window_start;
  render();
}

// rebuild static TripsLayer data from each flight's CURRENT route (relative
// timestamps keep TripsLayer's float32 precise)
function rebuildTrips() {
  const ws = state.snap.window_start;
  state.trips = state.flights.map((f) => {
    const total = f.cum[f.cum.length - 1] || 1;
    const dur = f.t1 - f.t0;
    return {
      path: f.la.map((la, i) => [f.lo[i], la]),
      timestamps: Array.from(f.cum, (c) => f.t0 - ws + (c / total) * dur),
      band: f.alt >= 35000 ? "HIGH" : "LOW",
    };
  });
}

// ---------- fuel-cost model (ported from holdcost.burn_kghr + autoroute._package) ----------
// Prices a reroute detour in fuel / dollars / CO2, using the SAME constants and
// burn curve the Python toolkit uses, so the viz and the algorithm agree.
const CO2_PER_KG_FUEL = 3.16; // kg CO2 per kg Jet-A burned (ICAO)
const JETA_KG_PER_GAL = 3.04; // Jet-A density
// piecewise-linear cruise/hold burn proxy from cruise speed (kt -> kg/hr).
// turboprop ~350, RJ ~1400, narrowbody ~2400, widebody ~3300.
function burnKgHr(spdKt) {
  const xs = [120, 200, 300, 400, 460, 510];
  const ys = [350, 650, 1400, 2300, 2700, 3300];
  if (spdKt <= xs[0]) return ys[0];
  if (spdKt >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 1; i < xs.length; i++) {
    if (spdKt <= xs[i]) {
      const t = (spdKt - xs[i - 1]) / (xs[i] - xs[i - 1]);
      return ys[i - 1] + t * (ys[i] - ys[i - 1]);
    }
  }
  return ys[ys.length - 1];
}
// extra nautical miles of a detour -> {kg, usd, co2} at the current fuel price.
// Mirrors autoroute._package: extra_kg = extra_nm/spd*burn; usd = kg/density*price.
function priceDetour(extraNm, spdKt) {
  const burn = burnKgHr(spdKt);
  const kg = (extraNm / Math.max(spdKt, 1)) * burn; // extra_min/60 * burn
  const usd = (kg / JETA_KG_PER_GAL) * state.fuelUsdGal;
  const co2 = kg * CO2_PER_KG_FUEL;
  return { kg, usd, co2 };
}

// ---------- AAR arrival metering + CDM cheapest-absorber (ported from holdcost.py) ----------
// Airport Acceptance Rate (AAR) meters arrivals: each landing takes 3600/AAR sec,
// so when reroutes delay/bunch arrivals a queue forms and planes must HOLD. WHERE
// a plane holds is a fuel decision priced at the LIVE fuel price:
//   already airborne -> air-hold (circle the airport), burns cruise burn_kghr ($$$)
//   not yet departed  -> gate-hold (depart later), burns ~APU only (cheap)
// CDM substitution reshuffles a carrier's OWN flights across its OWN slots so the
// cheap / gate-holdable ones absorb the wait and the expensive jets land on time.
const GATE_BURN_KGHR = 120.0; // APU burn while gate-held (vs ~0 air; honest)
const AIR_RESERVE_MIN = 45.0; // holding-fuel reserve -> diversion risk past this
const DIVERSION_PENALTY_MIN = 90.0; // extra burn-equiv minutes if reserve blown

function airlineOf(fn) {
  if (/^N\d/.test(fn || "")) return "GA"; // tail number -> general aviation
  const m = /^([A-Z]+)/.exec(fn || "");
  return m ? m[1] : "OTH";
}
// single-server GDP metering at constant AAR -> RBS slot time per flight (epoch s)
function meterSlots(etas, aar) {
  const order = etas.map((_, i) => i).sort((a, b) => etas[a] - etas[b]);
  const slots = new Array(etas.length);
  const step = 3600 / Math.max(aar, 1);
  let free = -Infinity;
  for (const k of order) {
    const t = Math.max(etas[k], free);
    free = t + step;
    slots[k] = t;
  }
  return slots;
}
// fuel (kg) to absorb `delayMin`: air-hold burns cruise burn (+diversion tail past
// reserve); gate-hold burns only APU. This is where the live fuel price bites.
function holdFuelKg(delayMin, airborne, burn) {
  if (airborne) {
    let kg = (delayMin / 60) * burn;
    if (delayMin > AIR_RESERVE_MIN) kg += (DIVERSION_PENALTY_MIN / 60) * burn;
    return kg;
  }
  return (delayMin / 60) * GATE_BURN_KGHR;
}
// CDM cheapest-absorber over one carrier's own slot set: airborne flights (must
// burn to hold) grab earliest feasible slots in ETA order; gate-holdable flights
// absorb the latest. Returns {localIdx: delayMin}. Mirrors holdcost._assign.
function assignCheapest(idxs, slotSet, etas, airborne) {
  const slots = [...slotSet].sort((a, b) => a - b);
  const used = new Array(slots.length).fill(false);
  const air = idxs.filter((k) => airborne[k]).sort((a, b) => etas[a] - etas[b]);
  const gnd = idxs.filter((k) => !airborne[k]).sort((a, b) => etas[b] - etas[a]);
  const grab = (eta, rev) => {
    const a = rev ? slots.length - 1 : 0;
    const b = rev ? -1 : slots.length;
    const d = rev ? -1 : 1;
    for (let s = a; s !== b; s += d)
      if (!used[s] && slots[s] >= eta) return (used[s] = true), slots[s];
    for (let s = a; s !== b; s += d)
      if (!used[s]) return (used[s] = true), slots[s];
    return eta;
  };
  const res = {};
  for (const k of air) res[k] = Math.max(0, (grab(etas[k], false) - etas[k]) / 60);
  for (const k of gnd) res[k] = Math.max(0, (grab(etas[k], true) - etas[k]) / 60);
  return res;
}
// Meter every destination airport at the AAR and price holds under RBS (FAA today)
// and CDM substitution (cost-optimized). etaOf(f) picks which landing time to use.
// Returns fleet totals. Mirrors holdcost.build_event's policy loop, system-wide.
function meterArrivals(flights, etaOf, aar) {
  const byAirport = new Map();
  flights.forEach((f, i) => {
    const a = byAirport.get(f.d) || byAirport.set(f.d, []).get(f.d);
    a.push(i);
  });
  const dkg = state.fuelUsdGal / JETA_KG_PER_GAL;
  let rbsKg = 0, subKg = 0, airMin = 0, gateMin = 0, divRbs = 0, divSub = 0;
  for (const idxs of byAirport.values()) {
    const etas = idxs.map((i) => etaOf(flights[i]));
    const air = idxs.map((i) => flights[i].air);
    const burn = idxs.map((i) => burnKgHr(flights[i].spd));
    const slot = meterSlots(etas, aar);
    const rbsDelay = etas.map((e, j) => Math.max(0, (slot[j] - e) / 60));
    // CDM substitution within each carrier (reassign its own slots)
    const subDelay = new Array(idxs.length).fill(0);
    const carriers = new Map();
    idxs.forEach((gi, j) => {
      const c = airlineOf(flights[gi].fn);
      (carriers.get(c) || carriers.set(c, []).get(c)).push(j);
    });
    for (const g of carriers.values()) {
      const d = assignCheapest(g, g.map((j) => slot[j]), etas, air);
      for (const j of g) subDelay[j] = d[j];
    }
    for (let j = 0; j < idxs.length; j++) {
      rbsKg += holdFuelKg(rbsDelay[j], air[j], burn[j]);
      subKg += holdFuelKg(subDelay[j], air[j], burn[j]);
      if (air[j]) {
        airMin += rbsDelay[j];
        if (rbsDelay[j] > AIR_RESERVE_MIN) divRbs++;
        if (subDelay[j] > AIR_RESERVE_MIN) divSub++;
      } else gateMin += rbsDelay[j];
    }
  }
  return {
    rbsUsd: rbsKg * dkg, subUsd: subKg * dkg,
    rbsCo2: rbsKg * CO2_PER_KG_FUEL, subCo2: subKg * CO2_PER_KG_FUEL,
    airMin, gateMin, divRbs, divSub,
  };
}

// ---------- no-fly zone: reroute every affected flight around the box ----------
function restoreFlight(f) {
  if (f._orig) {
    f.la = f._orig.la;
    f.lo = f._orig.lo;
    f.cum = f._orig.cum;
    f.t1 = f._orig.t1;
    delete f._orig;
  }
  f.blocked = false;
}
function applyNFZ(box) {
  // 1. reset every flight to its original route + clear airport impact
  for (const f of state.flights) restoreFlight(f);
  for (const a of state.airports) {
    a.cancelled = 0;
    a.closed = false;
  }
  // 2. cache the baseline demand computed the SAME (JS) way, so before/after
  //    isolates the reroute effect rather than Python-vs-JS method differences
  if (!state.baselineDemandJS)
    state.baselineDemandJS = computeDemandFromRoutes();
  if (!box) {
    state.nfz = null;
    state.nfzDemand = null;
    state.nfzStats = null;
    rebuildTrips();
    updateMeta();
    render();
    return;
  }
  for (const a of state.airports)
    if (pointInBox(a.lon, a.lat, box)) a.closed = true; // airport inside the zone
  // 3. reroute / ground every affected flight
  let rerouted = 0,
    grounded = 0,
    addedDelay = 0,
    extraNmTot = 0, // total detour distance (nm) across the fleet
    extraKgTot = 0, // extra fuel burned (kg)
    extraUsdTot = 0, // extra fuel cost ($)
    extraCo2Tot = 0; // extra CO2 (kg)
  const worst = []; // per-flight detour rows, for the chatbot's "hardest hit" list
  const impacted = new Set(); // destination airports the zone disrupts
  for (const f of state.flights) {
    const n = f.la.length;
    if (
      pointInBox(f.lo[0], f.la[0], box) ||
      pointInBox(f.lo[n - 1], f.la[n - 1], box)
    ) {
      f.blocked = true; // origin/destination inside the zone — can't reroute
      grounded++;
      impacted.add(f.d);
      const ao = state.airportByIcao.get(f.o),
        ad = state.airportByIcao.get(f.d);
      if (ao) ao.cancelled++;
      if (ad) ad.cancelled++;
      continue;
    }
    const rr = rerouteAround(f.la, f.lo, box);
    if (!rr) continue;
    impacted.add(f.d); // this flight now lands later -> its airport's queue shifts
    const origNm = f.cum[f.cum.length - 1] / 1.852; // filed route (nm)
    const cum = buildCumulative(rr.la, rr.lo);
    const nm = cum[cum.length - 1] / 1.852; // km -> nautical miles
    const newT1 = f.t0 + (nm / f.spd) * 3600; // constant speed -> later landing
    addedDelay += Math.max(0, newT1 - f.t1);
    // price the detour with the toolkit's fuel-cost model
    const extraNm = Math.max(0, nm - origNm);
    const c = priceDetour(extraNm, f.spd);
    extraNmTot += extraNm;
    extraKgTot += c.kg;
    extraUsdTot += c.usd;
    extraCo2Tot += c.co2;
    worst.push({
      fn: f.fn,
      o: f.o,
      d: f.d,
      extra_nm: Math.round(extraNm),
      extra_usd: Math.round(c.usd),
    });
    f._orig = { la: f.la, lo: f.lo, cum: f.cum, t1: f.t1 };
    f.la = rr.la;
    f.lo = rr.lo;
    f.cum = cum;
    f.t1 = newT1;
    rerouted++;
  }
  // 3b. AAR arrival metering, scoped to the airports the zone disrupts: reroutes
  //     land flights later / groundings remove them, changing those airports'
  //     arrival queues. Price the resulting holds (air vs gate, by live fuel)
  //     under RBS and CDM substitution. Baseline uses each flight's ORIGINAL
  //     landing time so the delta isolates the zone's effect.
  const atImpacted = (f) => impacted.has(f.d);
  const flyingNow = state.flights.filter((f) => !f.blocked && atImpacted(f));
  const postAAR = meterArrivals(flyingNow, (f) => f.t1, state.aar);
  const baseAAR = meterArrivals(
    state.flights.filter(atImpacted),
    (f) => (f._orig ? f._orig.t1 : f.t1),
    state.aar,
  );

  // 4. recompute the sector demand cascade from the rerouted routes
  state.nfz = box;
  state.nfzDemand = computeDemandFromRoutes();
  state.nfzStats = {
    rerouted,
    grounded,
    addedDelayMin: Math.round(addedDelay / 60),
    overBefore: peakOverCount(state.baselineDemandJS),
    overAfter: peakOverCount(state.nfzDemand),
    airportsClosed: state.airports.filter((a) => a.closed).length,
    airportsAffected: state.airports.filter((a) => !a.closed && a.cancelled > 0)
      .length,
    extraNm: Math.round(extraNmTot),
    extraFuelKg: Math.round(extraKgTot),
    extraFuelUsd: Math.round(extraUsdTot),
    extraCo2Kg: Math.round(extraCo2Tot),
    worstFlights: worst.sort((a, b) => b.extra_usd - a.extra_usd).slice(0, 5),
    fuelUsdGal: state.fuelUsdGal,
    // AAR arrival-hold (live-fuel-priced air-vs-gate decision)
    aar: state.aar,
    aarHoldUsdExtra: Math.round(postAAR.rbsUsd - baseAAR.rbsUsd), // zone-attributable
    cdmSaveUsd: Math.round(postAAR.rbsUsd - postAAR.subUsd), // optimization win
    cdmSaveCo2Kg: Math.round(postAAR.rbsCo2 - postAAR.subCo2),
    airHoldMin: Math.round(postAAR.airMin),
    gateHoldMin: Math.round(postAAR.gateMin),
    divRbs: postAAR.divRbs,
    divSub: postAAR.divSub,
  };
  rebuildTrips();
  updateMeta();
  render();
}
function clearNFZ() {
  applyNFZ(null);
}

// ---------- AI chat: browser-side tool adapters ----------
// The chatbot drives the SAME in-browser engine the manual controls do. Each
// adapter runs a real scenario and returns a compact JSON the model narrates —
// so every number in a reply provably came from the engine, never invented.
function nmToBox(lat, lon, radiusNm) {
  const dLat = radiusNm / 60; // 1 nm = 1/60 degree of latitude
  const dLon =
    radiusNm / 60 / Math.max(Math.cos((lat * Math.PI) / 180), 0.01);
  return { w: lon - dLon, e: lon + dLon, s: lat - dLat, n: lat + dLat };
}
window.AIRSPACE = {
  // current jet-fuel price the engine is using (live feed or manual)
  fuelPrice() {
    const el = document.getElementById("fuel-src");
    return {
      usd_per_gal: state.fuelUsdGal,
      source: el ? el.textContent : "manual",
    };
  },
  // ground a fuzzy place to an airport's real coordinates when it's an ICAO
  airportPos(icao) {
    const a = state.airportByIcao.get((icao || "").toUpperCase());
    return a ? { icao: a.icao, lat: a.lat, lon: a.lon } : null;
  },
  // TOOL 1: apply a keep-out zone and report the fleet-wide impact
  simulate_no_fly_zone({
    center_lat,
    center_lon,
    place_name,
    radius_nm = 60,
    fuel_usd_per_gal,
  }) {
    if (!isFinite(center_lat) || !isFinite(center_lon))
      return { error: "center_lat and center_lon are required numbers" };
    if (isFinite(fuel_usd_per_gal)) setFuelPrice(+fuel_usd_per_gal, "manual");
    const box = nmToBox(center_lat, center_lon, radius_nm);
    applyNFZ(box); // reroutes/grounds the fleet, prices it, redraws the map
    map.easeTo({ center: [center_lon, center_lat], zoom: 5, duration: 800 });
    const s = state.nfzStats || {};
    return {
      place_name: place_name || "the area",
      radius_nm,
      flights_rerouted: s.rerouted || 0,
      flights_grounded: s.grounded || 0,
      total_extra_nm: s.extraNm || 0,
      total_extra_fuel_usd: s.extraFuelUsd || 0,
      total_extra_co2_tonnes: Math.round((s.extraCo2Kg || 0) / 100) / 10,
      added_reroute_delay_min: s.addedDelayMin || 0,
      airports_closed: s.airportsClosed || 0,
      sectors_over_capacity_before: s.overBefore || 0,
      sectors_over_capacity_after: s.overAfter || 0,
      arrival_hold_extra_usd: s.aarHoldUsdExtra || 0,
      cdm_savings_usd: s.cdmSaveUsd || 0,
      diversion_risk_flights: s.divRbs || 0,
      worst_flights: s.worstFlights || [],
      worst_flights_capped_at: 5,
      fuel_usd_per_gal: state.fuelUsdGal,
    };
  },
  // TOOL 2: congestion / weather cost of metering arrivals at one hub
  analyze_hub({ hub, weather_severity = 1.0, base_aar = 55, fuel_usd_per_gal }) {
    hub = (hub || "").toUpperCase();
    if (!hub) return { error: "hub (ICAO code) is required" };
    if (isFinite(fuel_usd_per_gal)) setFuelPrice(+fuel_usd_per_gal, "manual");
    const arrivals = state.flights.filter((f) => f.d === hub);
    if (!arrivals.length)
      return { error: `no flights arriving at ${hub} in this snapshot`, hub };
    // worse weather throttles the landing rate (acceptance rate)
    const aar = Math.max(
      1,
      Math.round(base_aar / Math.max(weather_severity, 0.1)),
    );
    const m = meterArrivals(arrivals, (f) => f.t1, aar);
    const a = state.airportByIcao.get(hub);
    if (a) map.easeTo({ center: [a.lon, a.lat], zoom: 6, duration: 800 });
    return {
      hub,
      arrivals_metered: arrivals.length,
      base_aar,
      weather_severity,
      effective_aar: aar,
      congestion_hold_usd: Math.round(m.rbsUsd),
      cdm_savings_usd: Math.round(m.rbsUsd - m.subUsd),
      cdm_savings_co2_tonnes: Math.round((m.rbsCo2 - m.subCo2) / 100) / 10,
      air_hold_min: Math.round(m.airMin),
      gate_hold_min: Math.round(m.gateMin),
      diversion_risk_flights: m.divRbs,
      diversion_risk_after_cdm: m.divSub,
      fuel_usd_per_gal: state.fuelUsdGal,
    };
  },
};

// ---------- rendering ----------
let imageCache = {};
function wxImage(path) {
  if (!path) return null;
  const url = `${DATA}/${state.snap.id}/${path}`;
  if (!(url in imageCache)) imageCache[url] = url; // deck loads URLs directly
  return url;
}

function render() {
  if (!state.snap) return;
  // forecast horizon advances the WHOLE view (flights, weather, sectors) to
  // state.t + horizon, so flights are drawn where they'll be and the ones that
  // will cross a storm (precomputed cf intervals) light up in advance.
  const dt = dispTime();
  const k = stripAt(dt); // forecast-shifted weather strip (= now when horizon 0)
  const strip = state.snap.strips[k];
  const o = state.opts;
  const bounds = [
    state.grid.lon_min,
    state.grid.lat_min,
    state.grid.lon_max,
    state.grid.lat_max,
  ];

  // compute live flight positions. In GDP scenario, each flight's whole
  // trajectory is shifted later by its assigned ground delay (constant speed).
  const gdpOn = o.scenario === "gdp";
  const pts = [];
  const ghosts = []; // baseline positions of held flights (where they'd be sans GDP)
  const activeIcaos = new Set(); // airports of flights airborne right now
  let nAir = 0,
    nConf = 0;
  for (const f of state.flights) {
    if (state.nfz && f.blocked) continue; // grounded by the no-fly zone
    const delay = gdpOn ? f.gdpDelay || 0 : 0;
    const t0 = f.t0 + delay;
    const t1 = f.t1 + delay;
    // ghost: where this held flight would be in the baseline (no-GDP) timeline
    if (gdpOn && delay > 0 && dt >= f.t0 && dt <= f.t1) {
      const gf = (dt - f.t0) / (f.t1 - f.t0);
      ghosts.push({ position: positionAt(f, gf), f });
    }
    if (dt < t0 || dt > t1) continue;
    nAir++;
    activeIcaos.add(f.o);
    activeIcaos.add(f.d);
    const conf = inConflict(f, k); // predicted in-storm at the forecast strip
    if (conf) nConf++;
    const affected = f.cf && f.cf.length > 0; // route crosses a storm at some point
    if (o.conflictsOnly && !conf) continue;
    const frac = (dt - t0) / (t1 - t0);
    const [lon, lat] = positionAt(f, frac);
    f._pos = [lon, lat];
    // heading: bearing between a point just behind and just ahead on the route
    const df = Math.min(0.02, 120 / (t1 - t0));
    const a = positionAt(f, Math.max(0, frac - df));
    const b = positionAt(f, Math.min(1, frac + df));
    const bearing = bearingDeg(a[1], a[0], b[1], b[0]);
    pts.push({ f, position: [lon, lat], conf, bearing, affected });
  }
  state.conflictPts = pts.filter((p) => p.conf);

  // CLICKING a flight (or its ghost) spotlights it and fades the rest.
  // Hover only updates the tooltip (+ "route of hovered"), no re-render.
  const hl = state.selected;
  const onHover = (info) => {
    state.hovered = info.object && info.object.f ? info.object.f : null;
  };
  const onClick = (info) => {
    if (info.object && info.object.f) selectFlight(info.object.f);
  };
  const DIM = 30;
  const flightColor = (d) => {
    let c;
    if (d.conf) c = [255, 59, 59]; // in storm now/forecast — red
    else if (o.wxAffected && d.affected) c = [255, 170, 40]; // route hits a storm — amber
    else c = d.f.alt >= 35000 ? [77, 163, 255] : [70, 211, 154];
    let alpha = 255;
    if (hl && d.f !== hl) alpha = DIM;
    else if (o.wxAffected && !d.affected && !d.conf) alpha = 55; // fade the unaffected
    return [c[0], c[1], c[2], alpha];
  };

  const layers = [];

  // no-fly zone rectangle (active = solid red; preview = dashed while dragging)
  const nfzBox = state.nfzPreview || state.nfz;
  if (nfzBox) {
    const b = nfzBox;
    const ring = [
      [b.w, b.s],
      [b.e, b.s],
      [b.e, b.n],
      [b.w, b.n],
      [b.w, b.s],
    ];
    const preview = !!state.nfzPreview;
    layers.push(
      new GeoJsonLayer({
        id: "nfz",
        data: {
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [ring] },
        },
        stroked: true,
        filled: true,
        getFillColor: [255, 60, 60, preview ? 30 : 55],
        getLineColor: [255, 80, 80, 255],
        getLineWidth: 2,
        lineWidthUnits: "pixels",
      }),
    );
  }

  if (o.retop && strip.retop) {
    layers.push(
      new BitmapLayer({
        id: "retop",
        image: wxImage(strip.retop),
        bounds,
        opacity: o.wxOpacity,
        _imageCoordinateSystem: 0,
      }),
    );
  }
  if (o.refc && strip.refc) {
    layers.push(
      new BitmapLayer({
        id: "refc",
        image: wxImage(strip.refc),
        bounds,
        opacity: o.wxOpacity,
      }),
    );
  }

  let nOver = 0;
  if (o.sectorBand !== "off" && state.sectorsByBand) {
    const band = o.sectorBand;
    const step = currentStep();
    const feats = state.sectorsByBand[band];
    for (const f of feats) {
      const c = sectorDemand(band, f.properties.name, step);
      if (c > effCap(band, f.properties.name, step)) nOver++;
    }
    const trig = [state.t, band, o.scenario, o.horizon, o.stormCap];
    layers.push(
      new GeoJsonLayer({
        id: "sectors",
        data: { type: "FeatureCollection", features: feats },
        pickable: true,
        stroked: true,
        filled: true,
        getFillColor: (f) =>
          sectorFill(
            sectorDemand(band, f.properties.name, step),
            effCap(band, f.properties.name, step),
          ),
        // violet edge = storm-reduced capacity; red = over capacity (App palette)
        getLineColor: (f) => {
          const nm = f.properties.name;
          if (sectorDemand(band, nm, step) > effCap(band, nm, step))
            return [255, 90, 90, 255];
          if (stormRed(band, nm, step) > 0) return [199, 125, 255, 200];
          return [120, 140, 170, 90];
        },
        getLineWidth: (f) => {
          const nm = f.properties.name;
          if (sectorDemand(band, nm, step) > effCap(band, nm, step)) return 2;
          return stormRed(band, nm, step) > 0 ? 1.5 : 0.5;
        },
        lineWidthUnits: "pixels",
        updateTriggers: {
          getFillColor: trig,
          getLineColor: trig,
          getLineWidth: trig,
        },
        onHover,
        onClick: (info) => {
          if (info.object) locateSector(info.object);
        },
      }),
    );
  }

  // highlight the located sector (from the sectors-by-capacity panel)
  if (state.focusedSector) {
    layers.push(
      new GeoJsonLayer({
        id: "focus-sector",
        data: { type: "FeatureCollection", features: [state.focusedSector] },
        stroked: true,
        filled: false,
        getLineColor: [255, 230, 90, 255],
        getLineWidth: 2.5,
        lineWidthUnits: "pixels",
      }),
    );
  }

  if (o.trails && state.hovered) {
    const f = state.hovered;
    const path = f.la.map((la, i) => [f.lo[i], la]);
    layers.push(
      new PathLayer({
        id: "trail",
        data: [{ path }],
        getPath: (d) => d.path,
        getColor: [255, 220, 80],
        getWidth: 2,
        widthUnits: "pixels",
      }),
    );
  }

  // fading motion trails behind every plane (GPU-animated; data is static)
  if (o.motionTrails && state.trips && TripsLayer) {
    layers.push(
      new TripsLayer({
        id: "motion-trails",
        data: state.trips,
        getPath: (d) => d.path,
        getTimestamps: (d) => d.timestamps,
        getColor: (d) => (d.band === "HIGH" ? [77, 163, 255] : [70, 211, 154]),
        opacity: 0.6,
        widthMinPixels: 1.5,
        trailLength: 1800, // seconds of trail
        currentTime: state.t - state.snap.window_start,
        fadeTrail: true,
        jointRounded: true,
        capRounded: true,
      }),
    );
  }

  // baseline "ghosts" of held flights — faint, showing the GDP displacement
  if (gdpOn && ghosts.length) {
    layers.push(
      new ScatterplotLayer({
        id: "gdp-ghosts",
        data: ghosts,
        pickable: true,
        getPosition: (d) => d.position,
        getRadius: (d) => (d.f === hl ? 4.5 : 2.4),
        radiusUnits: "pixels",
        radiusMinPixels: 1.5,
        getFillColor: (d) =>
          hl
            ? d.f === hl
              ? [255, 230, 90, 230]
              : [150, 160, 180, 22]
            : [150, 160, 180, 90],
        updateTriggers: {
          getPosition: state.t,
          getFillColor: hl,
          getRadius: hl,
        },
        onHover,
        onClick,
      }),
    );
  }

  if (o.flights && o.arrows) {
    layers.push(
      new IconLayer({
        id: "flights-arrows",
        data: pts,
        pickable: true,
        getPosition: (d) => d.position,
        getIcon: () => ({
          url: ARROW_ICON,
          width: 64,
          height: 64,
          anchorX: 32,
          anchorY: 32,
          mask: true,
        }),
        getAngle: (d) => -d.bearing, // icon points north at 0; rotate CW to heading
        getSize: (d) => (d.f === hl ? 28 : d.conf ? 22 : 16),
        sizeUnits: "pixels",
        getColor: flightColor,
        updateTriggers: {
          getAngle: state.t,
          getColor: [state.t, hl, o.wxAffected],
          getSize: [state.t, hl],
        },
        onHover,
        onClick,
      }),
    );
  } else if (o.flights) {
    layers.push(
      new ScatterplotLayer({
        id: "flights-dots",
        data: pts,
        pickable: true,
        getPosition: (d) => d.position,
        getRadius: (d) => (d.f === hl ? 6 : d.conf ? 4 : 2.6),
        radiusUnits: "pixels",
        radiusMinPixels: 1.5,
        getFillColor: flightColor,
        updateTriggers: {
          getFillColor: [state.t, hl, o.wxAffected],
          getRadius: [state.t, hl],
        },
        onHover,
        onClick,
      }),
    );
  }

  // connector between the hovered held flight and its baseline ghost
  if (hl && gdpOn) {
    const pp = pts.find((p) => p.f === hl);
    const gg = ghosts.find((g) => g.f === hl);
    if (pp && gg) {
      layers.push(
        new PathLayer({
          id: "gdp-connector",
          data: [{ path: [pp.position, gg.position] }],
          getPath: (d) => d.path,
          getColor: [255, 230, 90, 230],
          getWidth: 1.5,
          widthUnits: "pixels",
        }),
      );
    }
  }

  // airports (derived from route endpoints): "active" = all in this snapshot,
  // "hubs" = busiest 30 by traffic. Labels show the busiest.
  if (o.airports !== "off" && state.airports) {
    // "hubs" = busiest 30 (whole window); "active" = airports of flights
    // airborne right now. Closed airports (inside the zone) are always shown.
    let aps =
      o.airports === "hubs"
        ? state.airports
            .slice()
            .sort((a, b) => b.n - a.n)
            .slice(0, 30)
        : state.airports.filter((a) => activeIcaos.has(a.icao));
    if (state.nfz) {
      const have = new Set(aps.map((a) => a.icao));
      for (const a of state.airports)
        if (a.closed && !have.has(a.icao)) aps.push(a);
    }
    const airportColor = (d) =>
      d.closed
        ? [255, 70, 70, 245]
        : d.cancelled > 0
          ? [245, 160, 40, 245]
          : [240, 240, 250, 230];
    layers.push(
      new ScatterplotLayer({
        id: "airports",
        data: aps,
        pickable: true,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: (d) => (d.closed || d.cancelled > 0 ? 5 : 4),
        radiusUnits: "pixels",
        radiusMinPixels: 2.5,
        stroked: true,
        lineWidthMinPixels: 1,
        getLineColor: [30, 30, 40, 255],
        getFillColor: airportColor,
        updateTriggers: {
          getFillColor: [state.nfz, state.t],
          getRadius: [state.nfz, state.t],
        },
        onHover,
      }),
    );
    if (TextLayer) {
      const busy = aps
        .slice()
        .sort((a, b) => b.n - a.n)
        .slice(0, 40);
      layers.push(
        new TextLayer({
          id: "airport-labels",
          data: busy,
          getPosition: (d) => [d.lon, d.lat],
          getText: (d) => d.icao,
          getSize: 11,
          getColor: [255, 255, 255, 235],
          getPixelOffset: [0, -11],
          getTextAnchor: "middle",
          getAlignmentBaseline: "bottom",
          fontWeight: 600,
          background: true,
          getBackgroundColor: [17, 21, 28, 205],
          backgroundPadding: [3, 1],
        }),
      );
    }
  }

  // ring around the located flight (from the in-weather panel)
  if (state.focused) {
    const f = state.focused;
    if (state.t >= f.t0 && state.t <= f.t1) {
      const frac = (state.t - f.t0) / (f.t1 - f.t0);
      const [lon, lat] = positionAt(f, frac);
      layers.push(
        new ScatterplotLayer({
          id: "focus-flight",
          data: [{ position: [lon, lat] }],
          getPosition: (d) => d.position,
          getRadius: 9,
          radiusUnits: "pixels",
          stroked: true,
          filled: false,
          getLineColor: [255, 230, 90, 255],
          lineWidthMinPixels: 2,
          updateTriggers: { getPosition: state.t },
        }),
      );
    }
  }

  overlay.setProps({
    layers,
    getTooltip: ({ object }) => {
      if (!object) return null;
      if (object.icao) {
        let html = `<b>${object.icao}</b><br>${object.dep} dep · ${object.arr} arr`;
        if (object.closed)
          html += `<br><span style="color:#ff6b6b">closed (in no-fly zone)</span>`;
        else if (object.cancelled > 0)
          html += `<br><span style="color:#f0a040">${object.cancelled} cancelled</span>`;
        return { className: "tooltip", html };
      }
      if (object.f) {
        return {
          className: "tooltip",
          html:
            `<b>${object.f.fn}</b> ${object.f.o}→${object.f.d}<br>` +
            `alt ${object.f.alt.toLocaleString()} ft · ${object.f.spd} kt<br>` +
            (object.conf
              ? `<span style="color:#ff6b6b">in impassable weather</span>`
              : `dep ${fmtClock(object.f.t0)}`),
        };
      }
      if (object.properties && object.properties.band) {
        const p = object.properties;
        const step = currentStep();
        const c = sectorDemand(p.band, p.name, step);
        const cap = effCap(p.band, p.name, step);
        const red = stormRed(p.band, p.name, step);
        const over = c > cap;
        return {
          className: "tooltip",
          html:
            `<b>${p.name}</b><br>` +
            `demand ${c} / capacity ${cap}` +
            (red > 0
              ? `<br><span style="color:#c77dff">storm-reduced ${Math.round(
                  red * 100,
                )}% (was ${p.capacity})</span>`
              : "") +
            (over
              ? `<br><span style="color:#ff6b6b">over capacity</span>`
              : ""),
        };
      }
      return null;
    },
  });

  document.getElementById("clock").textContent =
    fmtClock(state.t) + (o.horizon ? `  ▶ +${o.horizon}m forecast` : "");
  document.getElementById("time").value = state.t;
  const stats = document.getElementById("stats");
  stats.innerHTML =
    `${nAir.toLocaleString()} airborne · ` +
    `<a class="stat-link" id="stat-weather">${nConf} in weather</a>` +
    (o.sectorBand !== "off" ? ` · ${nOver} sectors over capacity` : "");
  const sw = document.getElementById("stat-weather");
  if (sw) sw.onclick = () => openDock("conflicts");

  state.frame++;
  updateDock();
}

// ---------- animation ----------
function tick(ts) {
  if (!state.playing) return;
  if (state.lastFrame == null) state.lastFrame = ts;
  const dt = (ts - state.lastFrame) / 1000;
  state.lastFrame = ts;
  state.t += dt * state.speed;
  if (state.t >= state.snap.window_end) {
    state.t = state.snap.window_end;
    stop();
  }
  render();
  requestAnimationFrame(tick);
}
function play() {
  if (state.t >= state.snap.window_end) state.t = state.snap.window_start;
  state.playing = true;
  state.lastFrame = null;
  document.getElementById("play").textContent = "II";
  requestAnimationFrame(tick);
}
function stop() {
  state.playing = false;
  document.getElementById("play").textContent = "PLAY";
}

// ---------- no-fly-zone draw interaction ----------
function boxFrom(a, b) {
  return {
    w: Math.min(a.lng, b.lng),
    e: Math.max(a.lng, b.lng),
    s: Math.min(a.lat, b.lat),
    n: Math.max(a.lat, b.lat),
  };
}
function startDraw() {
  state.drawing = true;
  document.getElementById("nfz-draw").classList.add("active");
  map.getCanvas().style.cursor = "crosshair";
  map.dragPan.disable();
}
function endDraw() {
  state.drawing = false;
  document.getElementById("nfz-draw").classList.remove("active");
  map.getCanvas().style.cursor = "";
  map.dragPan.enable();
}
let nfzDragStart = null;
map.on("mousedown", (e) => {
  if (!state.drawing) return;
  nfzDragStart = e.lngLat;
});
map.on("mousemove", (e) => {
  if (!state.drawing || !nfzDragStart) return;
  state.nfzPreview = boxFrom(nfzDragStart, e.lngLat);
  render();
});
map.on("mouseup", (e) => {
  if (!state.drawing || !nfzDragStart) return;
  const box = boxFrom(nfzDragStart, e.lngLat);
  nfzDragStart = null;
  state.nfzPreview = null;
  endDraw();
  if (box.e - box.w < 0.05 || box.n - box.s < 0.05) {
    render();
    return;
  }
  applyNFZ(box); // real-time reprocess
});

// ---------- flight selection (click to spotlight) ----------
let lastSelectAt = 0;
function selectFlight(f) {
  lastSelectAt = performance.now();
  state.selected = state.selected === f ? null : f; // toggle
  render();
}
// click on empty map clears the selection (deferred so a flight-click can win)
map.on("click", () => {
  if (state.drawing) return;
  setTimeout(() => {
    if (performance.now() - lastSelectAt < 60) return; // a flight was just clicked
    if (state.selected) {
      state.selected = null;
      render();
    }
  }, 0);
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.selected) {
    state.selected = null;
    render();
  }
});

// ---------- right-side dock (always present; collapsible) ----------
// switch the active tab (and expand the dock if it was collapsed)
function openDock(tab) {
  state.dock = tab;
  if (state.dockCollapsed) setCollapsed(false);
  for (const b of document.querySelectorAll("#rd-tabs button[data-tab]"))
    b.classList.toggle("active", b.dataset.tab === tab);
  document
    .getElementById("rd-conflicts")
    .classList.toggle("hidden", tab !== "conflicts");
  document
    .getElementById("rd-sectors")
    .classList.toggle("hidden", tab !== "sectors");
  updateDock(true);
}
function setCollapsed(collapsed) {
  state.dockCollapsed = collapsed;
  document.getElementById("rightdock").classList.toggle("collapsed", collapsed);
  document.getElementById("rd-collapse").textContent = collapsed ? "+" : "–";
  if (!collapsed) updateDock(true);
}
function updateDock(force) {
  if (state.dockCollapsed) return;
  if (!force && state.playing && state.frame % 20 !== 0) return; // throttle
  if (state.dock === "conflicts") renderConflictList();
  else renderSectorList();
}

function renderConflictList() {
  const el = document.getElementById("rd-conflicts");
  const list = state.conflictPts
    .slice()
    .sort((a, b) => a.f.fn.localeCompare(b.f.fn));
  if (!list.length) {
    el.innerHTML = `<div class="rd-empty">No flights in impassable weather right now.</div>`;
    return;
  }
  let html = `<div class="rd-head">${list.length} flights in weather</div>`;
  list.forEach((p, i) => {
    const f = p.f;
    html +=
      `<div class="rd-row" data-i="${i}"><b>${f.fn}</b>` +
      `<span class="rd-sub">${f.o}→${f.d} · ${Math.round(f.alt / 1000)}k ft</span></div>`;
  });
  el.innerHTML = html;
  el.querySelectorAll(".rd-row").forEach((row) => {
    row.onclick = () => locateFlight(list[+row.dataset.i].f);
  });
}

const SECTOR_SORTS = {
  demand: (a, b) => b.dem - a.dem || b.cap - a.cap,
  load: (a, b) => b.dem / b.cap - a.dem / a.cap || b.dem - a.dem,
  cap: (a, b) => b.cap - a.cap || b.dem - a.dem,
  name: (a, b) => a.f.properties.name.localeCompare(b.f.properties.name),
};
const SORT_LABELS = {
  demand: "demand",
  load: "load %",
  cap: "capacity",
  name: "name",
};

function renderSectorList() {
  const el = document.getElementById("rd-sectors");
  const band = state.dockBand;
  const step = currentStep();
  const rows = state.sectorsByBand[band]
    .map((f) => ({
      f,
      cap: effCap(band, f.properties.name, step),
      dem: sectorDemand(band, f.properties.name, step),
      storm: stormRed(band, f.properties.name, step) > 0,
    }))
    .sort(SECTOR_SORTS[state.dockSort] || SECTOR_SORTS.demand);
  const sortLinks = Object.keys(SORT_LABELS)
    .map(
      (k) =>
        `<a data-sort="${k}" class="${state.dockSort === k ? "on" : ""}">${SORT_LABELS[k]}</a>`,
    )
    .join(`<span class="rd-sep">·</span>`);
  let html =
    `<div class="rd-head">${band} sectors` +
    `<span class="rd-bandtoggle">` +
    `<a data-band="LOW" class="${band === "LOW" ? "on" : ""}">LOW</a>` +
    `<a data-band="HIGH" class="${band === "HIGH" ? "on" : ""}">HIGH</a></span></div>` +
    `<div class="rd-sort">sort: ${sortLinks}</div>`;
  rows.forEach((r, i) => {
    const over = r.dem > r.cap;
    html +=
      `<div class="rd-row" data-i="${i}"><b>${r.f.properties.name}</b>` +
      `<span class="rd-sub ${over ? "over" : ""}">${r.storm ? "⛈ " : ""}${r.dem} / ${r.cap}${over ? " over" : ""}</span></div>`;
  });
  el.innerHTML = html;
  el.querySelectorAll(".rd-bandtoggle a").forEach((a) => {
    a.onclick = () => {
      state.dockBand = a.dataset.band;
      renderSectorList();
    };
  });
  el.querySelectorAll(".rd-sort a").forEach((a) => {
    a.onclick = () => {
      state.dockSort = a.dataset.sort;
      renderSectorList();
    };
  });
  el.querySelectorAll(".rd-row").forEach((row) => {
    row.onclick = () => locateSector(rows[+row.dataset.i].f);
  });
}

function locateFlight(f) {
  state.focused = f;
  if (state.t >= f.t0 && state.t <= f.t1) {
    const [lon, lat] = positionAt(f, (state.t - f.t0) / (f.t1 - f.t0));
    map.easeTo({
      center: [lon, lat],
      zoom: Math.max(map.getZoom(), 6),
      duration: 600,
    });
  }
  render();
}
function sectorCentroid(feat) {
  const ring = feat.geometry.coordinates[0];
  let x = 0,
    y = 0;
  for (const [lon, lat] of ring) {
    x += lon;
    y += lat;
  }
  return [x / ring.length, y / ring.length];
}
function locateSector(feat) {
  // click the already-selected sector again to deselect it
  if (
    state.focusedSector &&
    state.focusedSector.properties.name === feat.properties.name
  ) {
    state.focusedSector = null;
    render();
    return;
  }
  state.focusedSector = feat;
  map.easeTo({
    center: sectorCentroid(feat),
    zoom: Math.max(map.getZoom(), 5),
    duration: 600,
  });
  render();
}

// ---------- shareable frame link ----------
function buildShareURL() {
  const o = state.opts;
  const meta = state.snap;
  const frac =
    (state.t - meta.window_start) / (meta.window_end - meta.window_start);
  const c = map.getCenter();
  const p = new URLSearchParams();
  p.set("snap", state.index.indexOf(meta));
  p.set("t", frac.toFixed(4));
  if (!o.flights) p.set("flights", "0");
  if (!o.refc) p.set("refc", "0");
  if (o.retop) p.set("retop", "1");
  if (o.conflictsOnly) p.set("conf", "1");
  if (o.arrows) p.set("arrows", "1");
  if (o.motionTrails) p.set("trails", "1");
  if (o.sectorBand !== "off") p.set("sectors", o.sectorBand);
  if (o.airports !== "off") p.set("airports", o.airports);
  if (o.scenario !== "baseline") p.set("scen", o.scenario);
  if (state.dock) p.set("dock", state.dock);
  if (state.nfz) {
    const b = state.nfz;
    p.set(
      "nfz",
      `${b.w.toFixed(3)},${b.s.toFixed(3)},${b.e.toFixed(3)},${b.n.toFixed(3)}`,
    );
  }
  p.set(
    "view",
    `${c.lng.toFixed(3)},${c.lat.toFixed(3)},${map.getZoom().toFixed(2)}`,
  );
  return location.origin + location.pathname + "?" + p.toString();
}
async function copyShare() {
  const url = buildShareURL();
  history.replaceState(null, "", url); // reflect in address bar too
  const btn = document.getElementById("share");
  const old = btn.textContent;
  try {
    await navigator.clipboard.writeText(url);
    btn.textContent = "Link copied!";
  } catch {
    btn.textContent = "Copy from address bar";
  }
  setTimeout(() => (btn.textContent = old), 1300);
}

// ---------- controls ----------
document.getElementById("play").onclick = () =>
  state.playing ? stop() : play();
document.getElementById("time").oninput = (e) => {
  stop();
  state.t = +e.target.value;
  render();
};
document.getElementById("speed").onchange = (e) =>
  (state.speed = +e.target.value);
document.getElementById("wx-opacity").oninput = (e) => {
  state.opts.wxOpacity = e.target.value / 100;
  render();
};
const bind = (id, key) =>
  (document.getElementById(id).onchange = (e) => {
    state.opts[key] = e.target.checked;
    render();
  });
bind("ly-flights", "flights");
bind("ly-refc", "refc");
bind("ly-retop", "retop");
bind("ly-conflicts-only", "conflictsOnly");
bind("ly-wx-affected", "wxAffected");
bind("ly-trails", "trails");
bind("ly-arrows", "arrows");
bind("ly-motion-trails", "motionTrails");
document.getElementById("ly-stormcap").onchange = (e) => {
  state.opts.stormCap = e.target.checked;
  if (state.nfz) applyNFZ(state.nfz); // storm-cap changes before/after over-counts
  updateMeta();
  render();
};
document.getElementById("horizon").onchange = (e) => {
  state.opts.horizon = +e.target.value;
  render();
};
document.getElementById("airport-mode").onchange = (e) => {
  state.opts.airports = e.target.value;
  render();
};
document.getElementById("sector-band").onchange = (e) => {
  state.opts.sectorBand = e.target.value;
  render();
};
document.getElementById("scenario").onchange = (e) => {
  state.opts.scenario = e.target.value;
  updateMeta();
  render();
};
document.getElementById("share").onclick = copyShare;
document.getElementById("nfz-draw").onclick = () =>
  state.drawing ? endDraw() : startDraw();
document.getElementById("nfz-clear").onclick = clearNFZ;
// set the fuel price + label, then re-price the active reroute (if any).
// src: "manual" | a live-feed description string.
function setFuelPrice(v, src) {
  state.fuelUsdGal = v;
  document.getElementById("fuel-price").value = v;
  document.getElementById("fuel-price-out").textContent = v.toFixed(2);
  const el = document.getElementById("fuel-src");
  const live = src && src !== "manual";
  el.textContent = live ? src : "manual";
  el.title = live ? src : "drag to set a hypothetical price";
  el.classList.toggle("live", !!live);
  if (state.nfz) applyNFZ(state.nfz);
}
document.getElementById("fuel-price").oninput = (e) =>
  setFuelPrice(+e.target.value, "manual");

// pull the live jet-fuel price from the optional /api/fuel endpoint (serve.py).
// Static file servers 404 it — we just keep the slider default then.
async function loadLiveFuel() {
  try {
    const r = await fetch("/api/fuel");
    if (!r.ok) return;
    const p = await r.json();
    if (p && p.live && isFinite(p.usd_per_gal)) {
      setFuelPrice(+p.usd_per_gal, `live · ${p.as_of || p.source || "market"}`);
    }
  } catch (_) {
    /* offline / static server — manual slider stands */
  }
}
document.getElementById("fuel-live-link").onclick = loadLiveFuel;
document.getElementById("aar").oninput = (e) => {
  state.aar = +e.target.value;
  document.getElementById("aar-out").textContent = state.aar;
  if (state.nfz) applyNFZ(state.nfz); // re-meter arrivals at the new rate
};
document.getElementById("rd-collapse").onclick = () =>
  setCollapsed(!state.dockCollapsed);
document.querySelectorAll("#rd-tabs button[data-tab]").forEach((b) => {
  b.onclick = () => openDock(b.dataset.tab);
});

// Load data independently of the basemap so the app works even if tiles are slow.
loadIndex().catch((e) => console.error("loadIndex failed:", e));
loadLiveFuel(); // best-effort: upgrade the slider to the live market price

