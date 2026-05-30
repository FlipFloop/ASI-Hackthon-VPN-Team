# Hold-Cost Engine — A Plain-English Guide

*A walkthrough of every feature, written for someone with no aviation background.*

---

## 1. The one-sentence idea

> When too many planes want to land at the same airport at the same time, some of them have to wait. They can wait **in the air** (expensive — circling burns fuel and money) or **on the ground** (cheap — engines basically off). Today the waiting is handed out *fairly* but **blindly to cost**. This app re-orders *who waits* to save real dollars and CO₂ — and shows you exactly how much.

That's it. Everything below is detail on top of that idea.

---

## 2. The story, with an everyday analogy

Imagine a **popular restaurant with a fixed number of tables**. At 7pm, far more people show up than there are tables. Someone has to wait.

- **The tables** = the airport's ability to land planes. There's a fixed rate — only so many planes can touch down per hour.
- **The crowd at 7pm** = a rush of flights all scheduled to arrive around the same time.
- **Waiting in the air** = a customer who circles the block in their car with the engine running until a table frees up. Burns gas the whole time.
- **Waiting on the ground** = a customer who just stays home and leaves later. Costs nothing.

Now here's the twist that makes this a *money* problem:

- A **circling jumbo jet** burns fuel like a semi-truck idling — maybe **$70 a minute**.
- A **small regional plane** circling burns far less — maybe **$20 a minute**.
- A plane **still parked at its origin gate** burns **almost nothing**.

So the question *"who should wait, and where?"* is really a **money question**. The official system doesn't treat it that way — and that gap is what this tool exploits.

---

## 3. How the official system works today (and why it leaves money on the table)

When an airport gets overwhelmed, the FAA runs something called a **Ground Delay Program**. Think of it as **issuing numbered tickets** (called "slots") for landing — like a deli counter. The rule for who gets which ticket is called **Ration-by-Schedule**: *whoever was scheduled to arrive earliest gets the earliest ticket.*

This rule is **fair** — nobody can jump the line by being richer or pushier. But it is **completely blind to cost**. It will happily make an expensive jumbo jet circle while a cheap-to-delay plane lands ahead of it, because it only looks at the *schedule*, never the *fuel bill*.

**That blindness is the opportunity.** You can keep the system just as fair *within each airline* and still save a fortune by being smart about *which of your own planes* you make wait.

---

## 4. The four "policies" — the heart of the app

The app compares **four different ways** to hand out the same waiting time to the same planes. They all produce the *same total delay* — they just distribute it differently. You see them as four colored bars labeled **"Cost by allocation policy."**

| Bar | Plain meaning | Real-world status |
|---|---|---|
| **All-air** (orange) | Everyone who's delayed circles in the air. The worst, most wasteful case. | This is *why* delay programs were invented — to stop this. |
| **RBS** (yellow) | The fair, by-the-schedule method the FAA uses **today**. Planes still at the gate wait on the ground; planes already flying circle. | What actually happens now. |
| **CDM substitution** (green) ⭐ | An airline **re-shuffles its own planes within its own tickets** to make the cheap ones wait and let the expensive ones land first. **Allowed under today's rules.** | The deployable product — this is the headline. |
| **System-optimal** (gray) | The theoretical best if *all airlines pooled together* and the whole system optimized for cost. | A "what's the ceiling?" number — **not legal today** (airlines won't give up their tickets to rivals). |

The bars are sized by dollar cost, so you can *see* the savings: each greener bar is shorter (cheaper) than the one above it.

**Why "CDM substitution" is the star:** "CDM" (Collaborative Decision Making) is the real program that lets an airline swap its *own* flights between the landing tickets it already holds. So cost-optimizing that swap is something an airline could literally do tomorrow — no rule changes needed. That's what makes it a credible product rather than a fantasy.

---

## 5. The big green headline (top-right)

This is the punchline of the whole tool:

> **$10,494**
> *+ 27.5 t CO₂ avoided · the whole arrival bank · KATL GDP, FAA-legal today*
> *vs. "everyone loiters in the air": $59,321 · cross-airline ceiling (not FAA-legal): $14,713 · 213 flights shown*

Reading it:
- **The big number** = money saved by smart substitution (green bar) compared to today's fair-but-blind method (yellow bar), for this one congestion event.
- **CO₂ avoided** = the same saving expressed as climate impact (burning less fuel = less carbon). Measured in metric tons.
- **"vs everyone loiters"** = the dramatic number — how much you save versus the worst case where everyone circles.
- **"cross-airline ceiling"** = the absolute most you could theoretically save if the rules let all airlines cooperate. Your realistic saving sits *below* this ceiling.

---

## 6. The controls ("Live levers")

Three sliders let you explore "what if?" in real time.

### Fuel price slider
The price of jet fuel, in dollars per gallon. **Slide it up and the savings grow; slide it down and they shrink.** This is the app's signature point: *the right decision changes with the fuel market.* When fuel is cheap, circling isn't such a big deal; when fuel is expensive, every minute of circling hurts, so smart allocation matters more. (The current systems ignore fuel price entirely.)

### Acceptance rate slider
How many planes the airport can land **per hour**. This is the size of the bottleneck.
- **Lower it** (bad weather, fewer open runways) → more planes must wait → bigger savings, and **diversion risks start appearing** (more on that below).
- **Raise it** (clear skies, all runways open) → less waiting → smaller problem.

*(Note: real airports publish this number; our dataset didn't include it, so it's an adjustable assumption. That's standard practice for this kind of analysis.)*

### Weather severity slider
A multiplier on how badly storms hurt the airport. When a storm sits over the field, the airport can't land as many planes, so its acceptance rate drops. This slider lets you dial that effect from "ignore weather" (0) up to "storms hit twice as hard" (×2). This is how the **real weather data** in the project feeds into the cost.

### "use live price" link
Resets the fuel slider back to the **current real market price** (see the ticker, next).

---

## 7. The live fuel ticker (top-right of the header)

A small chip showing something like **"Jet fuel $3.67/gal · ULSD (NY Harbor) … "** with a **pulsing green dot**.

- The **price** is pulled live from the commodities market — no fake numbers.
- **Why "ULSD / heating oil"?** Jet fuel itself doesn't have a convenient free live price, but it's chemically almost identical to heating-oil/diesel futures, which airlines actually use to hedge their fuel costs. So we track that and add a small adjustment. It's the standard proxy the industry uses.
- The **green pulsing dot** means the feed is live. If the internet is unavailable, it turns gray and falls back to a sensible default price.

---

## 8. The map (left side)

A dark map of the United States showing the live situation for the selected airport.

- **The green dot with a ring** = the destination airport (the "hub") everyone is flying toward. The ring is just a visual marker around it.
- **The colored dots** = individual planes currently *in the air* heading to that airport. Their color tells you what the smart plan does with them:
  - 🟢 **Green = on-time** — lands as planned, no waiting.
  - 🟠 **Orange = air-hold** — has to circle and burn fuel/money.
  - 🔴 **Red = diversion risk** — would have to circle so long it might run low on fuel and be forced to land somewhere else entirely (the worst outcome — see below).
  - 🔵 **Blue** in the legend = gate-hold — but these planes are still parked at their origin, so they don't show on the map; they're in the table instead.
- **Reddish translucent squares** = storms (areas of heavy rain/thunderstorms). Where these sit near the airport, they choke its landing rate. This is the actual weather radar data from the project.

Hovering over any plane shows its flight number, airline, where it's coming from, its fuel burn, and what the plan does with it.

---

## 9. The flight board (bottom table)

Every plane competing for landing slots, one per row. You can **click any column header to sort.** The columns:

| Column | What it means in plain English |
|---|---|
| **Flight** | The flight's ID code. |
| **Airline** | Which airline (e.g. DAL = Delta, SWA = Southwest). |
| **From** | The airport it's coming from. |
| **Status** | "airborne" = already flying (can only wait by circling). "gate" = still parked (can wait for free on the ground). |
| **Cruise kt** | Its cruising speed. We use this as a stand-in for plane *size* — faster usually means bigger. |
| **Burn kg/hr** | How much fuel it burns per hour while waiting. Bigger planes = bigger number = more expensive to make wait. |
| **ETA min** | Minutes until it was *scheduled* to arrive. |
| **RBS delay** | How long it waits under today's fair-but-blind method. |
| **Substituted** | How long it waits under the smart cost-optimized plan. |
| **Absorbs** | *Where* it does its waiting: **on-time** (no wait), **gate** (free, on the ground), or **air** (circling, costs money). |

The key thing to notice: smart substitution pushes the waiting onto the **gate** (free) and onto **cheap, small planes**, while letting **expensive big jets** land on time.

---

## 10. The stats boxes (under the bars)

Four quick numbers about the current situation:

- **Acceptance rate /hr** — how many planes the airport is landing per hour right now (lower when storms are cutting into it).
- **Diversion-risk flights (RBS → substitution)** — e.g. "27 → 4". The first number is how many planes would be at risk of running low on fuel under today's method; the second is after the smart plan. **Lower is safer** — this is the safety win, not just the money win.
- **Delay moved to the gate** — total minutes of waiting that the smart plan shifts from the expensive air to the free ground.
- **Flights** — how many planes are in the current view (changes when you filter by airline).

---

## 11. The two dropdowns (header)

### Hub
Pick which busy airport to analyze (Atlanta, Chicago, Dallas, Denver, etc.). Some are picked because they have storms nearby (good for showing the weather effect); others are clear (good for showing pure overcrowding).

### Airline
Filter the whole view down to a **single airline**. This matters because of how the rules work: an airline can only re-shuffle *its own* planes. So:
- **"All airlines"** shows the system-wide picture.
- Picking, say, **Delta** shows just Delta's planes and just Delta's savings.
- You'll notice an airline saves the most **at its own hub airport**, because that's where it has the most flights (and therefore the most flexibility to re-shuffle). At Atlanta, Delta alone captures most of the total savings.

---

## 12. Key terms, defined simply

- **Hub** — a big airport an airline uses as a major connecting point (Atlanta for Delta, etc.).
- **Acceptance rate (AAR)** — the number of planes an airport can land per hour. The bottleneck.
- **Ground Delay Program (GDP)** — the official "issue numbered landing tickets" process used when an airport is overwhelmed.
- **Ration-by-Schedule (RBS)** — the rule for handing out those tickets: earliest-scheduled gets the earliest ticket. Fair, but ignores cost.
- **Slot / ticket** — a reserved time to land.
- **CDM substitution** — an airline legally swapping its *own* flights between the tickets it holds. The basis of the smart plan here.
- **Gate-hold** — making a plane wait on the ground before it takes off. Cheap (engines off).
- **Air-hold / loiter / circle** — making a plane wait by flying in circles near the airport. Expensive (burning fuel) and, if extreme, unsafe.
- **Burn rate** — how fast a plane uses fuel. Bigger plane = higher burn = more expensive to delay.
- **Diversion** — when a circling plane runs so low on fuel it must abandon its destination and land elsewhere. The worst, most disruptive (and unsafe) outcome — and the strongest reason to prefer ground waiting.

---

## 13. Why this is genuinely new (the "so what?")

Existing air-traffic optimization software is excellent at one thing: **moving the most planes through, minimizing total delay *minutes*.** It treats every minute of delay as interchangeable.

But a minute of delay is **not** interchangeable in dollars or carbon:
- A minute of a jumbo jet circling ≠ a minute of a small plane circling ≠ a minute of a plane sitting at the gate.

This tool adds the missing layer: it prices every option in **live fuel dollars and CO₂**, and re-allocates the unavoidable waiting to the cheapest, safest place — **without breaking the fairness rules.** It's a money-and-climate lens on a problem everyone else looks at purely through a stopwatch.

Three things make it defensible:
1. **It moves with the market** — the recommendation changes with live fuel prices. Nobody else's does.
2. **It's deployable today** — it works within existing FAA rules (CDM substitution), no permission needed.
3. **It's safer, not just cheaper** — it reduces the risk of fuel-emergency diversions.

---

## 14. Honest limitations (worth knowing before you present)

- The dataset doesn't include real airport landing rates, so that number is an **adjustable assumption** (standard for this kind of study).
- The dataset doesn't say what *type* each aircraft is, so we **estimate fuel burn from cruise speed** as a stand-in for size.
- The flight data is a single snapshot with no real holding recorded, so this is a **decision-support / "what-if" tool, not a predictor** — it tells you the cheapest way to absorb a given congestion event, it doesn't claim to forecast the future.
- Fuel price is live; everything else is computed from the provided flight + weather data.
