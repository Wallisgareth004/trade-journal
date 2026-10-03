"""The journal file: one SQLite database, the single source of truth.

~/Library/Application Support/trade-journal/journal.sqlite, owner-only (600)
in an owner-only folder (700). Not iCloud, not git.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from . import paths

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rulebook_versions (
  version TEXT PRIMARY KEY,
  effective_from TEXT NOT NULL,
  placeholder INTEGER NOT NULL,
  numbers_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  file_name TEXT,
  imported_at TEXT NOT NULL,
  rows INTEGER NOT NULL DEFAULT 0,
  new_orders INTEGER NOT NULL DEFAULT 0,
  new_trades INTEGER NOT NULL DEFAULT 0,
  notes_json TEXT
);

CREATE TABLE IF NOT EXISTS checklists (
  id INTEGER PRIMARY KEY,
  filled_at TEXT NOT NULL,
  asset TEXT,
  direction TEXT CHECK (direction IN ('long', 'short')),
  setup TEXT,
  answers_json TEXT NOT NULL,
  auto_json TEXT NOT NULL,
  emotion_word TEXT,
  confidence INTEGER CHECK (confidence BETWEEN 1 AND 5),
  verdict TEXT NOT NULL CHECK (verdict IN ('go', 'no-trade', 'taken-anyway')),
  outcome TEXT CHECK (outcome IN ('taken', 'skipped')),
  matched_trade_id INTEGER REFERENCES trades(id),
  rulebook_version TEXT
);

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY,
  trade_ref TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL CHECK (source IN ('legacy-csv', 'tradingview-csv', 'manual')),
  source_ref TEXT,
  asset TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
  setup TEXT,
  placed_time TEXT,
  entry_time TEXT,
  exit_time TEXT,
  entry REAL,
  stop REAL,
  target REAL,
  exit REAL,
  size REAL,
  leverage TEXT,
  point_value REAL,
  fees REAL,
  balance_at_entry REAL,
  risk_currency REAL,
  risk_pct REAL,
  result_currency REAL,
  result_r REAL,
  planned_rr REAL,
  exit_reason TEXT,
  stop_placed TEXT CHECK (stop_placed IN ('with-order', 'after-entry', 'none')),
  stop_moved_further INTEGER,
  session_window TEXT,
  weekday TEXT,
  emotion_word TEXT,
  confidence INTEGER,
  mistake TEXT,
  lesson TEXT,
  note TEXT,
  checklist_id INTEGER REFERENCES checklists(id),
  rulebook_version TEXT,
  grade TEXT CHECK (grade IN ('A', 'B', 'C')),
  grade_reasons_json TEXT,
  followed_rules INTEGER,
  journaled_at TEXT,
  flags_json TEXT,
  legacy_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY,
  order_id TEXT NOT NULL UNIQUE,
  import_id INTEGER REFERENCES imports(id),
  trade_id INTEGER REFERENCES trades(id),
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  type TEXT NOT NULL,
  qty REAL NOT NULL,
  limit_price REAL,
  stop_price REAL,
  fill_price REAL,
  status TEXT NOT NULL,
  commission REAL,
  placing_time TEXT,
  closing_time TEXT,
  level_id TEXT,
  leverage TEXT,
  raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS balances (
  id INTEGER PRIMARY KEY,
  time TEXT NOT NULL,
  balance_before REAL,
  balance_after REAL,
  realized_pnl REAL,
  action TEXT NOT NULL,
  import_id INTEGER REFERENCES imports(id),
  UNIQUE (time, action)
);

CREATE TABLE IF NOT EXISTS screenshots (
  id INTEGER PRIMARY KEY,
  trade_id INTEGER NOT NULL REFERENCES trades(id),
  phase TEXT NOT NULL CHECK (phase IN ('entry', 'exit', 'other')),
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  added_at TEXT NOT NULL,
  UNIQUE (trade_id, sha256)
);

CREATE TABLE IF NOT EXISTS edits (
  id INTEGER PRIMARY KEY,
  trade_id INTEGER NOT NULL REFERENCES trades(id),
  field TEXT NOT NULL,
  old TEXT,
  new TEXT,
  edited_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  week TEXT PRIMARY KEY,
  answers_json TEXT NOT NULL,
  saved_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect(path=None) -> sqlite3.Connection:
    """Open (or create, owner-only) the journal and make sure the schema exists."""
    path = paths.check_private(path or paths.db_path())
    paths.ensure_private_dir(path.parent)
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    os.chmod(path, 0o600)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1')"
    )
    _upgrade(conn)
    conn.commit()
    return conn


# Each step takes the journal from the version before it. New journals start
# at version 1 and walk the same steps, so old and new files end up alike.
UPGRADES = {
    2: [
        "ALTER TABLE trades ADD COLUMN trip_key TEXT",
        "ALTER TABLE trades ADD COLUMN overrides_json TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS trades_trip_key ON trades(trip_key)",
    ],
}


def _upgrade(conn: sqlite3.Connection) -> None:
    current = int(
        conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[
            0
        ]
    )
    for version in range(current + 1, SCHEMA_VERSION + 1):
        with conn:
            for sql in UPGRADES[version]:
                conn.execute(sql)
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(version),),
            )


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def insert_trade(conn: sqlite3.Connection, fields: dict, *, commit: bool = True) -> int:
    """Insert one trade. An unknown field is an error, never silently dropped."""
    known = set(columns(conn, "trades"))
    unknown = sorted(set(fields) - known)
    if unknown:
        raise KeyError(f"not a trades column: {', '.join(unknown)}")
    stamp = now_iso()
    row = {"created_at": stamp, "updated_at": stamp, **fields}
    names = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    cur = conn.execute(
        f"INSERT INTO trades ({names}) VALUES ({marks})", tuple(row.values())
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def update_trade(
    conn: sqlite3.Connection, trade_id: int, fields: dict, *, commit: bool = True
) -> None:
    known = set(columns(conn, "trades"))
    unknown = sorted(set(fields) - known)
    if unknown:
        raise KeyError(f"not a trades column: {', '.join(unknown)}")
    if not fields:
        return
    row = {**fields, "updated_at": now_iso()}
    sets = ", ".join(f"{k} = ?" for k in row)
    conn.execute(f"UPDATE trades SET {sets} WHERE id = ?", (*row.values(), trade_id))
    if commit:
        conn.commit()
