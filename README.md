# Lumin
# Kaiku Intelligence API

Manufacturing operations intelligence for Ginolis Kaiku — real-time IoT production traceability, instant insights from your factory floor. No BI team, no SQL knowledge required.

## Architecture

```
Browser / Client
      │
      ▼
FastAPI Backend (JWT auth on all routes)
      │
      ├── /auth/*           ──► Register / Login (public)
      ├── /metrics/*        ──► Pre-written SQL (protected)
      ├── /machines         ──► Machine list (protected)
      └── /ask              ──► Groq: classify → data or knowledge (protected, rate limited 10/min)
                                       │
                          ┌────────────┴─────────────┐
                          ▼                           ▼
                    NL→SQL→SQLite            Kaiku knowledge base
                   (data questions)        (domain/product questions)
```

**Core endpoints run with zero external dependencies.** Groq is optional — only needed for `/ask`.

## Two-tier query system

| Query type | Example | Handled by |
|---|---|---|
| Hardcoded | "Show OEE by machine" | `/metrics/oee` — pre-written SQL |
| Freeform data | "Which operator had the lowest yield in March?" | `/ask` — Groq NL→SQL |
| Freeform knowledge | "What does PPM mean?" | `/ask` — Groq knowledge base |

## Endpoints

### Auth (public)

| Endpoint | Description |
|---|---|
| `POST /auth/register` | Create a new user account |
| `POST /auth/login` | Login, receive JWT |
| `GET /health` | Health check |

### Core metrics (protected)

| Endpoint | Description |
|---|---|
| `GET /metrics/yield` | Yield % per machine |
| `GET /metrics/oee` | OEE % per machine |
| `GET /metrics/ppm` | Parts Per Million defect rate |
| `GET /metrics/cycle-time` | Avg/min/max cycle time per machine |
| `GET /metrics/defects` | Defect breakdown by type and severity |
| `GET /metrics/downtime` | Downtime by machine and event type |
| `GET /metrics/traceability?barcode=X` | Full trace for a barcode |
| `GET /machines` | All machines |

All metric endpoints accept:
- `?machine_id=M001` — filter by machine
- `?period=week` — filter by period (`today`, `week`, `month`, `all`, or `YYYY-MM`)

### Freeform /ask (protected, rate limited)

```
POST /ask
{ "question": "Which machine had the highest PPM last month?", "period": "month" }
```

The question is first classified:
- **data** → NL→SQL→database result
- **knowledge** → answered from Kaiku product/domain knowledge base

Response includes `"type": "data" | "knowledge"` so the frontend can render appropriately.

## Setup

**1. Install dependencies**
```cmd
pip install -r requirements.txt
```

**2. Generate the database**
```cmd
python data/synthetic_data.py
```

**3. Configure environment**
```cmd
copy .env.example .env
```

```env
GROQ_API_KEY=your_groq_key_here
KAIKU_SECRET_KEY=your_secret_key_here
TOKEN_EXPIRE_MINUTES=60
```

Generate a secret key:
```cmd
python -c "import secrets; print(secrets.token_hex(32))"
```

**4. Run**
```cmd
uvicorn backend.main:app --reload
```

**5. Open frontend**
Open `frontend/index.html` in your browser.

## Run tests
```cmd
pytest tests/
```

## Project structure

```
kaiku/
├── backend/
│   ├── __init__.py
│   ├── main.py          # FastAPI app + all endpoints
│   ├── auth.py          # JWT auth
│   ├── metrics.py       # Pre-written SQL (yield, OEE, PPM, defects, downtime, traceability)
│   ├── database.py      # SQLite connection
│   ├── nl_to_sql.py     # Groq: classifier + NL→SQL + knowledge answerer
│   └── schema.py        # DB schema context + Kaiku knowledge base + classifier prompt
├── data/
│   ├── synthetic_data.py
│   ├── kaiku.db         # gitignored
│   └── auth.db          # gitignored
├── frontend/
│   └── index.html       # Dashboard UI
├── tests/
│   └── test_api.py
├── .env                 # gitignored
├── .env.example
├── requirements.txt
└── README.md
```

## Design decisions

**Why two /ask modes?** Operational users ask two types of questions: live data queries ("what was yield last week?") and domain/feature questions ("what does OEE mean?"). Routing to the right handler gives better answers and avoids wasting SQL calls on conceptual questions.

**Why pre-written SQL for metrics?** OEE, yield, PPM, and downtime are asked constantly and must be fast and deterministic. Pre-written SQL never hallucinates a column name.

**Why SQLite for now?** Zero infrastructure for MVP. Production would connect to TimescaleDB or InfluxDB for real-time sensor telemetry from Kaiku's IoT layer.

**Why bcrypt==4.0.1?** Passlib's bcrypt backend is incompatible with bcrypt 5.x — pin to 4.0.1 to avoid the `__about__` AttributeError.

## Data model

- **machines** — 7 machines across 3 lines (dispenser, assembly, inspection, packaging)
- **production_runs** — 600 runs over 6 months with yield, OEE inputs, cycle time
- **defects** — per-run defects with type (void, misalign, contamination, flow_error, visual) and severity
- **maintenance_events** — preventive, corrective, predictive maintenance with downtime
- **barcode_scans** — input/in-process/output scan results for traceability
- **sensor_readings** — temperature, pressure, flow rate, humidity per run"# Lumin" 
