"""SQLite-backed CRM: leads, the conversation log, and scheduled follow-ups."""

import re
import sqlite3
from datetime import datetime, timezone

from voice_crm.exceptions import CRMError
from voice_crm.logger import get_logger

log = get_logger("crm")

LEAD_STATUSES = ("new", "contacted", "interested", "not_interested", "won", "lost")
CHANNELS = ("whatsapp", "call")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'new',
    hubspot_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    channel TEXT NOT NULL,
    direction TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    channel TEXT NOT NULL,
    reason TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_phone(raw):
    cleaned = re.sub(r"[\s\-()]", "", raw)
    if not re.fullmatch(r"\+\d{8,15}", cleaned):
        raise CRMError(f"Phone numbers must look like +9779812345678, got {raw!r}")
    return cleaned


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    # Databases created before HubSpot sync existed are missing these two columns.
    _add_missing_column(conn, "leads", "hubspot_id", "TEXT")
    _add_missing_column(conn, "interactions", "synced", "INTEGER NOT NULL DEFAULT 0")
    return conn


def _add_missing_column(conn, table, column, definition):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        with conn:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        log.info("Added column %s.%s to the database", table, column)


def _execute(conn, sql, params=()):
    try:
        with conn:
            return conn.execute(sql, params)
    except sqlite3.Error as exc:
        raise CRMError(f"Database write failed: {exc}") from exc


def _one(row):
    return dict(row) if row is not None else None


def add_lead(conn, name, phone, now=None):
    name = name.strip()
    if not name:
        raise CRMError("A lead needs a name")
    phone = normalize_phone(phone)
    if find_lead_by_phone(conn, phone) is not None:
        raise CRMError(f"A lead with phone {phone} already exists")

    stamp = now or utc_now()
    cursor = _execute(
        conn,
        "INSERT INTO leads (name, phone, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, phone, stamp, stamp),
    )
    log.info("Added lead id=%s phone=%s", cursor.lastrowid, phone)
    return cursor.lastrowid


def get_lead(conn, lead_id):
    return _one(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())


def find_lead_by_phone(conn, phone):
    row = conn.execute("SELECT * FROM leads WHERE phone = ?", (normalize_phone(phone),)).fetchone()
    return _one(row)


def list_leads(conn, status=None):
    if status is None:
        rows = conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
    else:
        if status not in LEAD_STATUSES:
            raise CRMError(f"Unknown status {status!r}")
        rows = conn.execute("SELECT * FROM leads WHERE status = ? ORDER BY id", (status,)).fetchall()
    return [dict(row) for row in rows]


def set_status(conn, lead_id, status):
    if status not in LEAD_STATUSES:
        raise CRMError(f"Unknown status {status!r}")
    cursor = _execute(
        conn,
        "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
        (status, utc_now(), lead_id),
    )
    if cursor.rowcount == 0:
        raise CRMError(f"No lead with id {lead_id}")
    log.info("Lead id=%s status -> %s", lead_id, status)


def log_interaction(conn, lead_id, channel, direction, body):
    if channel not in CHANNELS:
        raise CRMError(f"Unknown channel {channel!r}")
    _execute(
        conn,
        "INSERT INTO interactions (lead_id, channel, direction, body, created_at) VALUES (?, ?, ?, ?, ?)",
        (lead_id, channel, direction, body, utc_now()),
    )
    log.info("Logged %s/%s for lead id=%s", channel, direction, lead_id)


def list_interactions(conn, lead_id):
    rows = conn.execute(
        "SELECT * FROM interactions WHERE lead_id = ? ORDER BY created_at, id",
        (lead_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def schedule_followup(conn, lead_id, channel, reason, due_at=None):
    if channel not in CHANNELS:
        raise CRMError(f"Unknown channel {channel!r}")
    stamp = utc_now()
    cursor = _execute(
        conn,
        "INSERT INTO followups (lead_id, channel, reason, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (lead_id, channel, reason, due_at or stamp, stamp),
    )
    log.info("Scheduled %s follow-up id=%s for lead id=%s (%s)", channel, cursor.lastrowid, lead_id, reason)
    return cursor.lastrowid


def has_pending_followup(conn, lead_id, channel):
    row = conn.execute(
        "SELECT 1 FROM followups WHERE lead_id = ? AND channel = ? AND status = 'pending'",
        (lead_id, channel),
    ).fetchone()
    return row is not None


def pending_followups(conn, now_iso=None):
    rows = conn.execute(
        "SELECT * FROM followups WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id",
        (now_iso or utc_now(),),
    ).fetchall()
    return [dict(row) for row in rows]


def complete_followup(conn, followup_id):
    _execute(conn, "UPDATE followups SET status = 'done' WHERE id = ?", (followup_id,))


def set_hubspot_id(conn, lead_id, hubspot_id):
    _execute(conn, "UPDATE leads SET hubspot_id = ? WHERE id = ?", (hubspot_id, lead_id))


def unsynced_interactions(conn):
    rows = conn.execute("SELECT * FROM interactions WHERE synced = 0 ORDER BY created_at, id").fetchall()
    return [dict(row) for row in rows]


def mark_interaction_synced(conn, interaction_id):
    _execute(conn, "UPDATE interactions SET synced = 1 WHERE id = ?", (interaction_id,))
