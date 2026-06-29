"""
nl_to_sql.py — Two-mode Groq integration:
  1. question_type() — classifies question as 'data' or 'knowledge'
  2. nl_to_sql()     — generates SQL from natural language
  3. answer_from_knowledge() — answers domain/product questions
"""

import os
from groq import Groq
from backend.schema import DB_SCHEMA_CONTEXT, KAIKU_KNOWLEDGE, CLASSIFIER_PROMPT

GROQ_MODEL = "llama-3.1-8b-instant"

def _client():
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    return Groq(api_key=key)


def classify_question(question: str) -> str:
    """Returns 'data', 'knowledge', or 'unknown' if Groq unavailable."""
    client = _client()
    if not client:
        return "unknown"

    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=5,
            temperature=0.0,
            messages=[
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user",   "content": question},
            ],
        )
        result = resp.choices[0].message.content.strip().lower()
        return result if result in ("data", "knowledge") else "data"
    except Exception:
        return "data"  # safe fallback — try SQL path


def nl_to_sql(question: str, machine_id: str = None, period: str = "all") -> str | None:
    """Convert natural language to a SQLite SELECT query."""
    client = _client()
    if not client:
        return None

    user_prompt = f"Question: {question}"
    if machine_id:
        user_prompt += f"\nFilter to machine_id = '{machine_id}'"
    if period and period != "all":
        user_prompt += f"\nTime period: '{period}'"

    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=400,
            temperature=0.1,
            messages=[
                {"role": "system", "content": DB_SCHEMA_CONTEXT},
                {"role": "user",   "content": user_prompt},
            ],
        )
        sql = resp.choices[0].message.content.strip()
        # Strip markdown fences if model wraps in them
        sql = sql.replace("```sql", "").replace("```", "").strip()
        return sql if sql.upper().startswith("SELECT") else None
    except Exception:
        return None


def summarise_result(question: str, rows: list, context: dict | None = None) -> str | None:
    """Turn SQL result rows into a plain-English insight, enriched with defect/downtime context."""
    client = _client()
    if not client:
        return None

    context = context or {}
    defects = context.get("defects", [])
    downtime = context.get("downtime", [])

    context_block = ""
    if defects:
        context_block += f"""
Supporting data — defect breakdown for the top machine (last 30 days):
{defects}"""
    if downtime:
        context_block += f"""
Supporting data — maintenance/downtime for the top machine (last 30 days):
{downtime}"""

    prompt = f"""You are a manufacturing intelligence assistant for Kaiku by Ginolis — an IoT traceability platform used in diagnostics and biosensor production (LFD strips, biochips, microfluidic chips).

The user asked: "{question}"

Primary query results:
{rows}
{context_block}

Write 4 sentences exactly:
1. Headline: state the highest machine and its value (do not start with "The").
2. Range: state the lowest machine and its value from the full list — always the last row when sorted DESC. Then note the fleet spread (highest minus lowest).
3. Defects: if defect context provided, name the top 3 defect types with counts for the top machine.
4. Maintenance: if downtime context provided, state the event type, count, and total downtime minutes as plain fact — no recommendations, no "it's clear that", no "corrective action needed".

Rules:
- Only use numbers that appear in the data above — never invent figures
- Quote exact values (e.g. "8,240 PPM", "94.2% yield") — never say "average PPM" or "an average of", just "PPM"
- PPM is a defect rate — higher is worse. Never say a machine "outperformed" another if it has higher PPM
- Yield and OEE are the opposite — higher is better
- Do not mention SQL, "the data shows", or "the results indicate"
- Write like a shift supervisor giving a handover briefing
- When identifying the lowest value, always check ALL rows — it is the last row when sorted DESC or the first row when sorted ASC
- Never hedge with "it's likely", "it appears", "suggesting" — state findings as fact"""

    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=300,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None


def answer_from_knowledge(question: str) -> str | None:
    """Answer a domain/product question using Kaiku knowledge base."""
    client = _client()
    if not client:
        return None

    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=500,
            temperature=0.3,
            messages=[
                {"role": "system", "content": KAIKU_KNOWLEDGE},
                {"role": "user",   "content": question},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None