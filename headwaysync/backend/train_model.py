"""
train_model.py
Trains the bunching-prediction regressor on SYNTHETIC data, since no historical
GPS trend logs exist for this demo. This is run offline (not at request time).

Approach:
  1. Simulate thousands of independent lead/trail bus pair trajectories on a
     1D route, each with randomized dwell-time noise and traffic-factor drift
     (the same mechanics as simulator.py, but stripped down to just the two
     buses for fast headroom generation).
  2. At random sampled timesteps within each trajectory, extract the same
     sliding-window features the live system will compute (current gap,
     headway time, gap derivative, trail speed, window variance).
  3. Label = actual simulated time (seconds) until the gap closes to zero
     (i.e. true bunching), capped at a horizon of 1200s (20 min) for pairs
     that never bunch in that window.
  4. Train a RandomForestRegressor to predict that label from the features.

Run: python train_model.py
Produces: model.pkl (joblib dump of the fitted model + feature names)
"""
import random
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error
import joblib

HORIZON_S = 1200.0
DT = 5.0
ROUTE_LEN = 3400.0
BASE_SPEED = 6.5


def simulate_pair(rng, steps=400):
    """Simulate one lead/trail pair; return list of (t, gap_m, speed_trail)."""
    lead_dist = rng.uniform(200, 1000)
    trail_dist = 0.0
    lead_traffic = rng.uniform(0.85, 1.2)
    trail_traffic = rng.uniform(0.85, 1.2)
    lead_dwell = 0.0
    trail_dwell = 0.0

    traj = []
    t = 0.0
    for _ in range(steps):
        # random-walk traffic factors (congestion waves)
        lead_traffic = min(1.7, max(0.6, lead_traffic + rng.uniform(-0.05, 0.05)))
        trail_traffic = min(1.7, max(0.6, trail_traffic + rng.uniform(-0.05, 0.05)))

        # random dwell events (stop arrivals), more frequent & longer than avg
        if rng.random() < 0.06:
            lead_dwell += rng.gauss(14, 6)
        if rng.random() < 0.06:
            trail_dwell += rng.gauss(14, 6)

        if lead_dwell > 0:
            lead_dwell -= DT
            lead_speed = 0.0
        else:
            lead_speed = BASE_SPEED / lead_traffic
            lead_dist += lead_speed * DT

        if trail_dwell > 0:
            trail_dwell -= DT
            trail_speed = 0.0
        else:
            trail_speed = BASE_SPEED / trail_traffic
            trail_dist += trail_speed * DT

        gap = lead_dist - trail_dist
        traj.append((t, gap, max(trail_speed, 0.5)))
        t += DT

        if gap <= 15:
            break
    return traj


def extract_examples(traj, rng, window_n=5):
    """From one trajectory, sample points and compute sliding-window features
    plus the true time-to-bunch label."""
    examples = []
    n = len(traj)
    # find bunch time (first index where gap <= 15), else None
    bunch_t = None
    for (t, gap, _spd) in traj:
        if gap <= 15:
            bunch_t = t
            break

    for i in range(window_n, n):
        t, gap, trail_speed = traj[i]
        window = traj[max(0, i - window_n + 1): i + 1]
        g0 = window[0][1]
        g1 = window[-1][1]
        dt = max(1e-6, window[-1][0] - window[0][0])
        d_gap_dt = (g1 - g0) / dt
        gaps_in_window = [w[1] for w in window]
        variance = float(np.var(gaps_in_window))
        headway_s = gap / trail_speed

        if bunch_t is not None and bunch_t >= t:
            label = min(HORIZON_S, bunch_t - t)
        else:
            label = HORIZON_S

        # subsample: don't need every single timestep, keep training set sane
        if rng.random() < 0.35:
            examples.append({
                "gap_m": gap,
                "headway_s": headway_s,
                "d_gap_dt": d_gap_dt,
                "trail_speed": trail_speed,
                "window_variance": variance,
                "label": label,
            })
    return examples


def build_dataset(n_trajectories=1500, seed=42):
    rng = random.Random(seed)
    rows = []
    for _ in range(n_trajectories):
        traj = simulate_pair(rng)
        rows.extend(extract_examples(traj, rng))
    return rows


def main():
    print("Generating synthetic bunching trajectories...")
    rows = build_dataset()
    print(f"  -> {len(rows)} training examples from synthetic simulation")

    feature_names = ["gap_m", "headway_s", "d_gap_dt", "trail_speed", "window_variance"]
    X = np.array([[r[f] for f in feature_names] for r in rows])
    y = np.array([r["label"] for r in rows])

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = RandomForestRegressor(
        n_estimators=60,
        max_depth=9,
        min_samples_leaf=6,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, pred)
    print(f"  -> Test MAE: {mae:.1f} seconds (horizon capped at {HORIZON_S:.0f}s)")

    importances = dict(zip(feature_names, model.feature_importances_.round(3)))
    print(f"  -> Feature importances: {importances}")

    joblib.dump({"model": model, "feature_names": feature_names}, "model.pkl")
    print("Saved model.pkl")


if __name__ == "__main__":
    main()
