"""
alert_store.py
===============
Persists every anomaly the agent has ever raised, and lets a human mark
one as a false positive. If the same metric keeps getting marked noisy
on the same weekday, the store starts telling the detector to raise its
bar for that specific combination going forward.

This is what turns the agent from "fires the same rule forever" into
something that behaves a little more like a teammate: it remembers what
you told it last time.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime


SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    weekday TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    baseline_mean REAL NOT NULL,
    baseline_std REAL NOT NULL,
    z_score REAL NOT NULL,
    direction TEXT NOT NULL,
    severity TEXT NOT NULL,
    root_cause TEXT,
    status TEXT NOT NULL DEFAULT 'new',   -- new | reviewed | false_positive
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_unique_date_metric ON alerts(date, metric);

CREATE TABLE IF NOT EXISTS relaxed_combos (
    metric TEXT NOT NULL,
    weekday TEXT NOT NULL,
    relax_multiplier REAL NOT NULL,
    false_positive_count INTEGER NOT NULL,
    PRIMARY KEY (metric, weekday)
);
"""


@contextmanager
def _connect(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


def record_alert(db_path: str, anomaly, root_cause: str = None) -> int:
    with _connect(db_path) as conn:
        date = anomaly.date.strftime("%Y-%m-%d")
        weekday = anomaly.date.strftime("%A")
        conn.execute(
            """INSERT OR IGNORE INTO alerts
               (date, weekday, metric, value, baseline_mean, baseline_std,
                z_score, direction, severity, root_cause, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                date, weekday, anomaly.metric, float(anomaly.value),
                float(anomaly.baseline_mean), float(anomaly.baseline_std),
                float(anomaly.z_score), anomaly.direction, anomaly.severity,
                root_cause, "new", datetime.now().isoformat(timespec="seconds"),
            ),
        )
        row = conn.execute(
            "SELECT id FROM alerts WHERE date = ? AND metric = ?", (date, anomaly.metric)
        ).fetchone()
        return row[0]


def mark_feedback(db_path: str, alert_id: int, status: str,
                   false_positive_threshold: int = 2,
                   relax_multiplier: float = 1.3) -> None:
    """
    status: 'reviewed' or 'false_positive'.
    If a metric+weekday combo crosses the false-positive threshold,
    it gets registered in relaxed_combos so future runs raise the bar
    for that exact combination.
    """
    if status not in ("reviewed", "false_positive"):
        raise ValueError("status must be 'reviewed' or 'false_positive'")

    with _connect(db_path) as conn:
        row = conn.execute("SELECT metric, weekday FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if row is None:
            raise ValueError(f"No alert with id {alert_id}")
        metric, weekday = row

        conn.execute("UPDATE alerts SET status = ? WHERE id = ?", (status, alert_id))

        if status == "false_positive":
            fp_count = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE metric = ? AND weekday = ? AND status = 'false_positive'",
                (metric, weekday),
            ).fetchone()[0]

            if fp_count >= false_positive_threshold:
                conn.execute(
                    """INSERT INTO relaxed_combos (metric, weekday, relax_multiplier, false_positive_count)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(metric, weekday) DO UPDATE SET
                         false_positive_count = excluded.false_positive_count,
                         relax_multiplier = excluded.relax_multiplier""",
                    (metric, weekday, relax_multiplier, fp_count),
                )


def get_relaxed_combos(db_path: str) -> dict:
    """Returns {(metric, weekday): relax_multiplier} for combos the feedback loop has flagged as noisy."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT metric, weekday, relax_multiplier FROM relaxed_combos").fetchall()
    return {(m, w): mult for m, w, mult in rows}


def get_alert_history(db_path: str, limit: int = 50) -> list:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, date, weekday, metric, value, z_score, direction, severity, root_cause, status "
            "FROM alerts ORDER BY date DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    cols = ["id", "date", "weekday", "metric", "value", "z_score", "direction", "severity", "root_cause", "status"]
    return [dict(zip(cols, r)) for r in rows]
