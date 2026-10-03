"""A journal made by an older version is upgraded in place, keeping its rows."""

from __future__ import annotations

import sqlite3

from conftest import rows
from tradejournal import db, paths


def test_a_version_1_journal_gains_the_new_columns_and_keeps_its_trades():
    path = paths.db_path()
    paths.ensure_private_dir(path.parent)
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('schema_version', '1');"
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, trade_ref TEXT NOT NULL UNIQUE, source TEXT NOT NULL,"
        " asset TEXT NOT NULL, direction TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
        "INSERT INTO trades (trade_ref, source, asset, direction, created_at, updated_at)"
        " VALUES ('OLD-1', 'legacy-csv', 'ABC', 'long', 'x', 'x');"
    )
    old.commit()
    old.close()
    c = db.connect()
    assert {"trip_key", "overrides_json"} <= set(db.columns(c, "trades"))
    assert rows(c, "select trade_ref from trades") == [("OLD-1",)]
    assert rows(c, "select value from meta where key = 'schema_version'") == [
        (str(db.SCHEMA_VERSION),)
    ]
    c.close()
