#!/usr/bin/env python3
"""Anthropic proxy for the airspace chat box.

The browser owns the tool-use loop (the engine — reroute, fleet cost, AAR/CDM —
runs in app.js on the flight data already in memory). This module is a thin,
stateless proxy: it holds the constant system prompt + tool schemas server-side
(so they cache and the API key never reaches the browser) and forwards one model
turn at a time. The browser posts the full `messages` array each turn, we run one
`messages.create`, and hand the raw response back for the browser to dispatch.

Model: claude-opus-4-8. Prompt caching: a cache_control breakpoint on the system
block caches tools + system together (tools render before system), so every turn
after the first only pays full price for the new messages.
"""
import os

MODEL = "claude-opus-4-8"
MAX_TOKENS = 2048

# §8 of the handoff, lightly expanded. The rules are what keep the bot honest
# (never invent a number) and accessible (plain language, key impact first).
SYSTEM = """You are an airspace operations assistant for a live US air-traffic map. \
A user describes a what-if scenario in plain English; you translate it into ONE tool call, \
then explain the REAL numbers the tool returns and the map redraws to match.

Rules:
1. NEVER state a number that did not come from a tool result. Every figure — flights, \
dollars, CO2, minutes — must be one the tool returned. If you have not called a tool yet, \
call one; do not guess.
2. Prefer acting with sensible defaults over asking. Defaults: no-fly-zone radius 60 nautical \
miles; today's live fuel price; all affected flights. If a request is genuinely unrunnable \
(no location at all), ask exactly ONE short question with a suggested default \
("Which city — or should I use the busiest, Atlanta?"). Never reply with only a question \
when you could have produced a useful result.
3. For places, fill center_lat and center_lon from your own geographic knowledge \
(e.g. Chicago is about 41.88, -87.63). For a specific airport, pass its 4-letter ICAO code \
to analyze_hub (e.g. Atlanta = KATL, Chicago O'Hare = KORD, Denver = KDEN, Newark = KEWR).
4. Write for a non-expert at about an 8th-grade reading level. Expand every abbreviation on \
first use — e.g. "acceptance rate (how many planes an airport can land per hour)", \
"CO2 (carbon dioxide)". Never show a raw ICAO code without the city name.
5. Follow this shape every reply (most important first):
   - One headline sentence: the single biggest impact in human terms.
   - The 2-3 numbers that matter, rounded ("about $214,000", not "$213,847.12").
   - The few hardest-hit flights by name, as plain routes \
("United 482, Newark to San Francisco, detours 140 miles, about $3,100 more").
   - One closing line stating the assumptions you made (radius, fuel price).
6. Name things in words, never rely on color: say "reroute", "no-fly zone", "diversion risk". \
7. Keep replies under about 120 words unless the user asks for more. These are what-if \
estimates, not forecasts — don't overstate precision."""

TOOLS = [
    {
        "name": "simulate_no_fly_zone",
        "description": (
            "Apply a circular no-fly / keep-out zone over a place and report its impact on the "
            "whole fleet: how many flights must reroute around it or are grounded (origin or "
            "destination inside the zone), the total extra distance, extra fuel cost in dollars, "
            "extra CO2, added delay, arrival-hold cost, and the hardest-hit individual flights. "
            "The map redraws to show the zone and the rerouted paths. Use this for any "
            "'no-fly zone', 'keep-out', 'airspace closure', or 'what happens if we block the sky "
            "over X' scenario."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "center_lat": {"type": "number", "description": "Zone center latitude (you fill this from your knowledge of the place)."},
                "center_lon": {"type": "number", "description": "Zone center longitude."},
                "place_name": {"type": "string", "description": "What the user called the area, for your summary (e.g. 'Chicago')."},
                "radius_nm": {"type": "number", "description": "Zone radius in nautical miles. Defaults to 60 if omitted."},
                "fuel_usd_per_gal": {"type": "number", "description": "Jet-fuel price to value the detours. Omit to use the live market price already loaded."},
            },
            "required": ["center_lat", "center_lon", "place_name"],
        },
    },
    {
        "name": "analyze_hub",
        "description": (
            "Estimate the congestion / weather cost of metering arrivals at one airport. Reports "
            "the holding-fuel cost when planes must wait to land, how much a cost-optimized "
            "slot-swap (CDM substitution) saves, and how many flights risk diverting. Use this "
            "for 'storm over <airport>', 'congestion at <hub>', or 'what does bad weather at X "
            "cost' scenarios. Pass the airport's 4-letter ICAO code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hub": {"type": "string", "description": "Airport ICAO code, e.g. KATL for Atlanta, KORD for Chicago O'Hare."},
                "weather_severity": {"type": "number", "description": "1.0 = normal. Higher = worse weather, which lowers the landing rate (2.0 roughly halves it). Defaults to 1.0."},
                "base_aar": {"type": "number", "description": "Fair-weather acceptance rate (arrivals per hour). Defaults to 55."},
                "fuel_usd_per_gal": {"type": "number", "description": "Jet-fuel price for pricing holds. Omit to use the live price."},
            },
            "required": ["hub"],
        },
    },
]

# cache the tools + system prefix (tools render before system, so one breakpoint
# on the system block covers both — see prompt-caching guidance)
_SYSTEM_BLOCKS = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic  # imported lazily so static serving + /api/fuel work without the SDK
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


def run_turn(messages):
    """Run one model turn. `messages` is the full conversation the browser holds.

    Returns a plain dict (the Message serialized) on success. On failure returns
    a dict with an "error" string so the browser can show it in the transcript.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"error": "Server has no ANTHROPIC_API_KEY set. Start it with "
                         "`ANTHROPIC_API_KEY=sk-... ./serve.sh` so the chat can reach Claude."}
    try:
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM_BLOCKS,
            tools=TOOLS,
            messages=messages,
        )
        return resp.to_dict()
    except Exception as e:  # surface a readable message instead of a 500
        return {"error": f"{type(e).__name__}: {e}"}
