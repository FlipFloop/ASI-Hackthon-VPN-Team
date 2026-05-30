# Chat Interface — Implementation Handoff (MVP)

**Goal:** a chat box where a user types a plain-English scenario —
*"no-fly zone over Chicago, what happens to flights and routes?"* — and the app runs
that scenario through the **existing engines** (as if the user had filled every field by
hand), then replies with a short, **accessible, plain-language summary that leads with the
key flight impact**, and redraws the map.

**Design priorities (in order):** ①accessibility · ②generalize gracefully on vague input ·
③lead with the key flight info in human terms · ④keep it MVP-small.

> The implementer (you/Claude) owns the specifics. This doc fixes the *contract, behavior,
> and quality bar*, not the line-by-line code.

---

## 1. Core principle — the AI is a *front door*, not a new engine

The chatbot must **never invent numbers.** It only does two things:
1. **Extract parameters** from the sentence (which place, how big, what fuel price…).
2. **Narrate the real results** that the existing functions return.

Every figure in a reply must have come from an actual function call. This is enforced
structurally by using **Claude tool use (function calling)** — see §3.

---

## 2. What already exists (reuse, don't rebuild)

| Capability | Function (handed off) | Returns |
|---|---|---|
| Congestion / hub cost | `holdcost.build_event(scenario, hub, base_aar, fuel_usd_per_gal, weather_severity)` | savings, policies, by-airline, per-flight rows |
| Reroute one flight around a zone | `autoroute.reroute(flight, no_fly_zones, fuel_usd_per_gal, concurrent_flights)` | new route + fuel/$/CO₂ + GeoJSON |
| Live fuel price | `fuelfeed.get_price()` | `{usd_per_gal, source, live}` |

**The one new piece to write:** a fleet-level wrapper that applies a no-fly zone to *all*
affected flights and aggregates the impact (see §4). Everything else is glue.

---

## 3. Architecture — the tool-use loop

```
user text ─► Claude (system prompt + tool schemas)
                │  returns: tool_call(name, args)   ← Claude extracted the params
                ▼
        your dispatcher runs the real function
                │  returns: real JSON result
                ▼
        send result back to Claude
                │  returns: final natural-language summary (streamed)
                ▼
        UI: render summary  +  push result.geojson / event to the map
```

Use the Anthropic SDK with **tool use**. Loop: call the model → if it returns a tool call,
execute it and feed the result back → repeat until it returns text. Stream the final text.

**Use the `claude-api` skill when building this** — it sets up tool use + prompt caching
correctly. Cache the system prompt + tool schemas (they're constant) so every turn is cheap.

Model: `claude-sonnet-4-6` is plenty for this (fast, cheap); `claude-opus-4-8` if you want
the richest summaries.

### Tools to expose (JSON-schema sketches)

```jsonc
// 1) The headline scenario the user asked about
{ "name": "simulate_no_fly_zone",
  "description": "Apply a no-fly/keep-out zone and report impact on flights & routes.",
  "input_schema": { "type":"object", "properties": {
    "center_lat": {"type":"number"}, "center_lon": {"type":"number"},
    "place_name": {"type":"string", "description":"what the user called the area, for the summary"},
    "radius_nm":  {"type":"number", "default":60},
    "fuel_usd_per_gal": {"type":"number", "description":"omit to use live price"}
  }, "required":["center_lat","center_lon","place_name"] } }

// 2) Congestion / weather at a hub
{ "name":"analyze_hub", "description":"Cost of congestion at an airport; optional storm.",
  "input_schema": { "type":"object", "properties": {
    "hub": {"type":"string","description":"ICAO, e.g. KORD"},
    "weather_severity": {"type":"number","default":1.0},
    "base_aar": {"type":"number","default":55},
    "fuel_usd_per_gal": {"type":"number"}
  }, "required":["hub"] } }
```

**Geocoding tip:** make `center_lat/lon` Claude's job — instruct it to fill them from its own
knowledge of the place ("Chicago ≈ 41.88, −87.63"). For airports, ground it with the existing
`airport_pos(icao)`. No geocoding dependency needed for the MVP.

---

## 4. The one new function: `simulate_no_fly_zone`

A thin wrapper over `reroute()` (you flagged this as the "whole-fleet" hook). Pseudocode:

```python
def simulate_no_fly_zone(center_lat, center_lon, radius_nm=60, fuel=None):
    zone = circle_to_polygon(center_lat, center_lon, radius_nm)   # ~12-pt polygon
    flights = load_cached(SCENARIO)
    affected, total_extra_nm, total_usd, total_co2, lines = [], 0, 0, 0, []
    for f in flights:
        if not path_crosses(f, zone):        # cheap pre-filter
            continue
        out = reroute(f, [zone], fuel_usd_per_gal=fuel, concurrent_flights=flights)
        if out["rerouted"] and out["reroute"]["extra_nm"] > 0:
            affected.append(out); lines.append(out["geojson"])
            total_extra_nm += out["reroute"]["extra_nm"]
            total_usd      += out["cost"]["extra_fuel_usd"]
            total_co2      += out["cost"]["extra_co2_kg"]
    return {
      "n_affected": len(affected),
      "total_extra_nm": round(total_extra_nm),
      "total_extra_usd": round(total_usd),
      "total_extra_co2_t": round(total_co2/1000, 1),
      "worst_flights": top_n(affected, 5),     # for "key flights first" (see §6)
      "geojson": merge(lines),                 # for the map
      "assumptions": {"radius_nm": radius_nm, "fuel_used": fuel or "live"},
    }
```

Keep it bounded for the MVP (e.g. cap at the N most-affected flights, and say so) so latency
stays a few seconds.

---

## 5. Accessibility requirements (priority #1 — non-negotiable for the MVP)

Build the chat as an **accessible component from the start**:

- **Semantic structure:** transcript is a `role="log"` region with `aria-live="polite"` so
  screen readers announce each new reply without stealing focus. The input is a real
  `<label>`+`<textarea>`/`<input>`, submit is a real `<button>`.
- **Keyboard:** fully operable with no mouse — Tab order is logical, Enter sends, Shift+Enter
  newlines, focus returns to the input after a reply. Visible focus rings.
- **Loading state:** announce "working…" via the live region (not just a spinner), and disable
  the send button while a turn is in flight.
- **Don't rely on color alone:** the map uses red/green/orange — in the *text* summary always
  name the thing ("reroute", "no-fly zone", "diversion risk"), and ensure WCAG **AA contrast**
  (≥4.5:1) for all text.
- **Plain language:** target ~8th-grade reading level; **expand every acronym on first use**
  ("acceptance rate (how many planes the airport can land per hour)"). No raw ICAO codes
  without the city name.
- **Reduced motion:** respect `prefers-reduced-motion` (no auto-animations for those users).
- **Resizable / responsive:** works zoomed to 200% and on a narrow screen.
- **Lower the barrier:** show **3–4 example prompts** as clickable chips ("No-fly zone over
  Denver", "Storm over Atlanta at $6 fuel") so users aren't staring at an empty box.

A quick screen-reader pass (VoiceOver on Mac) before the demo is worth it.

---

## 6. Output design — key flight info first, in human terms

Every reply follows an **inverted-pyramid** template (most important first):

1. **One headline sentence — the "so what."**
   *"A no-fly zone over Chicago forces 38 flights to reroute, adding about $214,000 in fuel today."*
2. **The 2–3 numbers that matter, plainly stated** (flights affected · extra fuel $ · CO₂).
   Round them; no false precision.
3. **The few flights that matter most**, named in human terms — not a 200-row dump:
   *"Hardest hit: United 482 (Newark→San Francisco) detours 140 miles (+$3,100); …"*
4. **Then** the supporting context (which sectors tip over, assumptions made).
5. **State assumptions in one line** ("Assumed a 60-mile zone and today's live fuel price.").

Rules: lead with impact not method; never open with a number the reader can't interpret;
prefer "about $214k" over "$213,847.12"; pair the summary with the map redraw so users see
*and* read the result.

Give Claude this template **in the system prompt** so every answer is consistent.

---

## 7. Generalizing on vague input (priority #2)

The bot must **act, not stall.** System-prompt rules:

- **Fill sensible defaults** rather than refuse: default radius 60 nm, default scenario,
  live fuel price, "all affected flights." Then **state what you assumed** (§6 line 5).
- **Only ask a clarifying question if the request is unrunnable** (e.g. no location at all) —
  and ask **exactly one**, short, with a suggested default ("Which airport — or should I use
  the busiest, Atlanta?").
- **Map fuzzy places** to coordinates via Claude's own knowledge; if it's clearly an airport,
  use `airport_pos`.
- **If the question is broad** ("what happens to flights?"), pick the most informative single
  tool call, run it, and offer one follow-up ("Want me to compare another city?").
- **Never** reply with only a question when it could have produced a useful result.

---

## 8. System prompt (starting draft)

> You are an airspace operations assistant. Users describe a scenario in plain English; you
> translate it into a tool call, then explain the **real results** the tool returns. Rules:
> (1) Never state a number that didn't come from a tool result. (2) Prefer acting with sensible
> defaults over asking; if you must ask, ask one short question with a suggested default.
> (3) For places, fill center_lat/center_lon from your own geographic knowledge; for airports
> use their ICAO. (4) Write for a non-expert: expand acronyms, ~8th-grade level, lead with the
> single most important impact, then 2–3 key numbers (rounded), then the few hardest-hit flights
> by name, then assumptions in one line. (5) Keep replies under ~120 words unless asked for more.

---

## 9. Suggested surface area (keep it small)

- `POST /api/chat` — body `{messages:[...]}`, runs the tool-use loop, **streams** the reply;
  include the final `geojson`/event payload so the client updates the map.
- A chat panel component (the accessible one from §5) docked beside the existing map.
- `simulate_no_fly_zone` added next to the other engine functions.

That's the whole MVP: one endpoint, one wrapper function, one accessible UI panel, two tool
schemas, one system prompt.

---

## 10. Build order (half-day plan)

1. Write `simulate_no_fly_zone` (§4) and unit-test it on one obvious case (zone over a hub).
2. Define the two tool schemas + system prompt (§3, §8).
3. Implement the tool-use loop behind `POST /api/chat` (use the **`claude-api` skill**; turn on
   **prompt caching** for the system prompt + schemas).
4. Build the accessible chat panel (§5) with example-prompt chips.
5. Wire the returned `geojson`/event into the existing map render.
6. Screen-reader + keyboard pass; tune the summary template (§6) on 3–4 real prompts.

---

## 11. Honest scope / limitations to keep in mind

- Same underlying caveats as the engines: constant-cruise model, burn estimated from cruise
  speed, acceptance rate is an assumption, single data snapshot — so frame outputs as
  *"what-if estimates,"* not forecasts. Put that disclaimer once in the UI, not every reply.
- Claude-as-geocoder is great for well-known places; obscure spots may be approximate — fine
  for an MVP, note it if asked.
- Bound the fleet loop (top-N flights) so latency and token use stay sane; disclose the cap.

---

## 12. Paste-ready prompt for Claude in the main repo

> I'm adding an accessible AI chat box to our airspace app. Read `ChatInterfaceHandoff.md`,
> `AlgorithmHandoff.md`, and the existing `holdcost.py` / `autoroute.py` / `fuelfeed.py`. Build
> the MVP exactly as the handoff specifies: the `simulate_no_fly_zone` wrapper, two Claude tool
> schemas, a `POST /api/chat` tool-use loop with prompt caching, and an accessible chat panel
> (ARIA live region, full keyboard support, plain-language output that leads with key flight
> impact) wired into our existing map. Prioritize accessibility and graceful handling of vague
> requests per §5 and §7. Keep it small; don't rebuild the engines. Here are our current files: [paste]
