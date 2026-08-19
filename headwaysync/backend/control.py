"""
control.py
The operator-facing counterpart to recommendation.py (which speaks to
commuters). This module answers a different question: for a given
lead/trail bus pair, should the CONTROL ROOM tell the trail bus to
HOLD (wait a little longer at its next stop) or SPEED_UP (cut dwell time,
make up ground), in order to prevent or resolve bunching?

Design choice, consistent with the rest of this project: this stays a
transparent, auditable rule (headway ratio vs. schedule), not a model,
because an operator instructing a real driver needs to be able to point to
*why* -- "your headway is 38% under schedule and closing" is defensible in
a way that a black-box action never is. The ML model in ml_model.py is used
elsewhere (for the early "ML forecast" flag) but never decides the action
itself.
"""

# Headway ratio thresholds (actual_headway / scheduled_headway)
BUNCHING_RATIO = 0.45   # under 45% of scheduled headway -> actively bunching
WARNING_RATIO = 0.72    # under 72% -> early warning, closing in
LAGGING_RATIO = 1.8     # over 180% -> trail bus has fallen too far behind

ALPHA = 0.55            # proportional gain for hold-time sizing
MIN_HOLD_S = 10.0
MAX_HOLD_S = 90.0
SPEED_BOOST_FACTOR = 1.18   # ~18% effective speed increase while boosted
SPEED_BOOST_DURATION_S = 90.0

FORECAST_HORIZON_S = 240.0  # ML: flag "worth watching" if predicted bunch < 4 min


def evaluate(pair: dict, scheduled_headway_s: float, predicted_bunch_s: float) -> dict:
    """
    pair: one entry from HeadwayTracker.update() -- has lead_id, trail_id,
          gap_m, headway_s, d_gap_dt
    scheduled_headway_s: target headway for this route (total_length / n_buses / base_speed)
    predicted_bunch_s: ML model's predicted seconds-until-bunch for this pair

    Returns a dict describing status + recommended action. This function is
    pure (no side effects / no simulator mutation) -- main.py decides
    whether and when to actually apply it, so the rule stays testable in
    isolation.
    """
    headway_s = max(1.0, pair["headway_s"])
    ratio = headway_s / max(1.0, scheduled_headway_s)
    risk = 1.0 - ratio  # >0 : gap too small (trail closing in). <0 : gap too big.

    out = {
        "lead_id": pair["lead_id"],
        "trail_id": pair["trail_id"],
        "gap_m": pair["gap_m"],
        "headway_s": round(headway_s, 1),
        "scheduled_headway_s": round(scheduled_headway_s, 1),
        "ratio": round(ratio, 3),
        "risk": round(risk, 3),
        "predicted_bunch_s": round(predicted_bunch_s, 1),
        "forecast_alert": False,
        "status": "NORMAL",
        "action": "NONE",
        "action_value": None,       # seconds to hold, or speed factor
        "action_duration_s": None,  # how long the action applies for, in seconds
        "message": "Headway within normal range.",
    }

    if ratio <= BUNCHING_RATIO:
        out["status"] = "BUNCHING_RISK"
    elif ratio <= WARNING_RATIO:
        out["status"] = "EARLY_WARNING"
    elif ratio >= LAGGING_RATIO:
        out["status"] = "LAGGING"
    else:
        out["status"] = "NORMAL"

    # ML forecast flag -- only meaningful for buses not already flagged by
    # the rule (otherwise it's just restating what the rule already caught)
    if out["status"] == "NORMAL" and 0 < predicted_bunch_s < FORECAST_HORIZON_S:
        out["forecast_alert"] = True

    if out["status"] in ("BUNCHING_RISK", "EARLY_WARNING"):
        hold_s = max(MIN_HOLD_S, min(MAX_HOLD_S, ALPHA * risk * scheduled_headway_s))
        out["action"] = "HOLD"
        out["action_value"] = round(hold_s, 1)
        out["action_duration_s"] = round(hold_s, 1)
        out["message"] = (
            f"HOLD {pair['trail_id']} +{hold_s:.0f}s at next stop — "
            f"headway {headway_s:.0f}s vs scheduled {scheduled_headway_s:.0f}s "
            f"({ratio*100:.0f}% of schedule), closing on {pair['lead_id']}."
        )
    elif out["status"] == "LAGGING":
        out["action"] = "SPEED_UP"
        out["action_value"] = SPEED_BOOST_FACTOR
        out["action_duration_s"] = SPEED_BOOST_DURATION_S
        out["message"] = (
            f"ADVISE {pair['trail_id']} to minimize dwell / make up time — "
            f"headway {headway_s:.0f}s vs scheduled {scheduled_headway_s:.0f}s "
            f"({ratio*100:.0f}% of schedule), falling behind {pair['lead_id']}."
        )

    return out
