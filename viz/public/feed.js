// NAS Live Feed — RSS-reader-like live stream. Pulls NWS weather alerts (live,
// CORS-enabled), the live jet-fuel price (/api/fuel), and a server feed proxy
// (/api/feed) every POLL_MS, merges/dedupes, and renders newest-first with
// unread badges + relative times. Dependency-free; every source fails gracefully.
(function () {
  "use strict";
  var POLL_MS = 15000;
  var NWS_URL =
    "https://api.weather.gov/alerts/active?status=actual&message_type=alert";
  var WEATHER_EVENTS =
    /storm|thunder|tornado|hurricane|wind|hail|flood|snow|ice|blizzard|winter|fog|dust|tropical/i;

  var items = new Map(); // id -> item {id,category,title,body,source,ts,link}
  var seen = new Set(); // ids seen on a previous cycle (for "new" detection)
  var read = new Set(); // ids the user has read
  var firstLoad = true;
  var paused = false;
  var filter = "all";
  var lastFuelKey = null;
  var pollTimer = null;

  var $ = function (id) {
    return document.getElementById(id);
  };

  // ---------- clock + relative time ----------
  function tickClock() {
    var d = new Date();
    $("feed-clock").textContent =
      d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
  }
  function fmtRel(ts) {
    if (!ts) return "";
    var s = Math.floor(Date.now() / 1000 - ts);
    if (s < 45) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    var d = new Date(ts * 1000);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }
  function refreshTimes() {
    var list = $("feed-list");
    list.querySelectorAll(".feed-item").forEach(function (li) {
      var it = items.get(li.dataset.id);
      if (it) li.querySelector(".feed-item-time").textContent = fmtRel(it.ts);
    });
  }

  function setStatus(ok) {
    var el = $("feed-status");
    el.classList.toggle("live", ok);
    el.classList.toggle("degraded", !ok);
    $("feed-status-txt").textContent = ok ? "live" : "degraded";
  }

  // ---------- sources (each isolated; never throws) ----------
  async function fetchWeather() {
    var r = await fetch(NWS_URL, {
      headers: { Accept: "application/geo+json" },
    });
    if (!r.ok) throw new Error("nws " + r.status);
    var j = await r.json();
    var feats = (j.features || [])
      .filter(function (f) {
        return WEATHER_EVENTS.test((f.properties && f.properties.event) || "");
      })
      .slice(0, 40);
    return feats.map(function (f) {
      var p = f.properties || {};
      return {
        id: f.id || p.id || "nws-" + (p.sent || p.event),
        category: "weather",
        title: p.event || "Weather alert",
        body: (p.headline || p.areaDesc || "").slice(0, 240),
        source: "NWS",
        ts: p.sent ? Math.floor(Date.parse(p.sent) / 1000) : nowSec(),
        link: p.id || "https://www.weather.gov",
      };
    });
  }

  async function fetchFuel() {
    var r = await fetch("/api/fuel");
    if (!r.ok) throw new Error("fuel " + r.status);
    var p = await r.json();
    var price = (+p.usd_per_gal).toFixed(2);
    $("feed-ticker").textContent =
      "⛽ Jet fuel $" +
      price +
      "/gal · " +
      (p.source || "") +
      " · " +
      (p.as_of || "") +
      "   |   " +
      items.size +
      " updates · refreshed " +
      new Date().toUTCString().slice(17, 25);
    var key = "fuel-" + (p.as_of || price);
    var out = [];
    if (key !== lastFuelKey) {
      lastFuelKey = key;
      out.push({
        id: key,
        category: "fuel",
        title: "Jet fuel $" + price + "/gal",
        body: p.source + (p.live ? " · live" : " · fallback"),
        source: "fuelfeed",
        ts: nowSec(),
        link: "",
      });
    }
    return out;
  }

  async function fetchServerFeed() {
    var r = await fetch("/api/feed");
    if (!r.ok) return []; // static server / absent endpoint — not a failure
    var j = await r.json();
    return (j.items || []).map(function (it) {
      return {
        id: String(it.id),
        category: it.category || "system",
        title: it.title || "",
        body: (it.body || "").slice(0, 280),
        source: it.source || "feed",
        ts: it.ts || nowSec(),
        link: it.link || "",
      };
    });
  }

  function nowSec() {
    return Math.floor(Date.now() / 1000);
  }

  // ---------- refresh + render ----------
  async function refresh() {
    if (paused) return;
    var results = await Promise.allSettled([
      fetchWeather(),
      fetchFuel(),
      fetchServerFeed(),
    ]);
    var ok = true;
    results.forEach(function (res) {
      if (res.status === "fulfilled") {
        res.value.forEach(function (it) {
          items.set(it.id, it);
        });
      } else {
        ok = false;
      }
    });
    setStatus(ok);
    // on first load, treat everything as already read (no badge storm on open)
    if (firstLoad) {
      items.forEach(function (_v, id) {
        read.add(id);
        seen.add(id);
      });
      firstLoad = false;
    }
    render();
    seen = new Set(items.keys());
  }

  function render() {
    var list = $("feed-list");
    var arr = Array.from(items.values()).sort(function (a, b) {
      return b.ts - a.ts;
    });
    list.innerHTML = "";
    var visible = 0;
    arr.forEach(function (it) {
      var li = document.createElement("li");
      li.className = "feed-item" + (read.has(it.id) ? " read" : "");
      li.dataset.id = it.id;
      li.dataset.cat = it.category;
      if (filter !== "all" && it.category !== filter) li.style.display = "none";
      else visible++;

      var dot = el("span", "feed-item-new");
      var main = el("div", "feed-item-main");
      var head = el("div", "feed-item-head");
      head.appendChild(txt("span", "feed-item-cat", it.category));
      head.appendChild(txt("span", "feed-item-time", fmtRel(it.ts)));
      main.appendChild(head);
      main.appendChild(txt("div", "feed-item-title", it.title));
      if (it.body) main.appendChild(txt("div", "feed-item-body", it.body));
      if (it.link) {
        var a = txt("a", "feed-item-src", it.source + " ↗");
        a.href = it.link;
        a.target = "_blank";
        a.rel = "noopener";
        main.appendChild(a);
      } else {
        main.appendChild(txt("span", "feed-item-src", it.source));
      }
      li.appendChild(dot);
      li.appendChild(main);
      // mark read once the user has seen it on screen (click anywhere on it)
      li.onclick = function () {
        if (!read.has(it.id)) {
          read.add(it.id);
          li.classList.add("read");
          updateTitle();
        }
      };
      list.appendChild(li);
    });
    $("feed-empty").hidden = visible > 0;
    updateTitle();
  }

  function el(tag, cls) {
    var e = document.createElement(tag);
    e.className = cls;
    return e;
  }
  function txt(tag, cls, text) {
    var e = el(tag, cls);
    e.textContent = text; // textContent only — XSS-safe for external feeds
    return e;
  }

  function unreadCount() {
    var n = 0;
    items.forEach(function (_v, id) {
      if (!read.has(id)) n++;
    });
    return n;
  }
  function updateTitle() {
    var n = unreadCount();
    document.title = (n ? "(" + n + ") " : "") + "NAS Live Feed";
  }

  // ---------- controls ----------
  function wireControls() {
    document.querySelectorAll(".feed-filter").forEach(function (b) {
      b.onclick = function () {
        filter = b.dataset.cat;
        document.querySelectorAll(".feed-filter").forEach(function (x) {
          x.classList.toggle("active", x === b);
        });
        render();
      };
    });
    $("feed-pause").onclick = function () {
      paused = !paused;
      $("feed-pause").textContent = paused ? "Resume" : "Pause";
      if (!paused) refresh();
    };
    $("feed-readall").onclick = function () {
      items.forEach(function (_v, id) {
        read.add(id);
      });
      document.querySelectorAll(".feed-item").forEach(function (li) {
        li.classList.add("read");
      });
      updateTitle();
    };
  }

  // ---------- boot ----------
  function init() {
    tickClock();
    setInterval(tickClock, 1000);
    setInterval(refreshTimes, 30000);
    wireControls();
    refresh();
    pollTimer = setInterval(refresh, POLL_MS);
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
