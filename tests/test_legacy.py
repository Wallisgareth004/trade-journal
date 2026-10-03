"""The old tracker's rows come in once, exactly as they were written."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FIXTURES, rows
from tradejournal import export, legacy

NOW = datetime(2026, 10, 2, 16, 0, tzinfo=timezone(timedelta(hours=2)))
OLD_COLUMNS = (
    "id,asset,direction,setup,grade,entry_date,entry,stop,size,risk_pct,risk_currency,"
    "exit_date,exit,fees,days_held,result_currency,result_R,process,followed_rules,"
    "exit_reason,confidence,mistake,lesson,session_window_SAST,planned_RR,emotion_word,screenshot"
).split(",")


def test_example_rows_are_skipped_and_real_rows_come_in(conn):
    r = legacy.migrate(conn, FIXTURES / "legacy_tracker.csv", now=NOW)
    assert (r.read, r.examples, r.new, r.already) == (3, 1, 2, 0)
    assert rows(conn, "select trade_ref, source from trades order by trade_ref") == [
        ("2026-09-28-XYZ-01", "legacy-csv"),
        ("2026-09-29-QRS-01", "legacy-csv"),
    ]


def test_migrating_twice_adds_nothing(conn):
    legacy.migrate(conn, FIXTURES / "legacy_tracker.csv", now=NOW)
    r = legacy.migrate(conn, FIXTURES / "legacy_tracker.csv", now=NOW)
    assert (r.new, r.already) == (0, 2)
    assert rows(conn, "select count(*) from trades") == [(2,)]


def test_old_times_are_read_as_south_african_time(conn):
    legacy.migrate(conn, FIXTURES / "legacy_tracker.csv", now=NOW)
    got = rows(
        conn,
        "select entry_time, exit_time from trades where trade_ref = '2026-09-28-XYZ-01'",
    )
    assert got == [("2026-09-28T15:42:00+02:00", "2026-09-28T16:30:00+02:00")]


def test_old_trades_are_kept_out_of_the_grading(conn):
    legacy.migrate(conn, FIXTURES / "legacy_tracker.csv", now=NOW)
    assert (
        rows(conn, "select grade, followed_rules, rulebook_version from trades")
        == [(None, None, None)] * 2
    )


@pytest.mark.parametrize("change", ["drop", "extra", "rename"])
def test_a_file_in_another_layout_is_refused_and_nothing_comes_in(
    conn, tmp_path, change
):
    with open(FIXTURES / "legacy_tracker.csv", newline="", encoding="utf-8") as fh:
        data = list(csv.reader(fh))
    if change == "drop":
        data = [r[:7] + r[8:] for r in data]
    elif change == "extra":
        data = [r + ["x"] for r in data]
    else:
        data[0][7] = "stop_loss"
    bad = tmp_path / "bad.csv"
    with open(bad, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(data)
    with pytest.raises(legacy.LayoutError):
        legacy.migrate(conn, bad, now=NOW)
    assert rows(conn, "select count(*) from trades") == [(0,)]


def test_his_values_come_back_unchanged_in_the_export(conn):
    legacy.migrate(conn, FIXTURES / "legacy_tracker.csv", now=NOW)
    out = export.write(conn)
    with open(FIXTURES / "legacy_tracker.csv", newline="", encoding="utf-8") as fh:
        want = {r["id"]: r for r in csv.DictReader(fh) if "EXAMPLE" not in r["id"]}
    with open(out, newline="", encoding="utf-8") as fh:
        got = {r["id"]: {c: r[c] for c in OLD_COLUMNS} for r in csv.DictReader(fh)}
    assert got == want


def test_a_row_already_imported_from_tradingview_is_not_doubled(conn, tmp_path):
    from datetime import datetime as dt

    from tradejournal import settings, tvimport
    from tradejournal.synth import HISTORY_HEADER, Spec, history_rows, to_csv

    spec = Spec(
        symbol="TEST:XYZUSD",
        direction="long",
        qty=2,
        entry=2010.5,
        stop=2005.5,
        target=2020.5,
        placed=dt(2026, 9, 28, 15, 40),
        exit_kind="target",
    )
    path = tmp_path / "h.csv"
    path.write_bytes(to_csv(HISTORY_HEADER, history_rows([spec])))
    tvimport.import_file(conn, path, settings.parse(""))
    r = legacy.migrate(conn, FIXTURES / "legacy_tracker.csv", now=NOW)
    assert (r.new, r.matched) == (1, 1)  # XYZ matched the TradingView trade; QRS is new
    assert rows(conn, "select count(*) from trades") == [(2,)]
