"""
headway.py
Computes spatial/temporal headway between consecutive buses on a loop route,
and maintains a sliding window of headway history per (lead, trail) pair so
the ML model has a short trend to reason about, not just a single snapshot.
"""
import time
from collections import deque, defaultdict

WINDOW_N = 5  # sliding window size, matches the design doc's N=5


class HeadwayTracker:
    def __init__(self, total_length_m: float):
        self.total_length_m = total_length_m
        self.history = defaultdict(lambda: deque(maxlen=WINDOW_N))  # pair_key -> deque[(t, headway_m)]

    @staticmethod
    def pair_key(lead_id, trail_id):
        return f"{trail_id}->{lead_id}"

    def ordered_pairs(self, bus_snapshot):
        """
        Order buses by position along the loop and pair each bus with the
        one immediately ahead of it (wrapping around at the terminus).
        Returns list of dicts: lead, trail, gap_m (>=0, wrapped).
        """
        buses = sorted(bus_snapshot, key=lambda b: b["dist_m"])
        n = len(buses)
        pairs = []
        for i in range(n):
            trail = buses[i]
            lead = buses[(i + 1) % n]
            if lead["bus_id"] == trail["bus_id"]:
                continue
            gap = lead["dist_m"] - trail["dist_m"]
            if gap < 0:
                gap += self.total_length_m
            pairs.append({"lead": lead, "trail": trail, "gap_m": gap})
        return pairs

    def update(self, bus_snapshot):
        """Push a new reading for every trail->lead pair, return enriched pairs
        with headway time, derivative, and short trend window."""
        now = time.time()
        pairs = self.ordered_pairs(bus_snapshot)
        enriched = []
        for p in pairs:
            lead, trail, gap_m = p["lead"], p["trail"], p["gap_m"]
            key = self.pair_key(lead["bus_id"], trail["bus_id"])
            dq = self.history[key]
            dq.append((now, gap_m))

            trail_speed = max(0.5, trail.get("speed_mps", 1.0))
            headway_s = gap_m / trail_speed

            # derivative of gap over the sliding window (m/s of closing speed)
            if len(dq) >= 2:
                t0, g0 = dq[0]
                t1, g1 = dq[-1]
                dt = max(1e-6, t1 - t0)
                d_gap_dt = (g1 - g0) / dt
            else:
                d_gap_dt = 0.0

            enriched.append({
                "lead_id": lead["bus_id"],
                "trail_id": trail["bus_id"],
                "gap_m": round(gap_m, 1),
                "headway_s": round(headway_s, 1),
                "d_gap_dt": round(d_gap_dt, 4),
                "window": list(dq),
                "trail_speed": trail_speed,
            })
        return enriched
