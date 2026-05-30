// Accessible chat box that drives the in-browser airspace engine.
//
// The server (POST /api/chat) is a stateless Claude proxy — it runs ONE model
// turn per request. This file owns the tool-use loop: when the model returns a
// tool_use block, we execute it against window.AIRSPACE (the real engine in
// app.js — applyNFZ / meterArrivals), which also redraws the map, then feed the
// result back and ask the model to narrate. Every number it speaks came from
// the engine, never from the model's imagination.
(function () {
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const status = document.getElementById("chat-status");
  const chips = document.getElementById("chat-chips");
  const collapse = document.getElementById("chat-collapse");
  const body = document.getElementById("chat-body");

  const reduceMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  // full conversation in Anthropic message shape; the server is stateless so we
  // resend it every turn.
  const messages = [];
  let busy = false;

  function bubble(role, text, cls) {
    const el = document.createElement("div");
    el.className = "chat-msg " + (cls || role);
    el.textContent = text;
    log.appendChild(el);
    log.scrollTo({
      top: log.scrollHeight,
      behavior: reduceMotion ? "auto" : "smooth",
    });
    return el;
  }

  function setBusy(on, note) {
    busy = on;
    sendBtn.disabled = on;
    input.disabled = on;
    status.textContent = on ? note || "Working…" : "";
  }

  // friendly label for the "ran a scenario" note (transparency, not a number)
  const TOOL_LABEL = {
    simulate_no_fly_zone: "Placed a no-fly zone on the map and rerouted the fleet…",
    analyze_hub: "Metered arrivals at the airport…",
  };

  function runTool(name, args) {
    const api = window.AIRSPACE;
    if (!api || typeof api[name] !== "function") {
      return { error: `unknown tool ${name}` };
    }
    try {
      return api[name](args || {});
    } catch (e) {
      return { error: String((e && e.message) || e) };
    }
  }

  async function turn() {
    // loop until the model stops asking for tools
    for (let guard = 0; guard < 6; guard++) {
      let data;
      try {
        const r = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages }),
        });
        data = await r.json();
      } catch (e) {
        bubble("system", "Could not reach the server. Is serve.py running?", "error");
        return;
      }
      if (data.error) {
        bubble("system", data.error, "error");
        return;
      }

      // record the assistant turn verbatim (text + any tool_use blocks)
      messages.push({ role: "assistant", content: data.content });

      const text = (data.content || [])
        .filter((b) => b.type === "text")
        .map((b) => b.text)
        .join("\n")
        .trim();
      if (text) bubble("assistant", text);

      const toolUses = (data.content || []).filter((b) => b.type === "tool_use");
      if (data.stop_reason !== "tool_use" || toolUses.length === 0) return;

      // execute each requested tool against the real engine, redraw the map,
      // and feed results back for the model to narrate
      const results = [];
      for (const t of toolUses) {
        setBusy(true, TOOL_LABEL[t.name] || "Running the scenario…");
        const out = runTool(t.name, t.input);
        results.push({
          type: "tool_result",
          tool_use_id: t.id,
          content: JSON.stringify(out),
        });
      }
      messages.push({ role: "user", content: results });
      setBusy(true, "Summarizing the results…");
    }
    bubble("system", "Stopped after several steps to avoid a loop.", "error");
  }

  async function ask(textRaw) {
    const text = (textRaw || "").trim();
    if (!text || busy) return;
    bubble("user", text);
    messages.push({ role: "user", content: text });
    input.value = "";
    setBusy(true);
    try {
      await turn();
    } finally {
      setBusy(false);
      input.focus(); // return focus to the input after a reply
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    ask(input.value);
  });

  // Enter sends; Shift+Enter inserts a newline
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask(input.value);
    }
  });

  chips.addEventListener("click", (e) => {
    const b = e.target.closest("button.chip");
    if (b) ask(b.textContent);
  });

  collapse.addEventListener("click", () => {
    const open = collapse.getAttribute("aria-expanded") === "true";
    collapse.setAttribute("aria-expanded", String(!open));
    collapse.textContent = open ? "+" : "–";
    body.hidden = open;
  });
})();
