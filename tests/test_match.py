"""A checklist belongs to the first trade placed within the window after it."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from conftest import rows
from tradejournal import db, settings, tvimport
from tradejournal.synth import HISTORY_HEADER, Spec, history_rows, to_csv

T0 = datetime(2026, 10, 5, 14, 5, 0)  # the entry order's placing time


def checklist(
    conn,
    at: datetime,
    direction="long",
    asset=None,
    verdict="go",
    emotion="calm",
    confidence=4,
    setup="sweep-BOS-FVG",
):
    cur = conn.execute(
        "INSERT INTO checklists (filled_at, asset, direction, setup, answers_json, auto_json, emotion_word,"
        " confidence, verdict) VALUES (?, ?, ?, ?, '{}', '{}', ?, ?, ?)",
        (
            at.isoformat() + "+02:00",
            asset,
            direction,
            setup,
            emotion,
            confidence,
            verdict,
        ),
    )
    conn.commit()
    return cur.lastrowid


def import_trades(conn, tmp_path, *placed_times, direction="long", hold_minutes=30):
    """One bracket trade per placing time. TradingView nets a symbol into one
    position, so trades that overlap in time would merge: keep them apart."""
    specs = [
        Spec(
            symbol="TEST:ABCUSD",
            direction=direction,
            qty=1,
            entry=100,
            stop=99 if direction == "long" else 101,
            target=102 if direction == "long" else 98,
            placed=p,
            exit_kind="target",
            closed=p + timedelta(minutes=hold_minutes),
        )
        for p in placed_times
    ]
    path = tmp_path / "h.csv"
    path.write_bytes(to_csv(HISTORY_HEADER, history_rows(specs)))
    tvimport.import_file(conn, path, settings.parse(""))


def matched(conn):
    return rows(
        conn, "select t.trade_ref, t.checklist_id from trades t order by t.entry_time"
    )


def test_a_checklist_five_minutes_before_the_order_is_matched(conn, tmp_path):
    c = checklist(conn, T0 - timedelta(minutes=5))
    import_trades(conn, tmp_path, T0)
    assert matched(conn) == [("2026-10-05-ABCUSD-01", c)]
    assert rows(
        conn, "select outcome, matched_trade_id is not null from checklists"
    ) == [("taken", 1)]


def test_the_trade_takes_the_checklist_answers(conn, tmp_path):
    checklist(
        conn,
        T0 - timedelta(minutes=1),
        emotion="rushed",
        confidence=2,
        setup="trendline-FVG",
    )
    import_trades(conn, tmp_path, T0)
    assert rows(conn, "select emotion_word, confidence, setup from trades") == [
        ("rushed", 2, "trendline-FVG")
    ]


def test_a_checklist_older_than_the_window_is_not_matched(conn, tmp_path):
    checklist(conn, T0 - timedelta(minutes=31))
    import_trades(conn, tmp_path, T0)
    assert matched(conn) == [("2026-10-05-ABCUSD-01", None)]


def test_a_checklist_filled_after_the_order_was_placed_is_not_matched(conn, tmp_path):
    checklist(conn, T0 + timedelta(seconds=30))
    import_trades(conn, tmp_path, T0)
    assert matched(conn) == [("2026-10-05-ABCUSD-01", None)]


def test_one_checklist_covers_one_trade_only(conn, tmp_path):
    c = checklist(conn, T0 - timedelta(minutes=2))
    import_trades(conn, tmp_path, T0, T0 + timedelta(minutes=10), hold_minutes=5)
    assert matched(conn) == [
        ("2026-10-05-ABCUSD-01", c),
        ("2026-10-05-ABCUSD-02", None),
    ]


def test_each_trade_takes_the_latest_checklist_before_it(conn, tmp_path):
    first = checklist(conn, T0 - timedelta(minutes=20))
    second = checklist(conn, T0 - timedelta(minutes=3))
    import_trades(conn, tmp_path, T0)
    assert matched(conn) == [("2026-10-05-ABCUSD-01", second)]
    assert rows(
        conn, "select id, matched_trade_id from checklists where id = ?", first
    ) == [(first, None)]


def test_the_wrong_direction_is_not_matched(conn, tmp_path):
    checklist(conn, T0 - timedelta(minutes=2), direction="short")
    import_trades(conn, tmp_path, T0)
    assert matched(conn) == [("2026-10-05-ABCUSD-01", None)]


@pytest.mark.parametrize(
    "asset, hit", [("ABCUSD", True), ("abc", True), (None, True), ("XYZ", False)]
)
def test_a_named_asset_must_match_the_symbol(conn, tmp_path, asset, hit):
    c = checklist(conn, T0 - timedelta(minutes=2), asset=asset)
    import_trades(conn, tmp_path, T0)
    assert matched(conn) == [("2026-10-05-ABCUSD-01", c if hit else None)]


def test_a_no_trade_checklist_still_matches_so_the_grade_can_say_so(conn, tmp_path):
    c = checklist(conn, T0 - timedelta(minutes=2), verdict="no-trade")
    import_trades(conn, tmp_path, T0)
    assert matched(conn) == [("2026-10-05-ABCUSD-01", c)]


def test_old_tracker_trades_never_take_a_checklist(conn, tmp_path):
    checklist(conn, datetime(2026, 9, 30, 15, 0))
    db.insert_trade(
        conn,
        {
            "trade_ref": "OLD-1",
            "asset": "ABC",
            "direction": "long",
            "source": "legacy-csv",
            "entry_time": "2026-09-30T15:05:00+02:00",
            "legacy_json": json.dumps({}),
        },
    )
    import_trades(conn, tmp_path, T0)
    assert rows(conn, "select checklist_id from trades where trade_ref = 'OLD-1'") == [
        (None,)
    ]
