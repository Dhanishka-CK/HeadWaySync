"""
main.py
HeadwaySync backend. Runs the synthetic telemetry simulator in a background
asyncio task, recomputes headway/occupancy/bunching-risk/recommendations on
every tick, and exposes:
  GET  /api/route          -> route geometry + stops (for the map)
  GET  /api/state          -> latest full state snapshot (buses + per-stop cards)
  WS   /ws/live             -> pushes the same snapshot every ~2s
  /                          -> serves the frontend
"""
import asyncio
import json
import os
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from geometry import Route
from simulator import Simulator, CAPACITY, BASE_SPEED_MPS
from headway import HeadwayTracker
from occupancy import classify
from ml_model import predict_time_to_bunch
from recommendation import build_recommendation
import control

TICK_S = 1.0          # simulation step
BROADCAST_S = 2.0      # how often we push to clients
MAX_EVENTS = 40

app = FastAPI(title="HeadwaySync")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

route = Route()
sim = Simulator(route, n_buses=4)
tracker = HeadwayTracker(route.total_length_m)
SCHEDULED_HEADWAY_S = route.total_length_m / len(sim.buses) / BASE_SPEED_MPS

STATE = {
    "buses": [],
    "stops_cards": [],
    "updated_at": 0,
    "operator": {
        "correction_enabled": True,
        "scheduled_headway_s": round(SCHEDULED_HEADWAY_S, 1),
        "pairs": [],
        "stats": {"holds_issued_total": 0, "speedups_issued_total": 0, "active_alerts": 0, "forecast_watch": 0},
        "events": [],
    },
}
_clients = set()
_prev_pair_status = {}  # pair_key -> last status, for debouncing corrections/events
_event_id_counter = 0   # monotonic id per event; the events list itself is
                         # capped at MAX_EVENTS and trimmed from the front,
                         # so its length alone isn't a reliable "is there
                         # something new" signal for the frontend once a
                         # session has been running long enough to hit the
                         # cap -- a stable, ever-increasing id is.


def compute_state():
    global _event_id_counter
    snapshot = sim.snapshot()
    pairs = tracker.update(snapshot)
    pair_lookup = {(p["lead_id"], p["trail_id"]): p for p in pairs}

    # enrich each bus with crowd tier
    bus_by_id = {}
    for b in snapshot:
        info = classify(b["occupancy"], CAPACITY)
        b["tier"] = info["tier"]
        b["ratio"] = info["ratio"]
        bus_by_id[b["bus_id"]] = b

    total_len = route.total_length_m
    stop_cards = []
    for stop in route.stops:
        # distance from stop to each bus, going forward along the loop
        ahead = []
        for b in snapshot:
            d = (b["dist_m"] - stop["dist_m"]) % total_len
            ahead.append((d, b))
        ahead.sort(key=lambda x: x[0])
        lead_d, lead_bus = ahead[0]
        trail_d, trail_bus = ahead[1] if len(ahead) > 1 else ahead[0]

        pair = pair_lookup.get((lead_bus["bus_id"], trail_bus["bus_id"]))
        if pair:
            headway_s = pair["headway_s"]
            predicted = predict_time_to_bunch(pair)
        else:
            headway_s = trail_d / max(0.5, trail_bus.get("speed_mps", 1.0))
            predicted = 1200.0

        lead_info = {"tier": bus_by_id[lead_bus["bus_id"]]["tier"]}
        trail_info = {"tier": bus_by_id[trail_bus["bus_id"]]["tier"]}

        card = build_recommendation(lead_info, trail_info, headway_s, predicted)
        card["stop_id"] = stop["id"]
        card["stop_name_en"] = stop["name_en"]
        card["stop_name_ta"] = stop["name_ta"]
        lead_eta = max(1, round(lead_d / max(0.5, lead_bus.get("speed_mps", 1.0)) / 60))
        trail_eta = max(1, round(trail_d / max(0.5, trail_bus.get("speed_mps", 1.0)) / 60))
        card["next_bus_eta_min"] = lead_eta
        card["next_bus_tier"] = lead_info["tier"]
        card["upcoming"] = [
            {"eta_min": lead_eta, "tier": lead_info["tier"], "bus_label": lead_bus["bus_id"]},
            {"eta_min": trail_eta, "tier": trail_info["tier"], "bus_label": trail_bus["bus_id"]},
        ]
        stop_cards.append(card)

    # --- Operator / control-room side ---------------------------------
    # Reuses the same pairs already computed above (pair_lookup), so this
    # is the identical live headway data the rider view is built from --
    # not a separate, possibly-inconsistent calculation.
    operator_pairs = []
    active_alerts = 0
    forecast_watch = 0
    events = STATE["operator"]["events"]
    stats = STATE["operator"]["stats"]
    correction_enabled = STATE["operator"]["correction_enabled"]

    for pair in pairs:
        predicted = predict_time_to_bunch(pair)
        decision = control.evaluate(pair, SCHEDULED_HEADWAY_S, predicted)
        key = f"{decision['trail_id']}->{decision['lead_id']}"
        prev_status = _prev_pair_status.get(key, "NORMAL")
        status = decision["status"]

        if status in ("BUNCHING_RISK", "EARLY_WARNING"):
            active_alerts += 1
        if decision["forecast_alert"]:
            forecast_watch += 1

        # Only actually ISSUE a correction (and log an event) on the
        # transition into a flagged state -- not every tick while it
        # remains flagged, otherwise hold time would stack indefinitely.
        if status != prev_status:
            _event_id_counter += 1
            events.append({
                "id": _event_id_counter,
                "t": round(time.time(), 1),
                "level": status,
                "message": decision["message"] if status != "NORMAL"
                           else f"{decision['trail_id']} back to normal headway behind {decision['lead_id']}.",
                # Structured fields alongside the English message so the
                # frontend can render a localized (Tamil) sentence from the
                # same data instead of needing a second English-only string
                # from the backend.
                "trail_id": decision["trail_id"],
                "lead_id": decision["lead_id"],
                "action": decision["action"],
                "action_value": decision["action_value"],
                "action_duration_s": decision["action_duration_s"],
                "headway_s": decision["headway_s"],
                "scheduled_headway_s": decision["scheduled_headway_s"],
                "ratio": decision["ratio"],
            })
            if len(events) > MAX_EVENTS:
                del events[: len(events) - MAX_EVENTS]

            if correction_enabled:
                if decision["action"] == "HOLD":
                    sim.issue_hold(decision["trail_id"], decision["action_value"])
                    stats["holds_issued_total"] += 1
                elif decision["action"] == "SPEED_UP":
                    sim.issue_speed_boost(
                        decision["trail_id"], decision["action_value"], control.SPEED_BOOST_DURATION_S
                    )
                    stats["speedups_issued_total"] += 1

        _prev_pair_status[key] = status
        operator_pairs.append(decision)

    stats["active_alerts"] = active_alerts
    stats["forecast_watch"] = forecast_watch

    STATE["operator"]["pairs"] = operator_pairs
    STATE["operator"]["correction_enabled"] = correction_enabled

    STATE["buses"] = snapshot
    STATE["stops_cards"] = stop_cards
    STATE["updated_at"] = time.time()


async def simulation_loop():
    while True:
        sim.step(TICK_S)
        await asyncio.sleep(TICK_S)


async def broadcast_loop():
    while True:
        compute_state()
        dead = set()
        payload = json.dumps({"type": "state", "data": STATE})
        for ws in list(_clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        _clients.difference_update(dead)
        await asyncio.sleep(BROADCAST_S)


@app.on_event("startup")
async def startup():
    compute_state()
    asyncio.create_task(simulation_loop())
    asyncio.create_task(broadcast_loop())


@app.get("/health")
def health():
    """Lightweight liveness check -- returns instantly, touches nothing else.
    Point an external uptime pinger (e.g. UptimeRobot, cron-job.org) at this
    URL every 5-10 min on free hosts like Render to prevent the service from
    idling to sleep before a demo."""
    return {"status": "ok", "t": time.time()}


@app.get("/api/route")
def get_route():
    return {
        "route_id": route.route_id,
        "route_name_en": route.route_name_en,
        "route_name_ta": route.route_name_ta,
        "polyline": route.as_geojson_line(),
        "stops": route.stops,
    }


@app.get("/api/state")
def get_state():
    return STATE


@app.post("/api/correction")
async def set_correction(payload: dict):
    """Toggle operator corrections on/off. When off, control.py's decisions
    are still computed and shown in the Control Room (so you can see what
    the system WOULD do), but issue_hold/issue_speed_boost are not called --
    matches the ON/OFF demo pattern of "watch it bunch vs. watch it self-correct"."""
    enabled = bool(payload.get("enabled", True))
    STATE["operator"]["correction_enabled"] = enabled
    return {"correction_enabled": enabled}


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "state", "data": STATE}))
        while True:
            # keep the connection open; we don't need client messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)


FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/control")
    def control_room():
        return FileResponse(os.path.join(FRONTEND_DIR, "control.html"))
