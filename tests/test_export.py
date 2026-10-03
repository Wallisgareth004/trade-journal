"""trades.csv: the old 27 columns first, in order, so journal_stats.py still works."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import rows
from tradejournal import db, export

OLD_COLUMNS = (
    "id,asset,direction,setup,grade,entry_date,entry,stop,size,risk_pct,risk_currency,"
    "exit_date,exit,fees,days_held,result_currency,result_R,process,followed_rules,"
    "exit_reason,confidence,mistake,lesson,session_window_SAST,planned_RR,emotion_word,screenshot"
).split(",")
JOURNAL_STATS = Path(
    os.environ.get("TRADE_JOURNAL_STATS_SCRIPT", "/nonexistent/journal_stats.py")
)


def a_trade(conn, ref="2026-10-05-TESTUSD-01", **over):
    fields = {
        "trade_ref": ref,
        "asset": "TESTUSD",
        "direction": "long",
        "source": "tradingview-csv",
        "setup": "sweep-BOS-FVG",
        "entry_time": "2026-10-05T14:05:30+02:00",
        "exit_time": "2026-10-05T15:40:10+02:00",
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.5,
        "exit": 102.5,
        "size": 2.0,
        "fees": 0.0,
        "risk_currency": 2.0,
        "risk_pct": 0.5,
        "result_currency": 5.0,
        "result_r": 2.5,
        "planned_rr": 2.5,
        "exit_reason": "target",
        "session_window": "14:00-18:00",
        "weekday": "Mon",
        "emotion_word": "calm",
        "confidence": 4,
        "rulebook_version": "v0",
        "grade": "A",
        "grade_reasons_json": '["every rule held"]',
        "followed_rules": 1,
    }
    fields.update(over)
    return db.insert_trade(conn, fields)


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def test_the_old_columns_come_first_in_their_order(conn):
    header = read(export.write(conn))[0]
    assert header[:27] == OLD_COLUMNS
    for new in (
        "target_price",
        "weekday",
        "grade_reasons",
        "rulebook_version",
        "source",
        "source_ref",
        "created_at",
        "updated_at",
    ):
        assert new in header[27:]


def test_a_new_trade_is_written_in_the_old_formats(conn):
    a_trade(conn)
    header, row = read(export.write(conn))
    got = dict(zip(header, row))
    assert got["id"] == "2026-10-05-TESTUSD-01"
    assert got["entry_date"] == "2026-10-05T14:05"
    assert got["exit_date"] == "2026-10-05T15:40"
    assert got["days_held"] == "0"
    assert got["grade"] == "A"
    assert got["process"] == "green"
    assert got["followed_rules"] == "true"
    assert got["result_R"] == "2.5"
    assert got["session_window_SAST"] == "14:00-18:00"
    assert got["target_price"] == "102.5"


@pytest.mark.parametrize(
    "grade, colour, followed",
    [("A", "green", "true"), ("B", "yellow", "true"), ("C", "red", "false")],
)
def test_the_process_colour_follows_the_grade(conn, grade, colour, followed):
    a_trade(conn, grade=grade, followed_rules=1 if followed == "true" else 0)
    header, row = read(export.write(conn))
    got = dict(zip(header, row))
    assert (got["process"], got["followed_rules"]) == (colour, followed)


def test_an_ungraded_trade_leaves_the_grade_blank(conn):
    a_trade(conn, grade=None, followed_rules=None, rulebook_version=None)
    header, row = read(export.write(conn))
    got = dict(zip(header, row))
    assert (got["grade"], got["process"], got["followed_rules"]) == ("", "", "")


def test_a_formula_typed_into_a_lesson_is_defused_but_numbers_stay_numbers(conn):
    a_trade(conn, lesson='=HYPERLINK("http://x")', mistake="@SUM(A1)", result_r=-1.5)
    header, row = read(export.write(conn))
    got = dict(zip(header, row))
    assert got["lesson"] == '\'=HYPERLINK("http://x")'
    assert got["mistake"] == "'@SUM(A1)"
    assert got["result_R"] == "-1.5"


def test_tiny_prices_are_written_without_exponents(conn):
    a_trade(conn, entry=0.00001234, stop=0.0000123)
    header, row = read(export.write(conn))
    got = dict(zip(header, row))
    assert (got["entry"], got["stop"]) == ("0.00001234", "0.0000123")


def test_the_export_is_owner_only_and_leaves_no_temp_file(conn, private_dirs):
    data, _ = private_dirs
    out = export.write(conn)
    assert out == data / "exports" / "trades.csv"
    assert out.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in out.parent.iterdir()) == ["trades.csv"]


@pytest.mark.skipif(
    not JOURNAL_STATS.exists(),
    reason="set TRADE_JOURNAL_STATS_SCRIPT (tests/local.env) to a journal_stats.py",
)
def test_his_journal_stats_script_reads_the_export(conn):
    a_trade(conn, "2026-10-05-TESTUSD-01")
    a_trade(
        conn,
        "2026-10-05-TESTUSD-02",
        entry_time="2026-10-05T16:00:00+02:00",
        exit_time="2026-10-05T16:30:00+02:00",
        result_r=-1.0,
        grade="C",
        followed_rules=0,
    )
    out = export.write(conn)
    run = subprocess.run(
        [sys.executable, str(JOURNAL_STATS), str(out), "--no-mirror"],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    for words in (
        "Trades: 2 (2 closed)",
        "Win rate: 50%",
        "Average R: +0.75",
        "Followed the rules: 50%",
    ):
        assert words in run.stdout, run.stdout
    assert rows(conn, "select count(*) from trades") == [(2,)]
