"""
simulator.py
Synthetic telemetry engine. Since we have no live transit GPS/ETM feed to plug
into for this demo, this module generates realistic vehicle movement and
ticketing behaviour along the route polyline. It is written as a drop-in
replacement for a real feed: swap `Simulator.tick()` for a real GPS/ETM
poller and the rest of the pipeline (headway engine, ML model, occupancy,
recommendations) is unaffected.

Design choices that make it "realistic" rather than uniform random noise:
  - Buses have per-trip speed profiles (base speed + traffic multiplier that
    drifts slowly, simulating real congestion waves) -- this is what makes
    bunching emerge naturally rather than being scripted.
  - Dwell time at each stop is drawn from a Poisson distribution scaled by
    how many passengers board/alight there (busy stops = longer dwell =
    the classic bunching feedback loop).
  - Each stop has a demand profile (low/medium/high) so ETM ticket volume
    is not uniform across the route.
"""
import random
import time
from dataclasses import dataclass, field
from typing import List, Dict

from geometry import Route

CAPACITY = 45  # total on-board capacity (seated + comfortable standing) per bus
BASE_SPEED_MPS = 6.5  # ~23 km/h average urban bus cruising speed
STOP_DEMAND_WEIGHT = {
    "terminus": 3.0,
    "high_demand": 2.2,
    "normal": 1.0,
}


@dataclass
class Bus:
    bus_id: str
    dist_m: float = 0.0            # cumulative distance along route
    speed_mps: float = BASE_SPEED_MPS
    traffic_factor: float = 1.0    # >1 = slower (congestion), drifts over time
    dwell_remaining_s: float = 0.0
    occupancy: int = 0
    last_stop_idx: int = -1
    trip_count: int = 0
    # per-passenger destination ledger: list of (dest_stop_idx) for everyone aboard
    manifest: List[int] = field(default_factory=list)
    # --- operator control state (set by control.py via Simulator, consumed here) ---
    hold_extra_s: float = 0.0          # queued extra dwell, applied at next stop arrival
    speed_boost_factor: float = 1.0    # >1 while a SPEED_UP correction is active
    speed_boost_remaining_s: float = 0.0


class Simulator:
    def __init__(self, route: Route, n_buses: int = 4, seed: int = 7):
        self.route = route
        self.rng = random.Random(seed)
        self.buses: Dict[str, Bus] = {}
        self.etm_log: List[dict] = []  # rolling ticket transaction log
        self._init_buses(n_buses)
        self.tick_count = 0

    def _init_buses(self, n_buses):
        # Stagger buses evenly along the route to start, like a real schedule,
        # then let organic speed variance create bunching over time.
        spacing = self.route.total_length_m / n_buses
        for i in range(n_buses):
            bus_id = f"TN37-{4200 + i}"
            start_dist = (i * spacing) % self.route.total_length_m
            b = Bus(bus_id=bus_id, dist_m=start_dist)
            b.traffic_factor = self.rng.uniform(0.85, 1.15)
            # last_stop_idx must reflect stops already passed at the starting
            # position, otherwise step() thinks the bus just arrived at stop 0
            # and snaps it straight back to distance 0.
            passed = -1
            for idx, stop in enumerate(self.route.stops):
                if stop["dist_m"] <= start_dist:
                    passed = idx
            b.last_stop_idx = passed
            self.buses[bus_id] = b

        # Demo seeding: make bus 0 already crowded and running a bit slow
        # (as if it absorbed a big boarding wave earlier), and put bus 1
        # close behind it but nearly empty and slightly faster -- a textbook
        # early-stage bunching pair, so the UI has something meaningful to
        # show right away instead of a flat "board now" state for the first
        # several minutes while organic bunching builds up.
        bus_list = list(self.buses.values())
        if len(bus_list) >= 2:
            lead, trail = bus_list[0], bus_list[1]
            lead.occupancy = 34
            lead.manifest = [self.rng.randrange(0, len(self.route.stops)) for _ in range(34)]
            lead.traffic_factor = 1.25
            trail.dist_m = max(0.0, lead.dist_m - 420.0)
            trail.occupancy = 6
            trail.manifest = [self.rng.randrange(0, len(self.route.stops)) for _ in range(6)]
            trail.traffic_factor = 0.9

    def _demand_weight(self, stop):
        return STOP_DEMAND_WEIGHT.get(stop["kind"], 1.0)

    def _handle_stop_arrival(self, bus: Bus, stop_idx: int):
        """Simulate alighting + boarding + ETM ticket generation at a stop."""
        stop = self.route.stops[stop_idx]

        # Alighting: passengers whose destination is this stop leave
        alighting = sum(1 for d in bus.manifest if d == stop_idx)
        bus.manifest = [d for d in bus.manifest if d != stop_idx]
        bus.occupancy = max(0, bus.occupancy - alighting)

        # Boarding: demand-weighted random count, capped by remaining capacity.
        # Occasionally simulate a "rush" burst at a stop (e.g. college/office
        # let-out) so crowd tiers realistically vary across the route instead
        # of staying uniformly low.
        weight = self._demand_weight(stop)
        rush = 2.2 if self.rng.random() < 0.12 else 1.0
        mean_boarding = 4.0 * weight * rush
        boarding = int(min(
            max(0, round(self.rng.gauss(mean_boarding, 1.8 * weight))),
            max(0, CAPACITY - bus.occupancy)
        ))

        remaining_stops = list(range(stop_idx + 1, len(self.route.stops)))
        for _ in range(boarding):
            if not remaining_stops:
                break
            # Weight toward farther stops (triangular-ish) so passengers stay
            # aboard for a while, letting occupancy actually build up.
            dest = remaining_stops[min(
                len(remaining_stops) - 1,
                int(self.rng.triangular(0, len(remaining_stops) - 1, len(remaining_stops) - 1))
            )]
            bus.manifest.append(dest)
        bus.occupancy += boarding

        # Log ETM transactions (boardings) -- this is the feed downstream
        # modules would normally receive from real ticketing machines.
        ts = time.time()
        for _ in range(boarding):
            self.etm_log.append({
                "bus_id": bus.bus_id,
                "route_id": self.route.route_id,
                "origin_stop_id": stop["id"],
                "timestamp": ts,
                "event": "boarding",
            })
        if alighting:
            self.etm_log.append({
                "bus_id": bus.bus_id,
                "route_id": self.route.route_id,
                "stop_id": stop["id"],
                "timestamp": ts,
                "event": "alighting",
                "count": alighting,
            })
        # Trim log
        if len(self.etm_log) > 2000:
            self.etm_log = self.etm_log[-2000:]

        # Dwell time: longer if more people are boarding/alighting -- this
        # is the mechanism that turns a busy stop into a bunching trigger.
        activity = boarding + alighting
        bus.dwell_remaining_s = max(6.0, self.rng.gauss(8 + 2.2 * activity, 3.0))

        # Apply any queued operator HOLD correction -- consumed once here,
        # so it affects exactly the next stop dwell and nothing beyond it.
        if bus.hold_extra_s > 0:
            bus.dwell_remaining_s += bus.hold_extra_s
            bus.hold_extra_s = 0.0

        bus.last_stop_idx = stop_idx

    def issue_hold(self, bus_id: str, extra_s: float):
        """Queue an operator-ordered hold: adds extra_s of dwell time the
        next time this bus arrives at a stop. Real-world equivalent: a
        control-room instruction relayed to the driver to wait a bit
        longer before departing the upcoming stop."""
        bus = self.buses.get(bus_id)
        if bus:
            bus.hold_extra_s += extra_s

    def issue_speed_boost(self, bus_id: str, factor: float, duration_s: float):
        """Queue an operator-ordered speed-up: temporarily scales this bus's
        effective speed by `factor` for `duration_s` seconds of simulated
        driving time. Real-world equivalent: driver advised to minimize
        dwell time / avoid unnecessary slowdowns to close a growing gap."""
        bus = self.buses.get(bus_id)
        if bus:
            bus.speed_boost_factor = factor
            bus.speed_boost_remaining_s = duration_s

    def step(self, dt: float):
        """Advance simulation by dt seconds."""
        self.tick_count += 1
        for bus in self.buses.values():
            # Traffic factor slowly random-walks to simulate congestion waves
            bus.traffic_factor += self.rng.uniform(-0.03, 0.03)
            bus.traffic_factor = min(1.6, max(0.6, bus.traffic_factor))

            if bus.dwell_remaining_s > 0:
                bus.dwell_remaining_s -= dt
                continue

            # Apply any active operator SPEED_UP correction, ticking down its
            # remaining duration; reverts to normal speed once it expires.
            if bus.speed_boost_remaining_s > 0:
                bus.speed_boost_remaining_s -= dt
                if bus.speed_boost_remaining_s <= 0:
                    bus.speed_boost_factor = 1.0
                    bus.speed_boost_remaining_s = 0.0

            effective_speed = (bus.speed_mps / bus.traffic_factor) * bus.speed_boost_factor
            bus.dist_m += effective_speed * dt

            if bus.dist_m >= self.route.total_length_m:
                # Reached terminus: everyone alights, bus loops back
                bus.occupancy = 0
                bus.manifest = []
                bus.dist_m = 0.0
                bus.trip_count += 1
                bus.last_stop_idx = -1
                bus.traffic_factor = self.rng.uniform(0.85, 1.15)
                continue

            # Check if we've reached/passed the next stop
            next_idx = bus.last_stop_idx + 1
            if next_idx < len(self.route.stops):
                stop = self.route.stops[next_idx]
                if bus.dist_m >= stop["dist_m"]:
                    bus.dist_m = stop["dist_m"]
                    self._handle_stop_arrival(bus, next_idx)

    def snapshot(self):
        out = []
        for bus in self.buses.values():
            lat, lon = self.route.point_at_distance(bus.dist_m)
            out.append({
                "bus_id": bus.bus_id,
                "dist_m": round(bus.dist_m, 1),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "occupancy": bus.occupancy,
                "dwelling": bus.dwell_remaining_s > 0,
                "speed_mps": round(bus.speed_mps / bus.traffic_factor, 2),
                "trip_count": bus.trip_count,
                "holding": bus.hold_extra_s > 0,
                "speed_boosted": bus.speed_boost_remaining_s > 0,
            })
        return out


# random.Random has no .poisson; add a tiny helper so Simulator._handle_stop_arrival
# can call self.rng.poisson uniformly without importing numpy just for this.
def _poisson(self, lam):
    # Knuth's algorithm -- fine for small lambda used here
    L = pow(2.718281828, -lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= self.random()
        if p <= L:
            return k - 1


random.Random.poisson = _poisson
