"""
geometry.py
Handles route polyline math: converting a linear distance-along-route (meters)
into a lat/lon point, and basic haversine distance.
"""
import json
import math
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "route.json")


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class Route:
    """Loads the route geometry and exposes distance <-> lat/lon projection."""

    def __init__(self, path=DATA_PATH):
        with open(path, "r", encoding="utf-8") as f:
            self.raw = json.load(f)
        self.route_id = self.raw["route_id"]
        self.route_name_en = self.raw["route_name_en"]
        self.route_name_ta = self.raw["route_name_ta"]
        self.stops = self.raw["stops"]
        self.total_length_m = self.raw["total_length_m"]

        # `stops` are real, named boarding points (used for passenger
        # boarding/alighting logic and the rider-facing stop picker).
        # `shape` is an optional list of extra road-geometry waypoints
        # (no boarding happens there) that bend the polyline so it follows
        # the *actual* road corridor between stops instead of cutting a
        # straight line across the map. Both are merged, sorted by distance
        # along the route, into `path_points` -- used only for drawing the
        # route and interpolating a bus's live lat/lon. Boarding logic
        # (simulator.py) still walks `self.stops` only, so adding shape
        # points here never creates a phantom "stop".
        shape = self.raw.get("shape", [])
        merged = list(self.stops) + list(shape)
        merged.sort(key=lambda p: p["dist_m"])
        self.path_points = merged

    def point_at_distance(self, dist_m):
        """Given cumulative distance along the route, return (lat, lon),
        interpolating along the full road-shape polyline (stops + via
        points), not just the named stops."""
        dist_m = max(0.0, min(dist_m, self.total_length_m))
        pts = self.path_points
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            if a["dist_m"] <= dist_m <= b["dist_m"]:
                seg_len = b["dist_m"] - a["dist_m"]
                if seg_len <= 0:
                    return a["lat"], a["lon"]
                t = (dist_m - a["dist_m"]) / seg_len
                lat = a["lat"] + t * (b["lat"] - a["lat"])
                lon = a["lon"] + t * (b["lon"] - a["lon"])
                return lat, lon
        last = pts[-1]
        return last["lat"], last["lon"]

    def next_stop(self, dist_m):
        """Return the next real (boarding) stop ahead of the given distance."""
        for s in self.stops:
            if s["dist_m"] >= dist_m + 1e-6:
                return s
        return self.stops[-1]

    def as_geojson_line(self):
        """Full road-following polyline (stops + via/shape points), for
        drawing the route on the map."""
        return [[p["lat"], p["lon"]] for p in self.path_points]
