# AI Chatbot — How It Works

A guide to the plain-English "what-if" assistant docked on the airspace map.
A user types something like *"no-fly zone over Chicago — what happens to flights?"*
and the app runs that scenario through the **real engine**, redraws the map, and
replies with a short, accessible summary that leads with the key flight impact.

> **One-line mental model:** the AI is a **front door**, not a new engine. It only
> (1) extracts parameters from the sentence and (2) narrates the real numbers the
> engine returns. It never invents a figure.

---

## 1. The big idea: the engine lives in the browser

The original handoff assumed Python engines (`holdcost.py` / `autoroute.py` /
`fuelfeed.py`). Those were merged away — **all the scenario logic now lives in the
browser** in `public/app.js` (reroute, fleet-wide fuel/CO₂ cost, arrival-hold
metering, CDM optimization, sector-demand cascade). It runs on the flight data the
page already fetched into memory for the snapshot you're viewing.

So the chatbot is **client-driven**: the AI's "tools" execute *in the browser*
against that engine, and the server is just a thin relay to Claude that keeps the
API key off the client.

```
┌──────────────────────────── browser ────────────────────────────┐
│  chat panel (index.html)                                         │
│     │ user types / clicks an example chip                        │
│     ▼                                                            │
│  chat.js  ── owns the tool-use loop ──────────────────────┐      │
│     │ POST /api/chat  { messages: [...] }                  │      │
│     ▼                                                      │      │
└─────┼──────────────────────────────────────────────────── │ ─────┘
      │                                                      │
      ▼                                                      │ loop
┌─ serve.py /api/chat ─┐   stateless proxy                   │
│  chat_backend.py     │── messages.create ──►  Claude API   │
│  (system + tools,    │◄── response (text or tool_use) ─────┘
│   prompt caching)    │        (model: claude-opus-4-8)
└──────────────────────┘
      │ returns Claude's raw response to the browser
      ▼
   chat.js: if Claude asked for a tool ─►
      window.AIRSPACE.simulate_no_fly_zone(args)   (in app.js)
        → runs the REAL engine (applyNFZ / meterArrivals)
        → REDRAWS THE MAP as a side effect
        → returns a compact JSON result
      → chat.js feeds that result back as the next message → loop
   else: render Claude's final summary in the transcript
```

The key inversion vs. a "normal" chatbot: **Claude does not run the loop, and the
server does not run the tools.** Claude returns *one turn* per request; `chat.js`
runs the loop and executes the tools locally.

---

## 2. The turn-by-turn workflow

1. **User input** — types a scenario (Enter sends, Shift+Enter = newline) or clicks
   an example chip. `chat.js` appends it to the `messages` array.
2. **Ask Claude** — `chat.js` POSTs the full `messages` array to `/api/chat`.
   `serve.py` → `chat_backend.run_turn()` calls `messages.create` with the constant
   system prompt + two tool schemas, and returns Claude's raw response.
3. **Branch on `stop_reason`:**
   - **`tool_use`** → Claude extracted the parameters and wants to run a scenario.
     `chat.js` calls the matching function on `window.AIRSPACE`, which executes the
     real engine **and redraws the map**, then returns a compact JSON result.
     `chat.js` appends that as a `tool_result` message and **loops back to step 2**.
   - **`end_turn`** → Claude is done. `chat.js` renders the text summary and stops.
4. **Narration** — on the loop's second pass, Claude sees the real numbers and writes
   the plain-language summary (see §6). Focus returns to the input box.

A loop guard caps this at 6 iterations so a misbehaving turn can't spin forever.

---

## 3. Files involved

| File | Role |
|---|---|
| `public/index.html` | The accessible chat panel markup (`<section id="chat">`) + loads `chat.js` after `app.js`. |
| `public/chat.js` | **The orchestrator.** Owns the tool-use loop, renders the transcript, manages focus/loading state, wires chips + keyboard. |
| `public/app.js` | The map app **and the engine.** Exposes `window.AIRSPACE` (the tool adapters) over the existing `applyNFZ` / `meterArrivals` functions. |
| `public/style.css` | Chat panel styling (dark theme, WCAG-AA contrast, focus rings, reduced-motion). |
| `serve.py` | Static server + `POST /api/chat` (and the pre-existing `/api/fuel`). The chat route is a stateless relay. |
| `chat_backend.py` | Holds the **system prompt**, the **two tool schemas**, and the cached `messages.create` call. The only file that talks to the Anthropic SDK. |
| `serve.sh` | Launches `serve.py` using the data-bundle venv (which has the `anthropic` package). |

---

## 4. The two tools (what the AI can actually do)

Both are defined as JSON schemas in `chat_backend.py` (so Claude knows their shape)
**and** implemented as functions on `window.AIRSPACE` in `app.js` (so the browser can
run them). The names must match exactly on both sides.

### `simulate_no_fly_zone`
The headline scenario. Places a circular keep-out zone and reports fleet-wide impact.
- **Inputs:** `center_lat`, `center_lon`, `place_name` (required); `radius_nm`
  (default 60), `fuel_usd_per_gal` (optional — omit to use the current price).
- **What it does:** converts center+radius → the lat/lon box the existing
  `applyNFZ()` takes, runs it (reroutes/grounds the fleet, prices the detours,
  re-meters arrivals, recomputes sectors, **redraws the map**), and returns:
  flights rerouted/grounded, total extra miles/$/CO₂, added delay, arrival-hold
  cost, CDM savings, diversion-risk count, and the **top-5 hardest-hit flights**.

### `analyze_hub`
Congestion / weather cost at a single airport.
- **Inputs:** `hub` (ICAO code, required); `weather_severity` (default 1.0, higher =
  worse, which lowers the landing rate), `base_aar` (default 55), `fuel_usd_per_gal`.
- **What it does:** filters flights arriving at that hub, meters them at the
  weather-adjusted acceptance rate via the existing `meterArrivals()`, and returns
  the holding-fuel cost, CDM savings, air/gate hold minutes, and diversion risk.

> **Geocoding is Claude's job.** It fills `center_lat`/`center_lon` from its own
> knowledge ("Chicago ≈ 41.88, −87.63") and maps cities to ICAO codes for
> `analyze_hub` (Atlanta = KATL, etc.). No geocoding library is needed.

---

## 5. 🔑 Keys, env, and dependencies (what you MUST have)

| Requirement | Detail |
|---|---|
| **`ANTHROPIC_API_KEY`** | **Required for the chat to work.** Read from the environment by the Anthropic SDK in `chat_backend.py`. Never hard-code it and never send it to the browser. Start the server with it set: `ANTHROPIC_API_KEY=sk-... ./serve.sh`. If it's missing, `/api/chat` returns a friendly error telling you so (the rest of the app still works). |
| **`anthropic` Python package** | Installed in the data-bundle venv (`../hackathon_data_bundle/.venv`). `serve.sh` runs that venv's Python so the import resolves. Install with `../hackathon_data_bundle/.venv/bin/pip install anthropic` if missing. |
| **Built data assets** | The map (and therefore the engine the chat drives) needs `public/data/`. Build it from the bundle: `preprocess.py`, `preprocess_sectors.py`, `gdp.py` (pass a date substring to build a single snapshot, e.g. `python preprocess.py 2025-07-14`). |
| **Model** | `claude-opus-4-8` (set in `chat_backend.py`). Swap to `claude-sonnet-4-6` there if you want it cheaper/faster. |

No key is stored in the repo. The browser only ever talks to `/api/chat` on your own
server; the key lives only in the server process's environment.

---

## 6. Output style (why the replies read the way they do)

The system prompt in `chat_backend.py` enforces an **inverted-pyramid** format:

1. **One headline sentence** — the single biggest impact in human terms.
2. **2–3 rounded numbers** — "about $214,000", not "$213,847.12".
3. **The few hardest-hit flights by name** — "United 482, Newark to San Francisco,
   detours 140 miles, about $3,100 more."
4. **One assumptions line** — radius and fuel price used.

Plus the non-negotiables: **never state a number that didn't come from a tool**,
prefer acting with sensible defaults over asking, ask at most one short question only
if a request is truly unrunnable, expand every acronym on first use, ~8th-grade
reading level, name things in words (don't rely on the map's colors), keep it under
~120 words.

---

## 7. Accessibility (built in, not bolted on)

- Transcript is a `role="log"` + `aria-live="polite"` region — screen readers
  announce each reply without stealing focus.
- Real `<label>` + `<textarea>` + `<button>`; Enter sends, Shift+Enter newlines,
  focus returns to the input after each reply.
- "Working…" is announced via a `role="status"` live region and the send button +
  input are disabled while a turn is in flight.
- Visible focus rings (`:focus-visible`), WCAG-AA text contrast, `prefers-reduced-motion`
  respected (no smooth-scroll for those users), example chips lower the blank-box
  barrier, and a one-time "these are what-if estimates, not forecasts" disclaimer.

---

## 8. Prompt caching (keeps each turn cheap)

The system prompt and tool schemas are **constant**, so `chat_backend.py` puts a
`cache_control` breakpoint on the system block. Because the API renders
`tools → system → messages`, that one breakpoint caches *both* the tools and the
system prompt. Every turn after the first reads them from cache (~0.1× cost) instead
of reprocessing them. You can confirm it's working by watching
`usage.cache_read_input_tokens` climb on the second turn onward.

---

## 9. Run it

```bash
# 1. (once) build at least one snapshot's data, if public/data/ is empty
../hackathon_data_bundle/.venv/bin/python preprocess.py 2025-07-14
../hackathon_data_bundle/.venv/bin/python preprocess_sectors.py 2025-07-14
../hackathon_data_bundle/.venv/bin/python gdp.py 2025-07-14

# 2. serve it WITH the key in the environment
ANTHROPIC_API_KEY=sk-... ./serve.sh          # http://localhost:8765/index.html

# stop it later
kill $(lsof -ti tcp:8765)
```

Then open the page, make sure a snapshot is loaded, and try a chip like
**"No-fly zone over Chicago"** or **"Storm over Atlanta at $6 fuel."**

---

## 10. Extending it (adding a third tool)

Because tools execute in the browser, adding one is two coordinated edits:

1. **Implement** the function on `window.AIRSPACE` in `app.js` — it should run
   something against the existing engine/state and `return` a small JSON object
   (only the numbers you want narrated; round/aggregate, don't dump 200 rows).
2. **Declare** a matching JSON schema in `TOOLS` in `chat_backend.py` with the **same
   name** and a clear, prescriptive description (say *when* to call it).

`chat.js` needs no changes — it dispatches any `tool_use` block by name to
`window.AIRSPACE[name]`. Keep results bounded (e.g. top-N, and say so) so latency and
token use stay sane.

---

## 11. Limitations & gotchas

- **Estimates, not forecasts.** Same caveats as the engine: constant-cruise model,
  burn from cruise speed, acceptance rate is an assumption, single data snapshot.
- **Circle ≈ box.** `simulate_no_fly_zone` approximates the circular zone as a
  centered square so it can reuse `applyNFZ` unchanged; the reply notes the radius.
- **The data must be loaded.** The engine operates on the in-browser `state.flights`
  for the current snapshot. If `public/data/` isn't built, the map won't load and the
  tools have nothing to act on.
- **`/api/fuel` may fall back.** If `fuelfeed.py` isn't present in the bundle, the
  live fuel feed returns a fallback price and the UI uses the manual slider default;
  you can still set a price in chat ("…at $6 fuel"). Harmless to the chat.
- **Top-N flights are capped** (5) and the result says so — it's a deliberate bound,
  not the full fleet list.
