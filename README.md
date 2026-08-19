# HeadwaySync

A live commuter guidance system for bus bunching — predicts crowded/bunched
buses before they arrive and tells commuters, in plain language, whether to
board the approaching bus or wait a few minutes for a roomier one behind it.

Built as a hackathon MVP. Runs entirely locally, no external API keys needed.

---

## Quick start

Requires Python 3.10+.

```bash
cd backend
pip install -r requirements.txt

# (Optional) retrain the ML model from scratch — a pretrained model.pkl is
# already included, so this step is not required to run the app.
python train_model.py

# Run the app
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open **http://localhost:8000** in a browser. That's it — the backend
serves both the API and the frontend.

The simulation starts immediately and seeds one bus as already crowded with
a near-empty bus close behind it, so you'll see a "wait for the next bus"
recommendation right away instead of waiting several minutes for organic
bunching to build up. Leave it running and the rest of the route will
develop its own bunching/crowding patterns over time.

---

## What this demo actually does (and what's simulated)

Being upfront about scope, since a real deployment would plug into live
transit infrastructure that isn't available for a hackathon demo:

| Component | Status |
|---|---|
| Headway / bunching math (distance projection, sliding-window trend, gap derivative) | **Real** — computed live from position data, not hardcoded |
| ML bunching predictor | **Real model, trained on synthetic data.** A RandomForestRegressor is actually trained (see `train_model.py`) on ~125k synthetic bus-pair trajectories with randomized traffic/dwell noise, and achieves ~2.6 min MAE on a 20-min prediction horizon. There's no historical real-world GPS log to train on, so this is honestly synthetic — but the training pipeline, features, and model are real, not a stub. |
| ETM occupancy ledger (`Occupancy_k = Occupancy_(k-1) + Boardings_k − Alightings_k`) | **Real calculation**, run against a synthetic ETM ticket log |
| Bus GPS positions & ETM ticket events | **Simulated.** No live transit GPS/ETM feed exists for this route, so `simulator.py` generates realistic movement (variable traffic, Poisson-ish dwell times, demand-weighted boarding/alighting) instead. This module is written as a drop-in swap — a real deployment would replace `Simulator.step()` with a poller against the transit agency's actual GPS/ETM endpoints, and nothing downstream (headway engine, ML model, occupancy, recommendations) would need to change. |
| Concession correction scalar (α) | A **fixed assumed constant** (1.12), flagged in `occupancy.py`. A real deployment would calibrate this per-route from manual ridership counts vs. ticket volume — we don't have that data here. |
| Recommendation thresholds (4 min / 12 min / 80% / 60%) | Reasonable defaults from the original design doc, not empirically tuned. Easy to adjust in `recommendation.py`. |

The frontend deliberately shows **none** of the above — no numbers, formulas,
or model scores. Commuters only ever see a plain-language card ("Let this
one go" / "Good to board") and a rounded arrival estimate.

---

## Project structure

```
headwaysync/
├── backend/
│   ├── main.py            FastAPI app: REST + WebSocket, ties everything together
│   ├── simulator.py        Synthetic GPS/ETM telemetry engine
│   ├── geometry.py         Route polyline math (distance <-> lat/lon)
│   ├── headway.py          Live headway calculation + sliding-window trend
│   ├── occupancy.py        ETM-ledger occupancy + crowd tier classification
│   ├── ml_model.py         Loads model.pkl, predicts time-to-bunching
│   ├── train_model.py      Generates synthetic training data & trains the model
│   ├── recommendation.py   Rider-facing: converts signals into a plain-language decision card
│   ├── control.py          Operator-facing: HOLD/SPEED_UP decisions for the Control Room
│   ├── model.pkl           Pretrained model (included, ready to use)
│   ├── requirements.txt
│   └── data/route.json     Real Coimbatore road-following route + stops (Gandhipuram–Thudiyalur–Saravanampatti)
└── frontend/
    ├── index.html           Rider View
    ├── app.js               Rider View: WebSocket live updates, map, bilingual toggle
    ├── control.html         Control Room (operator view)
    ├── control.js           Control Room: pairs table, event log, fleet map, toast alerts, bilingual toggle
    ├── control.css          Control Room-specific styling
    ├── style.css            Shared transit-signage inspired design system
    └── vendor/leaflet/      Leaflet map library (vendored locally, no CDN dependency)
```

## Notes on the frontend

- **Rider View** (`/`) — bilingual English / Tamil toggle in the header,
  switches all live and static text. The "signal board" card at the top is
  the single source of guidance — it changes color (green = board,
  amber = crowded-but-far, red = wait) the same way a physical bus-stop
  LED sign would. The "Next at this stop" list shows the actual bus
  number (e.g. `TN37-4200`) alongside its ETA.
- **Control Room** (`/control`) — also bilingual (English / Tamil toggle in
  the header, mirroring the Rider View). Dynamic content (pair statuses,
  recommended-action sentences, event log) is rebuilt client-side from the
  structured decision fields the backend sends (bus ids, headway, ratio,
  action, duration) rather than translating a single English string, so
  both languages stay numerically in sync. It includes:
  - a live fleet map with all 4 buses shown as numbered markers
    (e.g. `4200`), colored by that bus's own headway status;
  - a pairs table and event log (see "Notes on the frontend" above for the
    ON/OFF correction toggle demo);
  - toast notifications that pop up whenever a new HOLD or SPEED_UP action
    is recommended, naming the bus and how long the action applies for.
- The map uses a vendored copy of Leaflet so it doesn't depend on a CDN
  being reachable — only the map *tiles* themselves (OpenStreetMap) need
  internet access at runtime.
- Google Fonts (Barlow Condensed / Inter / Noto Sans Tamil) are loaded from
  Google's CDN; if offline, the UI falls back to system fonts gracefully.

## Extending this

- Swap `simulator.py` for a real GPS/ETM poller — the rest of the pipeline
  is already decoupled from where the data comes from.
- Add more routes by adding more `data/route_*.json` files and a route
  picker in the frontend.
- Retrain `model.pkl` on real historical headway logs once available, by
  adapting `train_model.py`'s feature extraction to real trajectories
  instead of synthetic ones.
