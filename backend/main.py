"""
main.py — Kaiku FastAPI backend.
All routes mirror Lumin's structure; domain is now manufacturing intelligence.
"""

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from backend.auth import init_auth_db

from backend.auth import (
    router as auth_router,
    get_current_user,
)
from backend.database import get_conn
from backend import metrics as m
from backend.nl_to_sql import classify_question, nl_to_sql, answer_from_knowledge, summarise_result

app = FastAPI(title="Kaiku Intelligence API", version="1.0.0")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "kaiku-api"}


# ── Machines ───────────────────────────────────────────────────────────────────

@app.get("/machines")
def list_machines(user=Depends(get_current_user)):
    conn = get_conn()
    cur = conn.execute("SELECT * FROM machines ORDER BY machine_id")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Metrics (hardcoded SQL, fast & deterministic) ─────────────────────────────

def _params(request: Request):
    machine_id = request.query_params.get("machine_id")
    period = request.query_params.get("period", "all")
    return machine_id, period


@app.get("/metrics/yield")
def yield_metric(request: Request, user=Depends(get_current_user)):
    machine_id, period = _params(request)
    return m.get_yield(get_conn(), machine_id, period)


@app.get("/metrics/oee")
def oee_metric(request: Request, user=Depends(get_current_user)):
    machine_id, period = _params(request)
    return m.get_oee(get_conn(), machine_id, period)


@app.get("/metrics/ppm")
def ppm_metric(request: Request, user=Depends(get_current_user)):
    machine_id, period = _params(request)
    return m.get_ppm(get_conn(), machine_id, period)


@app.get("/metrics/cycle-time")
def cycle_time_metric(request: Request, user=Depends(get_current_user)):
    machine_id, period = _params(request)
    return m.get_cycle_time(get_conn(), machine_id, period)


@app.get("/metrics/defects")
def defects_metric(request: Request, user=Depends(get_current_user)):
    machine_id, period = _params(request)
    return m.get_defects(get_conn(), machine_id, period)

@app.on_event("startup")
def startup():
    init_auth_db()


@app.get("/metrics/downtime")
def downtime_metric(request: Request, user=Depends(get_current_user)):
    machine_id, period = _params(request)
    return m.get_downtime(get_conn(), machine_id, period)


@app.get("/metrics/traceability")
def traceability(barcode: str, user=Depends(get_current_user)):
    if not barcode:
        raise HTTPException(status_code=400, detail="barcode parameter required")
    results = m.get_traceability(get_conn(), barcode)
    if not results:
        raise HTTPException(status_code=404, detail=f"No records found for barcode {barcode}")
    return results


# ── /ask — Option B: classify → route to data or knowledge ────────────────────

class AskRequest(BaseModel):
    question: str
    machine_id: Optional[str] = None
    period: Optional[str] = "all"


class SummariseRequest(BaseModel):
    question: str
    rows: list


def _fetch_context(conn, machine_id: str) -> dict:
    """Fetch defect breakdown and downtime for a given machine to enrich the summary."""
    context = {}
    try:
        cur = conn.execute("""
            SELECT d.defect_type, d.severity, COUNT(*) as count
            FROM defects d
            JOIN production_runs r ON d.run_id = r.run_id
            WHERE d.machine_id = ?
              AND r.started_at >= DATE('now', '-30 days')
            GROUP BY d.defect_type, d.severity
            ORDER BY count DESC
            LIMIT 5
        """, (machine_id,))
        cols = [c[0] for c in cur.description]
        context["defects"] = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        context["defects"] = []

    try:
        cur = conn.execute("""
            SELECT event_type, COUNT(*) as events, SUM(downtime_minutes) as total_downtime_min
            FROM maintenance_events
            WHERE machine_id = ?
              AND started_at >= DATE('now', '-30 days')
            GROUP BY event_type
            ORDER BY total_downtime_min DESC
        """, (machine_id,))
        cols = [c[0] for c in cur.description]
        context["downtime"] = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        context["downtime"] = []

    return context


def _top_machine_id(conn, rows: list) -> str | None:
    """Resolve the top machine ID from the first result row."""
    if not rows:
        return None
    first = rows[0]
    if "machine_id" in first:
        return first["machine_id"]
    if "name" in first:
        try:
            cur = conn.execute(
                "SELECT machine_id FROM machines WHERE name = ?", (first["name"],)
            )
            row = cur.fetchone()
            return row[0] if row else None
        except Exception:
            return None
    return None


@app.post("/ask/summarise")
@limiter.limit("10/minute")
def ask_summarise(request: Request, body: SummariseRequest, user=Depends(get_current_user)):
    if not body.rows:
        return {"summary": None}

    conn = get_conn()
    machine_id = _top_machine_id(conn, body.rows)
    context = _fetch_context(conn, machine_id) if machine_id else {}

    summary = summarise_result(body.question, body.rows, context)
    return {"summary": summary}


# ── /ask/related-data — fetch live data relevant to a knowledge answer ─────────

class RelatedDataRequest(BaseModel):
    question: str

# Maps knowledge topic keywords → deterministic SQL + a display label
KNOWLEDGE_DATA_MAP = [
    (["defect", "defect type", "void", "misalign", "contamination", "flow error", "visual"],
     "Defect breakdown (all time)",
     """SELECT d.defect_type, d.severity, COUNT(*) as count
        FROM defects d
        GROUP BY d.defect_type, d.severity
        ORDER BY count DESC"""),

    (["oee", "overall equipment effectiveness"],
     "OEE by machine (last 30 days)",
     """SELECT m.name, ROUND(AVG(CAST(r.units_passed AS REAL)/r.units_planned)*100,1) as oee_pct
        FROM production_runs r JOIN machines m USING(machine_id)
        WHERE r.started_at >= DATE('now','-30 days')
        GROUP BY r.machine_id ORDER BY oee_pct DESC"""),

    (["yield", "strip yield"],
     "Yield by machine (last 30 days)",
     """SELECT m.name, ROUND(AVG(CAST(r.units_passed AS REAL)/r.units_produced)*100,1) as yield_pct
        FROM production_runs r JOIN machines m USING(machine_id)
        WHERE r.started_at >= DATE('now','-30 days')
        GROUP BY r.machine_id ORDER BY yield_pct DESC"""),

    (["ppm", "parts per million"],
     "PPM by machine (last 30 days)",
     """SELECT m.name, ROUND(AVG(r.ppm),0) as avg_ppm
        FROM production_runs r JOIN machines m USING(machine_id)
        WHERE r.started_at >= DATE('now','-30 days')
        GROUP BY r.machine_id ORDER BY avg_ppm DESC"""),

    (["downtime", "maintenance", "preventive", "corrective", "predictive"],
     "Maintenance events (last 30 days)",
     """SELECT m.name, e.event_type, COUNT(*) as events, SUM(e.downtime_minutes) as total_downtime_min
        FROM maintenance_events e JOIN machines m USING(machine_id)
        WHERE e.started_at >= DATE('now','-30 days')
        GROUP BY e.machine_id, e.event_type ORDER BY total_downtime_min DESC"""),

    (["cycle time", "cycle"],
     "Cycle time by machine (last 30 days)",
     """SELECT m.name, ROUND(AVG(r.cycle_time_seconds),1) as avg_cycle_time_s
        FROM production_runs r JOIN machines m USING(machine_id)
        WHERE r.started_at >= DATE('now','-30 days')
        GROUP BY r.machine_id ORDER BY avg_cycle_time_s ASC"""),
]


@app.post("/ask/related-data")
def ask_related_data(body: RelatedDataRequest, user=Depends(get_current_user)):
    q = body.question.lower()
    for keywords, label, sql in KNOWLEDGE_DATA_MAP:
        if any(kw in q for kw in keywords):
            try:
                conn = get_conn()
                cur = conn.execute(sql)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                return {"label": label, "rows": rows}
            except Exception:
                return {"label": None, "rows": []}
    return {"label": None, "rows": []}


@app.post("/ask")
@limiter.limit("10/minute")
def ask(request: Request, body: AskRequest, user=Depends(get_current_user)):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question cannot be empty")

    # Step 1: classify
    q_type = classify_question(question)

    if q_type == "unknown":
        return {
            "question": question,
            "type": "unknown",
            "fallback": True,
            "answer": "Groq API key not configured. Set GROQ_API_KEY in your .env to enable this feature.",
        }

    # Step 2a: knowledge question → answer from product knowledge base
    if q_type == "knowledge":
        answer = answer_from_knowledge(question)
        if not answer:
            return {
                "question": question,
                "type": "knowledge",
                "fallback": True,
                "answer": "Could not retrieve an answer. Please try again.",
            }
        return {
            "question": question,
            "type": "knowledge",
            "answer": answer,
            "fallback": False,
        }

    # Step 2b: data question → NL→SQL→result
    sql = nl_to_sql(question, body.machine_id, body.period)
    if not sql:
        return {
            "question": question,
            "type": "data",
            "fallback": True,
            "answer": "Could not generate a valid SQL query for that question.",
        }

    try:
        conn = get_conn()
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return {
            "question": question,
            "type": "data",
            "sql": sql,
            "rows": rows,
            "fallback": False,
        }
    except Exception as e:
        return {
            "question": question,
            "type": "data",
            "fallback": True,
            "answer": f"SQL execution error: {str(e)}",
            "sql": sql,
        }