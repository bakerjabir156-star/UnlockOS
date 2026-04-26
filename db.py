"""
db.py — UnlockOS SQLite History Store
Thread-safe helper for logging unlock results and reading history.
"""

import sqlite3
import threading
from datetime import datetime
from config import DB_PATH

_lock = threading.Lock()


def init_db() -> None:
    """Create tables if they don't exist."""
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unlock_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                model       TEXT    NOT NULL,
                serial_num  TEXT    NOT NULL,
                ios_version TEXT,
                chipset     TEXT,
                method      TEXT    NOT NULL,
                status      TEXT    NOT NULL,
                duration_s  REAL,
                notes       TEXT
            )
        """)
        conn.commit()
        conn.close()


def log_result(
    model: str,
    serial_num: str,
    status: str,          # "SUCCESS" | "FAILED" | "PARTIAL"
    method: str,          # "checkm8" | "mdm_bypass" | "proxy_hijack" | "mtk"
    ios_version: str = "",
    chipset: str = "",
    duration_s: float = 0.0,
    notes: str = "",
) -> int:
    """Insert a new unlock result row. Returns the new row id."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute(
            """INSERT INTO unlock_history
               (timestamp, model, serial_num, ios_version, chipset, method, status, duration_s, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (ts, model, serial_num, ios_version, chipset, method, status, duration_s, notes),
        )
        row_id = cursor.lastrowid
        conn.commit()
        conn.close()
    return row_id


def get_history(limit: int = 100) -> list[dict]:
    """Return the most recent unlock history rows as a list of dicts."""
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM unlock_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """Return aggregate statistics."""
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM unlock_history").fetchone()[0]
        success = conn.execute(
            "SELECT COUNT(*) FROM unlock_history WHERE status='SUCCESS'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM unlock_history WHERE status='FAILED'"
        ).fetchone()[0]
        conn.close()
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "rate": round((success / total * 100) if total else 0, 1),
    }
