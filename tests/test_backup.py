"""Backups: one copy of the journal a day in ~/Backups/trade-journal, plus the
screenshots and the two config files. Owner-only. Old copies are never removed."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from tradejournal import backup, db, paths, screenshots
from tradejournal.legacy import SAST
from test_screenshots import png

DAY = datetime(2026, 10, 5, 18, 0, tzinfo=SAST)


def a_trade(conn, ref="T1"):
    return db.insert_trade(
        conn,
        {
            "trade_ref": ref,
            "asset": "TEST",
            "direction": "long",
            "source": "tradingview-csv",
        },
    )


def test_a_daily_copy_holds_the_journal_and_is_private(conn, private_dirs):
    _, backups = private_dirs
    a_trade(conn)
    made = backup.daily(conn, DAY)
    assert made == backups / "journal-2026-10-05.sqlite"
    assert (
        made.stat().st_mode & 0o777 == 0o600 and backups.stat().st_mode & 0o777 == 0o700
    )
    copy = sqlite3.connect(made)
    assert copy.execute("select count(*) from trades").fetchone()[0] == 1
    copy.close()


def test_one_copy_a_day_and_old_ones_stay(conn, private_dirs):
    _, backups = private_dirs
    a_trade(conn)
    assert backup.daily(conn, DAY) is not None
    a_trade(conn, "T2")
    assert backup.daily(conn, DAY.replace(hour=20)) is None  # today's copy exists
    assert backup.daily(conn, DAY.replace(day=6)) is not None
    assert sorted(p.name for p in backups.glob("journal-*.sqlite")) == [
        "journal-2026-10-05.sqlite",
        "journal-2026-10-06.sqlite",
    ]


def test_screenshots_and_config_are_copied_too(conn, private_dirs):
    data, backups = private_dirs
    tid = a_trade(conn)
    screenshots.add(conn, tid, png(), phase="entry")
    paths.write_private(paths.rulebook_path(), b'version = "v0"\n')
    backup.daily(conn, DAY)
    assert (backups / "screenshots" / str(tid) / "entry.png").read_bytes() == png()
    assert (backups / "rulebook.toml").read_bytes() == b'version = "v0"\n'


def test_a_labelled_copy_is_made_on_request(conn, private_dirs):
    _, backups = private_dirs
    made = backup.snapshot(conn, DAY, label="before-migration")
    assert made.name == "journal-2026-10-05-1800-before-migration.sqlite"


def test_backups_may_not_live_in_documents(conn, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv(
        "TRADE_JOURNAL_BACKUPS", str(Path.home() / "Documents" / "journal-backups")
    )
    with pytest.raises(paths.UnsafeLocation):
        backup.daily(conn, DAY)
