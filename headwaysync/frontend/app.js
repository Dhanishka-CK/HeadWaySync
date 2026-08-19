(function () {
  "use strict";

  const API_BASE = ""; // same origin
  let lang = "en";
  let selectedStopId = null;
  let routeData = null;
  let map, polyline, busMarkers = {}, stopMarkers = {};
  let directionArrows = [];
  let routeIsClockwise = true;
  let lastStatus = null;

  const el = (id) => document.getElementById(id);

  // ---------- i18n for static chrome text ----------
  function applyStaticLang() {
    document.querySelectorAll("[data-en]").forEach((node) => {
      const text = lang === "ta" ? node.getAttribute("data-ta") : node.getAttribute("data-en");
      if (text) node.textContent = text;
    });
    document.querySelectorAll(".lang-opt").forEach((n) => {
      n.classList.toggle("active", n.getAttribute("data-lang") === lang);
    });
  }

  el("langToggle").addEventListener("click", () => {
    lang = lang === "en" ? "ta" : "en";
    document.body.classList.toggle("lang-ta", lang === "ta");
    applyStaticLang();
    if (routeData) {
      populateStopSelect();
      refreshStopLabels();
      setDirectionNote();
    }
    // re-render dynamic content in new language on next tick
  });

  function refreshStopLabels() {
    routeData.stops.forEach((stop, idx) => {
      const m = stopMarkers[stop.id];
      if (!m) return;
      const name = lang === "ta" ? stop.name_ta : stop.name_en;
      m.setTooltipContent(`${idx + 1}. ${name}`);
    });
  }

  // ---------- Icons for the signal board ----------
  const ICONS = {
    hand: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 11V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0"/><path d="M14 10V4a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v2"/><path d="M10 10.5V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-1-6-3l-3.3-4.3a2 2 0 1 1 3-2.6L8 14"/></svg>',
    clock: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
  };

  // ---------- Map setup ----------
  function initMap() {
    map = L.map("map", { zoomControl: true, attributionControl: false }).setView([11.005, 76.966], 14);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
    }).addTo(map);
  }

  function drawRouteAndStops() {
    const latlngs = routeData.polyline.map((p) => [p[0], p[1]]);
    polyline = L.polyline(latlngs, { color: "#0B1F3A", weight: 4, opacity: 0.55 }).addTo(map);
    map.fitBounds(polyline.getBounds(), { padding: [24, 24] });

    routeIsClockwise = computeLoopOrientation(latlngs);
    addDirectionArrows(latlngs);
    setDirectionNote();

    routeData.stops.forEach((stop, idx) => {
      const html = `<div class="stop-marker"><span class="stop-marker-num">${idx + 1}</span></div>`;
      const icon = L.divIcon({ className: "", html, iconSize: [22, 22], iconAnchor: [11, 11] });
      const m = L.marker([stop.lat, stop.lon], { icon, keyboard: false, zIndexOffset: 500 });
      const name = lang === "ta" ? stop.name_ta : stop.name_en;
      // Permanent label so stop names are always visible on the map, not
      // just on hover -- plus a fuller tooltip on hover/tap for detail.
      m.bindTooltip(`${idx + 1}. ${name}`, {
        permanent: true,
        direction: "top",
        offset: [0, -12],
        className: "stop-label",
      });
      m.addTo(map);
      stopMarkers[stop.id] = m;
    });
  }

  // ---------- Route direction (loop vs back-and-forth) ----------
  // The route is a single continuous loop: distance-along-route (dist_m)
  // only ever increases and wraps back to 0 at the terminus, so every bus
  // is always moving the SAME way around the loop -- never "backwards".
  // This is easy to misread on a plain polyline, so we (a) figure out
  // whether that loop reads as clockwise or counter-clockwise from the
  // actual stop/shape coordinates, and (b) draw small arrow markers along
  // the road so the direction is visible on the map itself, not just
  // stated in text.

  function bearingDeg(lat1, lon1, lat2, lon2) {
    const toRad = (d) => (d * Math.PI) / 180;
    const toDeg = (r) => (r * 180) / Math.PI;
    const y = Math.sin(toRad(lon2 - lon1)) * Math.cos(toRad(lat2));
    const x =
      Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
      Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lon2 - lon1));
    return (toDeg(Math.atan2(y, x)) + 360) % 360;
  }

  function computeLoopOrientation(latlngs) {
    // Signed-area (shoelace) test on (lon, lat) as a flat x/y plane.
    // Negative -> clockwise when viewed on a normal north-up map,
    // positive -> counter-clockwise.
    let area = 0;
    for (let i = 0; i < latlngs.length - 1; i++) {
      const [y1, x1] = latlngs[i];
      const [y2, x2] = latlngs[i + 1];
      area += x1 * y2 - x2 * y1;
    }
    return area <= 0;
  }

  function addDirectionArrows(latlngs) {
    directionArrows.forEach((m) => map.removeLayer(m));
    directionArrows = [];
    if (latlngs.length < 2) return;

    // Spread ~10 arrows evenly along the loop so direction is readable
    // no matter where on the map the rider is looking.
    const arrowCount = Math.min(10, latlngs.length - 1);
    const step = Math.max(1, Math.floor((latlngs.length - 1) / arrowCount));
    for (let i = 0; i < latlngs.length - 1; i += step) {
      const [lat1, lon1] = latlngs[i];
      const [lat2, lon2] = latlngs[i + 1];
      const brng = bearingDeg(lat1, lon1, lat2, lon2);
      const html = `<div class="direction-arrow" style="transform: rotate(${brng}deg);">&#10148;</div>`;
      const icon = L.divIcon({ className: "", html, iconSize: [18, 18], iconAnchor: [9, 9] });
      const m = L.marker([lat1, lon1], { icon, keyboard: false, interactive: false, zIndexOffset: 200 });
      m.addTo(map);
      directionArrows.push(m);
    }
  }

  function setDirectionNote() {
    const el_ = el("routeDirectionNote");
    if (!el_ || !routeData || !routeData.stops || !routeData.stops.length) return;
    const first = lang === "ta" ? routeData.stops[0].name_ta : routeData.stops[0].name_en;
    const last = routeData.stops[routeData.stops.length - 1];
    const lastName = lang === "ta" ? last.name_ta : last.name_en;
    const way = routeIsClockwise
      ? (lang === "ta" ? "வலஞ்சுழி (clockwise)" : "clockwise")
      : (lang === "ta" ? "இடஞ்சுழி (counter-clockwise)" : "counter-clockwise");
    el_.textContent =
      lang === "ta"
        ? `இது ஒரு சுழல் (loop) வழி — பேருந்துகள் அனைத்தும் ஒரே திசையில் (${way}) ${first} இலிருந்து ${lastName} வழியாக மீண்டும் ${first} க்குச் செல்கின்றன. முன்னும் பின்னுமாக செல்வதில்லை.`
        : `This is a loop route — every bus travels the same way around it (${way}), from ${first} through to ${lastName} and back to ${first}. Buses never run backwards along the route.`;
  }

  function tierLabel(tier) {
    const map = {
      low: { en: "Seats available", ta: "இருக்கை உள்ளது" },
      moderate: { en: "Standing room", ta: "நின்று செல்லலாம்" },
      high: { en: "Crowded", ta: "நெரிசல்" },
    };
    return map[tier] ? map[tier][lang] : "";
  }

  function updateBuses(buses) {
    buses.forEach((b) => {
      const html = `<div class="bus-marker ${b.tier}" style="width:22px;height:22px;"></div>`;
      const icon = L.divIcon({ className: "", html, iconSize: [22, 22] });
      if (busMarkers[b.bus_id]) {
        busMarkers[b.bus_id].setLatLng([b.lat, b.lon]);
        busMarkers[b.bus_id].setIcon(icon);
      } else {
        const m = L.marker([b.lat, b.lon], { icon, keyboard: false }).addTo(map);
        busMarkers[b.bus_id] = m;
      }
      const label = lang === "ta" ? "பேருந்து" : "Bus";
      busMarkers[b.bus_id].bindTooltip(`${label} ${b.bus_id} — ${tierLabel(b.tier)}`, { direction: "top" });
    });
  }

  // ---------- Stop picker ----------
  function populateStopSelect() {
    const select = el("stopSelect");
    const prev = select.value;
    select.innerHTML = "";
    routeData.stops.forEach((stop) => {
      const opt = document.createElement("option");
      opt.value = stop.id;
      opt.textContent = lang === "ta" ? stop.name_ta : stop.name_en;
      select.appendChild(opt);
    });
    if (prev) select.value = prev;
    if (!select.value) select.value = routeData.stops[0].id;
    selectedStopId = select.value;
  }

  el("stopSelect").addEventListener("change", (e) => {
    selectedStopId = e.target.value;
    if (window.__lastState) renderStopCard(window.__lastState);
  });

  // ---------- Signal board + upcoming list ----------
  function renderStopCard(state) {
    const card = state.stops_cards.find((c) => c.stop_id === selectedStopId);
    if (!card) return;

    const board = el("signalBoard");
    board.dataset.status = card.status;
    el("signalIcon").innerHTML = ICONS[card.icon] || ICONS.check;
    el("signalTitle").textContent = lang === "ta" ? card.title_ta : card.title_en;
    el("signalSubtitle").textContent = lang === "ta" ? card.subtitle_ta : card.subtitle_en;

    const etaLabel = lang === "ta" ? "நிமிடங்களில் அடுத்த பேருந்து" : "min to next bus";
    el("signalEta").innerHTML = `<span class="num">${card.next_bus_eta_min}</span><span>${etaLabel}</span>`;

    if (lastStatus !== card.status) {
      board.classList.remove("flicker");
      void board.offsetWidth; // restart animation
      board.classList.add("flicker");
      lastStatus = card.status;
    }

    // Upcoming list
    const list = el("upcomingList");
    list.innerHTML = "";
    card.upcoming.forEach((u, idx) => {
      const row = document.createElement("div");
      row.className = "upcoming-row";
      const orderLabel = idx === 0
        ? (lang === "ta" ? "அடுத்த பேருந்து" : "Next bus")
        : (lang === "ta" ? "அதற்கு பின்" : "After that");
      const busLabel = lang === "ta" ? "பேருந்து" : "Bus";
      row.innerHTML = `
        <div class="upcoming-left">
          <span class="tier-dot ${u.tier}"></span>
          <div>
            <div class="upcoming-label">
              ${orderLabel}
              <span class="upcoming-bus-id">${busLabel} ${u.bus_label}</span>
            </div>
            <div class="upcoming-sub">${tierLabel(u.tier)}</div>
          </div>
        </div>
        <div class="upcoming-eta">${u.eta_min}<small>${lang === "ta" ? "நிமி" : "min"}</small></div>
      `;
      list.appendChild(row);
    });
  }

  // ---------- WebSocket live feed ----------
  function connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "state") {
          window.__lastState = msg.data;
          updateBuses(msg.data.buses);
          renderStopCard(msg.data);
        }
      } catch (e) { /* ignore malformed frame */ }
    };
    ws.onclose = () => setTimeout(connectWS, 2000);
    ws.onerror = () => ws.close();
  }

  // ---------- Boot ----------
  async function boot() {
    applyStaticLang();
    initMap();
    const res = await fetch(`${API_BASE}/api/route`);
    routeData = await res.json();
    el("routeChip").textContent = routeData.route_id.replace(/[^0-9]/g, "") || "42";
    el("routeName").textContent = lang === "ta" ? routeData.route_name_ta : routeData.route_name_en;
    drawRouteAndStops();
    populateStopSelect();

    const stateRes = await fetch(`${API_BASE}/api/state`);
    const state = await stateRes.json();
    window.__lastState = state;
    updateBuses(state.buses);
    renderStopCard(state);

    connectWS();
  }

  boot();
})();
