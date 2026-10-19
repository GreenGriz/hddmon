"""SQLite: схема, миграции, CRUD."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .models import Event, Snapshot, iso, utcnow

SCHEMA_VERSION = 1

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
  id INTEGER PRIMARY KEY,
  dev_node TEXT NOT NULL,
  model TEXT,
  model_family TEXT,
  serial TEXT UNIQUE,
  transport TEXT,
  protocol TEXT,
  capacity_bytes INTEGER,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  present INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY,
  device_id INTEGER NOT NULL REFERENCES devices(id),
  taken_at TEXT NOT NULL,
  smart_passed INTEGER,
  temperature INTEGER,
  power_on_hours INTEGER,
  reallocated INTEGER,
  pending INTEGER,
  uncorrectable INTEGER,
  reported_uncorrect INTEGER,
  end_to_end_error INTEGER,
  command_timeout INTEGER,
  load_cycle INTEGER,
  power_cycle INTEGER,
  grown_defects INTEGER,
  selftest_status TEXT,
  verdict TEXT,
  score INTEGER,
  reasons TEXT,
  raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_device_taken ON snapshots(device_id, taken_at);

CREATE TABLE IF NOT EXISTS attributes (
  id INTEGER PRIMARY KEY,
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  device_id INTEGER NOT NULL,
  attr_id INTEGER,
  name TEXT NOT NULL,
  raw_value INTEGER,
  normalized INTEGER,
  worst INTEGER,
  failed INTEGER
);
CREATE INDEX IF NOT EXISTS idx_attributes_snapshot ON attributes(snapshot_id);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  device_id INTEGER,
  at TEXT NOT NULL,
  type TEXT NOT NULL,
  severity TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);
"""


def connect(db_path: str, read_only: bool = False) -> sqlite3.Connection:
    path = Path(db_path)
    if read_only:
        if not path.is_file():
            raise FileNotFoundError(f"БД не найдена: {path}; запусти `hddmon collect`")
        uri = path.resolve().as_uri()
        wal = path.with_name(path.name + "-wal")
        has_wal = wal.is_file() and wal.stat().st_size > 0
        if has_wal:
            candidates = [f"{uri}?mode=ro", f"{uri}?mode=ro&immutable=1"]
        else:
            candidates = [f"{uri}?mode=ro&immutable=1", f"{uri}?mode=ro"]
        last_error: sqlite3.Error | None = None
        for candidate in candidates:
            conn = None
            try:
                conn = sqlite3.connect(candidate, uri=True)
                conn.row_factory = sqlite3.Row
                conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
                return conn
            except sqlite3.Error as e:
                last_error = e
                if conn is not None:
                    conn.close()
        if last_error is not None:
            raise last_error
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
    row = cur.fetchone()
    version = int(row["value"]) if row else 0
    if version < 1:
        conn.executescript(SCHEMA_V1)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()


def upsert_device(conn: sqlite3.Connection, snap: Snapshot) -> int:
    now = iso(snap.taken_at)
    serial = snap.device.serial or f"node:{snap.device.dev_node}"
    row = conn.execute("SELECT id, first_seen FROM devices WHERE serial=?", (serial,)).fetchone()
    if row:
        conn.execute(
            """UPDATE devices SET dev_node=?, model=?, model_family=?, transport=?,
               protocol=?, capacity_bytes=?, last_seen=?, present=1 WHERE id=?""",
            (snap.device.dev_node, snap.device.model, snap.device.model_family,
             snap.device.transport, snap.device.protocol, snap.device.capacity_bytes,
             now, row["id"]),
        )
        return int(row["id"])
    cur = conn.execute(
        """INSERT INTO devices(dev_node, model, model_family, serial, transport, protocol,
           capacity_bytes, first_seen, last_seen, present)
           VALUES(?,?,?,?,?,?,?,?,?,1)""",
        (snap.device.dev_node, snap.device.model, snap.device.model_family, serial,
         snap.device.transport, snap.device.protocol, snap.device.capacity_bytes, now, now),
    )
    return int(cur.lastrowid)


def insert_snapshot(
    conn: sqlite3.Connection,
    device_id: int,
    snap: Snapshot,
    verdict: str,
    score: int,
    reasons: list[str],
    store_raw: bool,
) -> int:
    cur = conn.execute(
        """INSERT INTO snapshots(device_id, taken_at, smart_passed, temperature,
           power_on_hours, reallocated, pending, uncorrectable, reported_uncorrect,
           end_to_end_error, command_timeout, load_cycle, power_cycle, grown_defects,
           selftest_status, verdict, score, reasons, raw_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            device_id, iso(snap.taken_at),
            None if snap.smart_passed is None else int(snap.smart_passed),
            snap.temperature, snap.power_on_hours, snap.reallocated, snap.pending,
            snap.uncorrectable, snap.reported_uncorrect, snap.end_to_end_error,
            snap.command_timeout, snap.load_cycle, snap.power_cycle, snap.grown_defects,
            snap.selftest_status, verdict, score, json.dumps(reasons, ensure_ascii=False),
            snap.raw_json if store_raw else None,
        ),
    )
    snapshot_id = int(cur.lastrowid)
    conn.executemany(
        """INSERT INTO attributes(snapshot_id, device_id, attr_id, name, raw_value,
           normalized, worst, failed) VALUES(?,?,?,?,?,?,?,?)""",
        [
            (snapshot_id, device_id, a.attr_id, a.name, a.raw_value,
             a.normalized, a.worst, None if a.failed is None else int(a.failed))
            for a in snap.attributes
        ],
    )
    return snapshot_id


def get_last_snapshot(conn: sqlite3.Connection, device_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM snapshots WHERE device_id=? ORDER BY taken_at DESC, id DESC LIMIT 1",
        (device_id,),
    ).fetchone()


def get_history(conn: sqlite3.Connection, device_id: int, days: int | None = None,
                limit: int = 500) -> list[sqlite3.Row]:
    q = "SELECT * FROM snapshots WHERE device_id=?"
    params: list[Any] = [device_id]
    if days:
        since = iso(utcnow() - timedelta(days=days))
        q += " AND taken_at>=?"
        params.append(since)
    q += " ORDER BY taken_at DESC, id DESC LIMIT ?"
    params.append(limit)
    return list(conn.execute(q, params))


def list_devices(conn: sqlite3.Connection, present_only: bool = False) -> list[sqlite3.Row]:
    q = "SELECT * FROM devices"
    if present_only:
        q += " WHERE present=1"
    q += " ORDER BY dev_node"
    return list(conn.execute(q))


def find_device(conn: sqlite3.Connection, ref: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM devices WHERE serial=? OR dev_node=? OR model=? ORDER BY present DESC LIMIT 1",
        (ref, ref, ref),
    ).fetchone()


def latest_snapshots(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT s.*, d.dev_node, d.model, d.serial, d.present
           FROM snapshots s
           JOIN devices d ON d.id = s.device_id
           WHERE s.id = (SELECT MAX(id) FROM snapshots WHERE device_id = s.device_id)
           ORDER BY d.dev_node"""
    ))


def device_id_by_serial(conn: sqlite3.Connection, serial: str) -> int | None:
    row = conn.execute("SELECT id FROM devices WHERE serial=?", (serial,)).fetchone()
    return int(row["id"]) if row else None


def mark_missing(conn: sqlite3.Connection, seen_device_ids: Iterable[int],
                 unreadable_nodes: Iterable[str] = ()) -> list[sqlite3.Row]:
    seen = set(seen_device_ids)
    nodes = set(unreadable_nodes)
    rows = list(conn.execute("SELECT * FROM devices WHERE present=1"))
    missing = [r for r in rows if r["id"] not in seen and r["dev_node"] not in nodes]
    for r in missing:
        conn.execute("UPDATE devices SET present=0 WHERE id=?", (r["id"],))
    return missing


def insert_event(conn: sqlite3.Connection, ev: Event) -> int:
    cur = conn.execute(
        "INSERT INTO events(device_id, at, type, severity, message) VALUES(?,?,?,?,?)",
        (ev.device_id, iso(ev.at), ev.type, ev.severity, ev.message),
    )
    return int(cur.lastrowid)


def recent_events(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM events ORDER BY at DESC, id DESC LIMIT ?", (limit,)))


def critical_series(conn: sqlite3.Connection, device_id: int, column: str) -> list[tuple[datetime, int]]:
    """Ряд (время, значение) по критичному столбцу, только непустые значения."""
    allowed = {"reallocated", "pending", "uncorrectable", "reported_uncorrect",
               "end_to_end_error", "command_timeout", "grown_defects"}
    if column not in allowed:
        raise ValueError(f"column not allowed: {column}")
    rows = conn.execute(
        f"SELECT taken_at, {column} AS v FROM snapshots WHERE device_id=? AND {column} IS NOT NULL "
        f"ORDER BY taken_at, id",
        (device_id,),
    )
    out = []
    for r in rows:
        out.append((datetime.fromisoformat(r["taken_at"]), int(r["v"])))
    return out
