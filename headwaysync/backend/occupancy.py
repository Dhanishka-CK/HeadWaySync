"""
occupancy.py
Since the simulator already tracks true per-bus occupancy directly (it is
generating the ETM events itself), this module is the layer a real deployment
would use: it recomputes occupancy purely from the ETM boarding/alighting
ledger (as the design doc specifies), applies the concession correction
scalar, and classifies into crowd tiers. This keeps the "ETM -> occupancy"
pipeline real and swappable even though our demo's ground truth already
exists in the simulator.
"""

CAPACITY = 45
CONCESSION_SCALAR = 1.12  # accounts for pass holders / unticketed concession riders
# NOTE: this is a fixed assumed constant for the demo, not survey-derived.
# A production deployment would calibrate alpha_route per route from manual
# ridership counts vs ETM ticket volume.

TIER_THRESHOLDS = {
    "low": 0.50,
    "moderate": 0.80,
}


def occupancy_from_ledger(etm_log, bus_id):
    """Reconstruct current occupancy for a bus purely from its ETM events,
    walking the boarding/alighting ledger in order (Occupancy_k = Occupancy_(k-1)
    + Boardings_k - Alightings_k), as an independent check against the
    simulator's live occupancy counter."""
    count = 0
    for ev in etm_log:
        if ev["bus_id"] != bus_id:
            continue
        if ev["event"] == "boarding":
            count += 1
        elif ev["event"] == "alighting":
            count -= ev.get("count", 1)
    return max(0, count)


def classify(occupancy: int, capacity: int = CAPACITY):
    adjusted = occupancy * CONCESSION_SCALAR
    ratio = adjusted / capacity
    if ratio < TIER_THRESHOLDS["low"]:
        tier = "low"
    elif ratio < TIER_THRESHOLDS["moderate"]:
        tier = "moderate"
    else:
        tier = "high"
    return {
        "tier": tier,
        "ratio": round(min(ratio, 1.3), 3),
        "occupancy": occupancy,
    }
