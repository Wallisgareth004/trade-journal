"""The journal file: private, durable, and only where it may live."""

from __future__ import annotations

from pathlib import Path

import pytest

from tradejournal import db, paths


def test_a_new_journal_is_owner_only(private_dirs):
    data, _ = private_dirs
    c = db.connect()
    c.close()
    assert (data / "journal.sqlite").stat().st_mode & 0o777 == 0o600
    assert data.stat().st_mode & 0o777 == 0o700


def test_what_is_written_is_there_after_reopening():
    c = db.connect()
    db.insert_trade(
        c,
        {"trade_ref": "T-1", "asset": "TEST", "direction": "long", "source": "manual"},
    )
    c.close()
    c = db.connect()
    assert c.execute("select trade_ref from trades").fetchall()[0][0] == "T-1"
    c.close()


def test_a_journal_in_documents_is_refused_before_anything_is_written(
    monkeypatch, tmp_path
):
    where = Path.home() / "Documents" / "tj-test-should-not-exist"
    monkeypatch.setenv("TRADE_JOURNAL_HOME", str(where))
    with pytest.raises(paths.UnsafeLocation):
        db.connect()
    assert not where.exists()


def test_a_trade_needs_a_known_direction(conn):
    with pytest.raises(Exception):
        db.insert_trade(
            conn,
            {
                "trade_ref": "T-1",
                "asset": "TEST",
                "direction": "sideways",
                "source": "manual",
            },
        )


def test_two_trades_cannot_share_a_ref(conn):
    db.insert_trade(
        conn,
        {"trade_ref": "T-1", "asset": "TEST", "direction": "long", "source": "manual"},
    )
    with pytest.raises(Exception):
        db.insert_trade(
            conn,
            {
                "trade_ref": "T-1",
                "asset": "TEST",
                "direction": "short",
                "source": "manual",
            },
        )


def test_an_unknown_field_is_refused_not_dropped(conn):
    with pytest.raises(KeyError, match="pnl"):
        db.insert_trade(
            conn,
            {
                "trade_ref": "T-1",
                "asset": "TEST",
                "direction": "long",
                "source": "manual",
                "pnl": 5,
            },
        )
