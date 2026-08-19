(function () {
  "use strict";

  const el = (id) => document.getElementById(id);
  let correctionEnabled = true;
  let renderedEventCount = 0;
  let lang = "en";
  let routeData = null;
  let map, polyline, busMarkers = {}, stopMarkers = {};

  // ---------- i18n ----------
  // Static chrome text uses data-en/data-ta attributes (same pattern as the
  // Rider View). Dynamic sentences (pair messages, event log, toasts) are
  // NOT translated from the backend's English string -- instead they're
  // rebuilt client-side from the structured fields every decision already
  // carries (trail_id, lead_id, headway_s, scheduled_headway_s, ratio,
  // action, action_value), so both languages stay in sync with the same
  // underlying numbers instead of needing a second backend-generated string.
  const T = {
    en: {
      correction: "Correction",
      statusLabel: { NORMAL: "NORMAL", EARLY_WARNING: "EARLY WARNING", BUNCHING_RISK: "BUNCHING RISK", LAGGING: "LAGGING" },
      mlBunch: (m) => `ML: bunch in ~${m}m`,
      noPairs: "No pairs yet — waiting for data…",
      noEvents: "No corrections issued yet.",
      hold: (trail, s, headway, sched, pct, lead) =>
        `HOLD ${trail} +${s}s at next stop — headway ${headway}s vs scheduled ${sched}s (${pct}% of schedule), closing on ${lead}.`,
      speed: (trail, headway, sched, pct, lead, dur) =>
        `ADVISE ${trail} to speed up for ~${dur}s (minimize dwell / make up time) — headway ${headway}s vs scheduled ${sched}s (${pct}% of schedule), falling behind ${lead}.`,
      normal: (trail, lead) => `${trail} back to normal headway behind ${lead}.`,
      normalRange: "Headway within normal range.",
      toastHoldTitle: "SLOW DOWN / HOLD recommended",
      toastHoldBody: (trail, s) => `Bus ${trail}: wait an extra ${s}s at its next stop.`,
      toastSpeedTitle: "SPEED UP recommended",
      toastSpeedBody: (trail, dur) => `Bus ${trail}: speed up for ~${dur}s — falling behind the bus ahead.`,
    },
    ta: {
      correction: "திருத்தம்",
      statusLabel: { NORMAL: "இயல்பு", EARLY_WARNING: "முன் எச்சரிக்கை", BUNCHING_RISK: "கூட்டு அபாயம்", LAGGING: "பின்தங்கியுள்ளது" },
      mlBunch: (m) => `ML: ~${m} நிமிடத்தில் கூடும்`,
      noPairs: "இன்னும் தரவு இல்லை — காத்திருக்கவும்…",
      noEvents: "இதுவரை திருத்தங்கள் எதுவும் வழங்கப்படவில்லை.",
      hold: (trail, s, headway, sched, pct, lead) =>
        `${trail} பேருந்து அடுத்த நிறுத்தத்தில் +${s} வினாடி காத்திருக்க வேண்டும் — இடைவெளி ${headway}வி, திட்டம் ${sched}வி (${pct}%), ${lead} பேருந்தை நெருங்குகிறது.`,
      speed: (trail, headway, sched, pct, lead, dur) =>
        `${trail} பேருந்து ${dur}வி நேரம் வேகமெடுக்க வேண்டும் (நிறுத்த நேரத்தைக் குறைக்கவும்) — இடைவெளி ${headway}வி, திட்டம் ${sched}வி (${pct}%), ${lead} பேருந்திடமிருந்து பின்தங்குகிறது.`,
      normal: (trail, lead) => `${trail} பேருந்து ${lead} பேருந்தின் பின்னால் இயல்பு இடைவெளிக்குத் திரும்பியுள்ளது.`,
      normalRange: "இடைவெளி இயல்பான வரம்பில் உள்ளது.",
      toastHoldTitle: "மெதுவாக்க / காத்திருக்க பரிந்துரை",
      toastHoldBody: (trail, s) => `பேருந்து ${trail}: அடுத்த நிறுத்தத்தில் கூடுதலாக ${s}வி காத்திருக்க வேண்டும்.`,
      toastSpeedTitle: "வேகமெடுக்க பரிந்துரை",
      toastSpeedBody: (trail, dur) => `பேருந்து ${trail}: ${dur}வி நேரம் வேகமெடுக்க வேண்டும் — முன் பேருந்திடமிருந்து பின்தங்குகிறது.`,
    },
  };
  const t = () => T[lang];

  function applyStaticLang() {
    document.querySelectorAll("[data-en]").forEach((node) => {
      const text = lang === "ta" ? node.getAttribute("data-ta") : node.getAttribute("data-en");
      if (text) node.textContent = text;
    });
    document.querySelectorAll(".lang-opt").forEach((n) => {
      n.classList.toggle("active", n.getAttribute("data-lang") === lang);
    });
    el("correctionLabel").textContent = `${t().correction}: ${correctionEnabled ? "ON" : "OFF"}`;
  }

  el("langToggle").addEventListener("click", () => {
    lang = lang === "en" ? "ta" : "en";
    document.body.classList.toggle("lang-ta", lang === "ta");
    applyStaticLang();
    if (window.__lastOp) renderAll(window.__lastState);
    if (routeData) refreshStopTooltips();
  });

  function fmtSec(s) {
    if (s == null) return "—";
    return `${Math.round(s)}s`;
  }

  function renderKpis(op) {
    el("kpiAlerts").textContent = op.stats.active_alerts;
    el("kpiHolds").textContent = op.stats.holds_issued_total;
    el("kpiSpeedups").textContent = op.stats.speedups_issued_total;
    el("kpiForecast").textContent = op.stats.forecast_watch;
  }

  // Rebuilds a pair's recommended-action sentence in the current language
  // from its structured fields (mirrors control.py's message templates).
  function pairMessage(p) {
    const pct = Math.round(p.ratio * 100);
    if (p.action === "HOLD") {
      return t().hold(p.trail_id, Math.round(p.action_value), Math.round(p.headway_s), Math.round(p.scheduled_headway_s), pct, p.lead_id);
    }
    if (p.action === "SPEED_UP") {
      return t().speed(p.trail_id, Math.round(p.headway_s), Math.round(p.scheduled_headway_s), pct, p.lead_id, Math.round(p.action_duration_s));
    }
    return t().normalRange;
  }

  function renderPairs(op) {
    const body = el("pairsBody");
    body.innerHTML = "";
    if (!op.pairs.length) {
      body.innerHTML = `<div class="pair-row"><span class="action-text">${t().noPairs}</span></div>`;
      return;
    }
    op.pairs.forEach((p) => {
      const row = document.createElement("div");
      row.className = "pair-row";
      const forecastTag = p.forecast_alert
        ? `<span class="pair-forecast">${t().mlBunch(Math.round(p.predicted_bunch_s / 60))}</span>`
        : "";
      const acted = p.action !== "NONE";
      const statusLabel = t().statusLabel[p.status] || p.status;
      row.innerHTML = `
        <span class="pair-name">${p.trail_id} → ${p.lead_id}${forecastTag}</span>
        <span>${fmtSec(p.headway_s)}</span>
        <span>${Math.round(p.ratio * 100)}%</span>
        <span><span class="status-badge ${p.status}">${statusLabel}</span></span>
        <span class="action-text ${acted ? "acted" : ""}">${pairMessage(p)}</span>
      `;
      body.appendChild(row);
    });
  }

  function eventMessage(e) {
    // New-style events carry structured fields; fall back to the raw
    // English message for anything logged before this field existed.
    if (e.trail_id == null) return e.message;
    if (e.level === "NORMAL") return t().normal(e.trail_id, e.lead_id);
    const pct = Math.round(e.ratio * 100);
    if (e.action === "HOLD") {
      return t().hold(e.trail_id, Math.round(e.action_value), Math.round(e.headway_s), Math.round(e.scheduled_headway_s), pct, e.lead_id);
    }
    if (e.action === "SPEED_UP") {
      return t().speed(e.trail_id, Math.round(e.headway_s), Math.round(e.scheduled_headway_s), pct, e.lead_id, Math.round(e.action_duration_s));
    }
    return e.message;
  }

  function renderEvents(op) {
    const list = el("eventsList");
    const events = op.events;
    if (!events.length) {
      list.innerHTML = `<div class="events-empty">${t().noEvents}</div>`;
      renderedEventCount = 0;
      return;
    }
    // The events list is capped and trimmed from the front on the backend,
    // so its length can stay flat even as new events replace old ones --
    // compare the newest event's id, not the array length, to decide
    // whether a re-render is needed.
    const newestId = events[events.length - 1].id ?? events.length;
    if (newestId === renderedEventCount) return;
    renderedEventCount = newestId;
    list.innerHTML = "";
    events.slice().reverse().forEach((e) => {
      const row = document.createElement("div");
      row.className = "event-row";
      const time = new Date(e.t * 1000).toLocaleTimeString();
      const statusLabel = t().statusLabel[e.level] || e.level;
      row.innerHTML = `
        <span class="event-tag ${e.level}">${statusLabel}</span>
        <span class="event-msg">${eventMessage(e)}</span>
        <span style="margin-left:auto;color:var(--slate);font-size:11px;">${time}</span>
      `;
      list.appendChild(row);
    });
  }

  // ---------- Toast notifications ----------
  let lastSeenEventId = null;
  function maybeToast(op) {
    const events = op.events;
    if (!events.length) return;
    if (lastSeenEventId === null) {
      // First load: don't flood with toasts for pre-existing history,
      // just set the baseline to the newest event already on record.
      lastSeenEventId = events[events.length - 1].id ?? events.length;
      return;
    }
    const fresh = events.filter((e) => (e.id ?? 0) > lastSeenEventId);
    fresh.forEach((e) => {
      if (e.action === "HOLD" || e.action === "SPEED_UP") showToast(e);
    });
    if (fresh.length) {
      lastSeenEventId = events[events.length - 1].id ?? lastSeenEventId;
    }
  }

  function showToast(e) {
    const stack = el("toastStack");
    const div = document.createElement("div");
    div.className = `toast ${e.level}`;
    const title = e.action === "HOLD" ? t().toastHoldTitle : t().toastSpeedTitle;
    const body = e.action === "HOLD"
      ? t().toastHoldBody(e.trail_id, Math.round(e.action_value))
      : t().toastSpeedBody(e.trail_id, Math.round(e.action_duration_s));
    div.innerHTML = `<div class="toast-title">${title}</div><div>${body}</div>`;
    stack.appendChild(div);
    setTimeout(() => {
      div.classList.add("toast-fade");
      setTimeout(() => div.remove(), 450);
    }, 6000);
  }

  // ---------- Fleet map (numbered buses) ----------
  function initMap() {
    map = L.map("fleetMap", { zoomControl: true, attributionControl: false }).setView([11.005, 76.966], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
  }

  function drawRouteAndStops() {
    const latlngs = routeData.polyline.map((p) => [p[0], p[1]]);
    polyline = L.polyline(latlngs, { color: "#0B1F3A", weight: 4, opacity: 0.55 }).addTo(map);
    map.fitBounds(polyline.getBounds(), { padding: [24, 24] });

    routeData.stops.forEach((stop, idx) => {
      const html = `<div class="stop-marker"><span class="stop-marker-num">${idx + 1}</span></div>`;
      const icon = L.divIcon({ className: "", html, iconSize: [22, 22], iconAnchor: [11, 11] });
      const m = L.marker([stop.lat, stop.lon], { icon, keyboard: false, zIndexOffset: 400 });
      m.bindTooltip(stopTooltip(stop, idx), { permanent: true, direction: "top", offset: [0, -12], className: "stop-label" });
      m.addTo(map);
      stopMarkers[stop.id] = m;
    });
  }

  function stopTooltip(stop, idx) {
    const name = lang === "ta" ? stop.name_ta : stop.name_en;
    return `${idx + 1}. ${name}`;
  }

  function refreshStopTooltips() {
    routeData.stops.forEach((stop, idx) => {
      const m = stopMarkers[stop.id];
      if (m) m.setTooltipContent(stopTooltip(stop, idx));
    });
  }

  // pairsByTrail: trail_id -> pair status, so each bus marker is colored by
  // its own headway status (the thing the control room cares about) rather
  // than by occupancy, which is the Rider View's concern.
  function updateFleetMap(buses, op) {
    if (!map) return;
    const pairsByTrail = {};
    (op.pairs || []).forEach((p) => { pairsByTrail[p.trail_id] = p.status; });

    buses.forEach((b) => {
      const status = pairsByTrail[b.bus_id] || "NORMAL";
      const shortId = b.bus_id.split("-").pop(); // e.g. "4200"
      const html = `<div class="bus-marker-labeled ${status}" style="width:30px;height:30px;">${shortId}</div>`;
      const icon = L.divIcon({ className: "", html, iconSize: [30, 30] });
      if (busMarkers[b.bus_id]) {
        busMarkers[b.bus_id].setLatLng([b.lat, b.lon]);
        busMarkers[b.bus_id].setIcon(icon);
      } else {
        const m = L.marker([b.lat, b.lon], { icon, keyboard: false, zIndexOffset: 600 }).addTo(map);
        busMarkers[b.bus_id] = m;
      }
      const label = lang === "ta" ? "பேருந்து" : "Bus";
      const statusLabel = t().statusLabel[status] || status;
      busMarkers[b.bus_id].bindTooltip(`${label} ${b.bus_id} — ${statusLabel}`, { direction: "top" });
    });
  }

  function renderAll(state) {
    const op = state.operator;
    if (!op) return;
    window.__lastOp = op;
    window.__lastState = state;
    correctionEnabled = op.correction_enabled;
    const btn = el("correctionToggle");
    btn.setAttribute("aria-pressed", String(correctionEnabled));
    el("correctionLabel").textContent = `${t().correction}: ${correctionEnabled ? "ON" : "OFF"}`;
    renderKpis(op);
    renderPairs(op);
    maybeToast(op);
    renderEvents(op);
    updateFleetMap(state.buses, op);
  }

  el("correctionToggle").addEventListener("click", async () => {
    const next = !correctionEnabled;
    try {
      const res = await fetch("/api/correction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      const data = await res.json();
      correctionEnabled = data.correction_enabled;
      const btn = el("correctionToggle");
      btn.setAttribute("aria-pressed", String(correctionEnabled));
      el("correctionLabel").textContent = `${t().correction}: ${correctionEnabled ? "ON" : "OFF"}`;
    } catch (e) { /* leave UI as-is, next WS push will resync */ }
  });

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "state") renderAll(msg.data);
      } catch (e) { /* ignore malformed frame */ }
    };
    ws.onclose = () => setTimeout(connectWS, 2000);
    ws.onerror = () => ws.close();
  }

  async function boot() {
    applyStaticLang();
    initMap();
    try {
      const routeRes = await fetch("/api/route");
      routeData = await routeRes.json();
      drawRouteAndStops();
    } catch (e) { /* map still usable without route overlay */ }

    try {
      const res = await fetch("/api/state");
      const state = await res.json();
      renderAll(state);
    } catch (e) { /* fall through to WS */ }
    connectWS();
  }

  boot();
})();
