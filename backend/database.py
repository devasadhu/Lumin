import sqlite3
import os

from data.synthetic_data import DB_PATH

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "kaiku.db")

def run_query(sql: str, params: list = []) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        first_word = sql.strip().upper().split()[0]
        if first_word not in ("SELECT", "WITH"):
            return {"columns": [], "rows": [], "error": "Only SELECT queries are allowed."}
        c.execute(sql, params)
        rows = c.fetchall()
        columns = [d[0] for d in c.description] if c.description else []
        return {"columns": columns, "rows": [dict(r) for r in rows], "error": None}
    except Exception as e:
        return {"columns": [], "rows": [], "error": str(e)}
    finally:
        conn.close()

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn