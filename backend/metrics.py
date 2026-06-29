"""
metrics.py — Pre-written SQL queries for Kaiku's core manufacturing KPIs.
All queries are read-only SELECT statements.
"""

import sqlite3
from typing import Optional


def _period_filter(col: str, period: Optional[str]) -> str:
    if not period or period == "all":
        return ""
    if period == "today":
        return f"AND DATE({col}) = DATE('now')"
    if period == "week":
        return f"AND {col} >= DATE('now', '-7 days')"
    if period == "month":
        return f"AND {col} >= DATE('now', '-30 days')"
    # YYYY-MM format
    if len(period) == 7 and period[4] == "-":
        return f"AND {col} LIKE '{period}%'"
    return ""


def _machine_filter(machine_id: Optional[str]) -> str:
    return f"AND pr.machine_id = '{machine_id}'" if machine_id else ""


# ── Yield ──────────────────────────────────────────────────────────────────────

YIELD_SQL = """
SELECT
    m.machine_id,
    m.name,
    m.location,
    COUNT(pr.run_id)                                              AS total_runs,
    SUM(pr.units_produced)                                        AS units_produced,
    SUM(pr.units_passed)                                          AS units_passed,
    ROUND(
        100.0 * SUM(pr.units_passed) / NULLIF(SUM(pr.units_produced), 0), 2
    )                                                             AS yield_pct
FROM machines m
LEFT JOIN production_runs pr ON m.machine_id = pr.machine_id
    AND pr.status = 'completed'
    {period_filter}
WHERE 1=1 {machine_filter}
GROUP BY m.machine_id, m.name, m.location
ORDER BY yield_pct DESC;
"""

def get_yield(conn: sqlite3.Connection, machine_id=None, period=None):
    sql = YIELD_SQL.format(
        period_filter=_period_filter("pr.started_at", period),
        machine_filter=_machine_filter(machine_id),
    )
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── OEE ───────────────────────────────────────────────────────────────────────

OEE_SQL = """
SELECT
    m.machine_id,
    m.name,
    m.type,
    COUNT(pr.run_id)                                              AS total_runs,
    SUM(pr.units_planned)                                         AS units_planned,
    SUM(pr.units_passed)                                          AS units_passed,
    ROUND(
        100.0 * SUM(pr.units_passed) / NULLIF(SUM(pr.units_planned), 0), 2
    )                                                             AS oee_pct
FROM machines m
LEFT JOIN production_runs pr ON m.machine_id = pr.machine_id
    AND pr.status = 'completed'
    {period_filter}
WHERE 1=1 {machine_filter}
GROUP BY m.machine_id, m.name, m.type
ORDER BY oee_pct DESC;
"""

def get_oee(conn: sqlite3.Connection, machine_id=None, period=None):
    sql = OEE_SQL.format(
        period_filter=_period_filter("pr.started_at", period),
        machine_filter=_machine_filter(machine_id),
    )
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── PPM ───────────────────────────────────────────────────────────────────────

PPM_SQL = """
SELECT
    m.machine_id,
    m.name,
    SUM(pr.units_produced)                                        AS units_produced,
    SUM(pr.units_produced - pr.units_passed)                      AS total_defects,
    ROUND(
        1000000.0 * SUM(pr.units_produced - pr.units_passed)
        / NULLIF(SUM(pr.units_produced), 0), 0
    )                                                             AS ppm
FROM machines m
LEFT JOIN production_runs pr ON m.machine_id = pr.machine_id
    AND pr.status = 'completed'
    {period_filter}
WHERE 1=1 {machine_filter}
GROUP BY m.machine_id, m.name
ORDER BY ppm DESC;
"""

def get_ppm(conn: sqlite3.Connection, machine_id=None, period=None):
    sql = PPM_SQL.format(
        period_filter=_period_filter("pr.started_at", period),
        machine_filter=_machine_filter(machine_id),
    )
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Cycle time ────────────────────────────────────────────────────────────────

CYCLE_TIME_SQL = """
SELECT
    m.machine_id,
    m.name,
    m.type,
    ROUND(AVG(pr.cycle_time_seconds), 2)                         AS avg_cycle_time_s,
    ROUND(MIN(pr.cycle_time_seconds), 2)                         AS min_cycle_time_s,
    ROUND(MAX(pr.cycle_time_seconds), 2)                         AS max_cycle_time_s
FROM machines m
JOIN production_runs pr ON m.machine_id = pr.machine_id
    AND pr.status = 'completed'
    AND pr.cycle_time_seconds IS NOT NULL
    {period_filter}
WHERE 1=1 {machine_filter}
GROUP BY m.machine_id, m.name, m.type
ORDER BY avg_cycle_time_s;
"""

def get_cycle_time(conn: sqlite3.Connection, machine_id=None, period=None):
    sql = CYCLE_TIME_SQL.format(
        period_filter=_period_filter("pr.started_at", period),
        machine_filter=_machine_filter(machine_id),
    )
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Defects ───────────────────────────────────────────────────────────────────

DEFECTS_SQL = """
SELECT
    m.machine_id,
    m.name,
    d.defect_type,
    d.severity,
    COUNT(d.defect_id)                                            AS defect_count
FROM defects d
JOIN machines m ON d.machine_id = m.machine_id
WHERE 1=1
    {period_filter}
    {machine_filter}
GROUP BY m.machine_id, m.name, d.defect_type, d.severity
ORDER BY defect_count DESC;
"""

def get_defects(conn: sqlite3.Connection, machine_id=None, period=None):
    pf = _period_filter("d.detected_at", period)
    mf = f"AND d.machine_id = '{machine_id}'" if machine_id else ""
    sql = DEFECTS_SQL.format(period_filter=pf, machine_filter=mf)
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Downtime ──────────────────────────────────────────────────────────────────

DOWNTIME_SQL = """
SELECT
    m.machine_id,
    m.name,
    me.event_type,
    COUNT(me.event_id)                                            AS event_count,
    SUM(me.downtime_minutes)                                      AS total_downtime_min,
    ROUND(AVG(me.downtime_minutes), 1)                            AS avg_downtime_min
FROM maintenance_events me
JOIN machines m ON me.machine_id = m.machine_id
WHERE 1=1
    {period_filter}
    {machine_filter}
GROUP BY m.machine_id, m.name, me.event_type
ORDER BY total_downtime_min DESC;
"""

def get_downtime(conn: sqlite3.Connection, machine_id=None, period=None):
    pf = _period_filter("me.started_at", period)
    mf = f"AND me.machine_id = '{machine_id}'" if machine_id else ""
    sql = DOWNTIME_SQL.format(period_filter=pf, machine_filter=mf)
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Traceability (barcode lookup) ──────────────────────────────────────────────

TRACEABILITY_SQL = """
SELECT
    bs.barcode,
    bs.scan_result,
    bs.stage,
    bs.scanned_at,
    m.machine_id,
    m.name          AS machine_name,
    m.location,
    pr.run_id,
    pr.batch_id,
    pr.product_code,
    pr.operator_id,
    pr.status       AS run_status
FROM barcode_scans bs
JOIN machines m ON bs.machine_id = m.machine_id
JOIN production_runs pr ON bs.run_id = pr.run_id
WHERE bs.barcode = ?
ORDER BY bs.scanned_at;
"""

def get_traceability(conn: sqlite3.Connection, barcode: str):
    cur = conn.execute(TRACEABILITY_SQL, (barcode,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]