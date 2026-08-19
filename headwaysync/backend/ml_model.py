"""
ml_model.py
Loads the trained RandomForestRegressor (model.pkl) and exposes a single
predict function used by the live pipeline. Falls back to a simple physics
estimate (gap / closing-speed) if the model file is missing, so the API
never hard-fails.
"""
import os
import joblib

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.pkl")

_bundle = None
if os.path.exists(MODEL_PATH):
    _bundle = joblib.load(MODEL_PATH)


def predict_time_to_bunch(pair: dict) -> float:
    """
    pair: dict with keys gap_m, headway_s, d_gap_dt, trail_speed, window (list)
    Returns predicted seconds until this trail bus bunches into the lead bus.
    """
    import numpy as np

    window = pair.get("window", [])
    gaps = [w[1] for w in window] if window else [pair["gap_m"]]
    variance = float(np.var(gaps)) if len(gaps) > 1 else 0.0

    if _bundle is not None:
        feats = _bundle["feature_names"]
        row = {
            "gap_m": pair["gap_m"],
            "headway_s": pair["headway_s"],
            "d_gap_dt": pair["d_gap_dt"],
            "trail_speed": pair["trail_speed"],
            "window_variance": variance,
        }
        X = [[row[f] for f in feats]]
        pred = float(_bundle["model"].predict(X)[0])
        return max(0.0, pred)

    # Fallback: naive physics -- if closing (d_gap_dt < 0), extrapolate gap/rate
    if pair["d_gap_dt"] < -0.05:
        return max(0.0, pair["gap_m"] / abs(pair["d_gap_dt"]))
    return 1200.0
