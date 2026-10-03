"""The pre-entry checklist: auto-checks from the journal's own data, the trader's taps,
and a verdict. Green on every killer item says Go; any red says no trade."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from conftest import rows
from test_rulebook import BASE, with_line
from tradejournal import checklist, db, rulebook
from tradejournal.legacy import SAST

NOW = datetime(2026, 10, 5, 14, 30, tzinfo=SAST)  # Monday, v0 in force
YES = {"stop_in_order": True, "planned_rr_ok": True, "size_ok": True}


def book(conn, text=BASE):
    rb = rulebook.parse(text)
    rulebook.record(conn, rb)
    return rb


def a_trade(conn, ref, entry, exit_=None, r=None, source="tradingview-csv"):
    db.insert_trade(
        conn,
        {
            "trade_ref": ref,
            "asset": "TEST",
            "direction": "long",
            "source": source,
            "entry_time": entry,
            "exit_time": exit_,
            "result_r": r,
        },
    )


def a_checklist(conn, at, verdict="go", outcome=None, matched=None):
    conn.execute(
        "INSERT INTO checklists (filled_at, direction, answers_json, auto_json, verdict, outcome, matched_trade_id)"
        " VALUES (?, 'long', '{}', '{}', ?, ?, ?)",
        (at, verdict, outcome, matched),
    )
    conn.commit()


# --- auto-checks ---------------------------------------------------------------


def test_a_quiet_day_counts_nothing(conn):
    auto = checklist.auto_checks(conn, book(conn), NOW)
    assert (auto.trades_today, auto.losses_today, auto.in_session) == (0, 0, None)


def test_trades_today_counts_imports_and_go_checklists_not_yet_imported(conn):
    rb = book(conn)
    a_trade(conn, "T1", "2026-10-05T09:40:00+02:00", "2026-10-05T10:10:00+02:00", 1.0)
    a_trade(
        conn, "T0", "2026-10-02T15:00:00+02:00", "2026-10-02T15:30:00+02:00", -1.0
    )  # another day
    a_checklist(conn, "2026-10-05T12:00:00+02:00", "go")  # taken, not imported yet
    a_checklist(conn, "2026-10-05T12:30:00+02:00", "taken-anyway")
    a_checklist(conn, "2026-10-05T13:00:00+02:00", "no-trade")  # not taken
    a_checklist(conn, "2026-10-05T13:30:00+02:00", "go", outcome="skipped")
    assert checklist.auto_checks(conn, rb, NOW).trades_today == 3


def test_a_matched_checklist_is_not_counted_twice(conn):
    rb = book(conn)
    a_trade(conn, "T1", "2026-10-05T09:40:00+02:00", "2026-10-05T10:10:00+02:00", 1.0)
    a_checklist(conn, "2026-10-05T09:35:00+02:00", "go", outcome="taken", matched=1)
    assert checklist.auto_checks(conn, rb, NOW).trades_today == 1


def test_losses_today_are_trades_closed_today_at_a_loss(conn):
    rb = book(conn)
    a_trade(conn, "T1", "2026-10-05T09:40:00+02:00", "2026-10-05T10:10:00+02:00", -1.0)
    a_trade(conn, "T2", "2026-10-05T11:00:00+02:00", "2026-10-05T11:20:00+02:00", 2.0)
    a_trade(conn, "T3", "2026-10-05T12:00:00+02:00", "2026-10-05T12:20:00+02:00", -0.4)
    a_trade(conn, "T4", "2026-10-02T12:00:00+02:00", "2026-10-02T12:20:00+02:00", -1.0)
    assert checklist.auto_checks(conn, rb, NOW).losses_today == 2


def test_the_session_check_follows_the_windows(conn):
    rb = book(
        conn,
        with_line("session_windows_sast", 'session_windows_sast = ["14:00-18:00"]'),
    )
    assert checklist.auto_checks(conn, rb, NOW).in_session is True
    early = NOW.replace(hour=11)
    assert checklist.auto_checks(conn, rb, early).in_session is False


# --- verdict -------------------------------------------------------------------


def test_all_green_says_go(conn):
    rb = book(conn)
    assert checklist.verdict(rb, YES, checklist.auto_checks(conn, rb, NOW)) == (
        "go",
        [],
    )


@pytest.mark.parametrize("item", ["stop_in_order", "planned_rr_ok", "size_ok"])
def test_one_no_says_no_trade_and_names_it(conn, item):
    rb = book(conn)
    answers = {**YES, item: False}
    assert checklist.verdict(rb, answers, checklist.auto_checks(conn, rb, NOW)) == (
        "no-trade",
        [item],
    )


def test_the_max_trades_a_day_turns_it_red(conn):
    rb = book(conn)
    for i in range(3):
        a_trade(
            conn,
            f"T{i}",
            f"2026-10-05T0{i + 7}:00:00+02:00",
            f"2026-10-05T0{i + 7}:30:00+02:00",
            1.0,
        )
    assert checklist.verdict(rb, YES, checklist.auto_checks(conn, rb, NOW)) == (
        "no-trade",
        ["under_max"],
    )


def test_the_stop_after_losses_turns_it_red(conn):
    rb = book(conn)
    a_trade(conn, "T1", "2026-10-05T09:00:00+02:00", "2026-10-05T09:30:00+02:00", -1.0)
    a_trade(conn, "T2", "2026-10-05T10:00:00+02:00", "2026-10-05T10:30:00+02:00", -1.0)
    assert checklist.verdict(rb, YES, checklist.auto_checks(conn, rb, NOW)) == (
        "no-trade",
        ["under_stop_after"],
    )


def test_outside_the_windows_is_red_but_unset_windows_are_not(conn):
    rb = book(
        conn,
        with_line("session_windows_sast", 'session_windows_sast = ["09:00-11:00"]'),
    )
    assert checklist.verdict(rb, YES, checklist.auto_checks(conn, rb, NOW)) == (
        "no-trade",
        ["in_session"],
    )
    unset = book(conn)
    assert (
        checklist.verdict(unset, YES, checklist.auto_checks(conn, unset, NOW))[0]
        == "go"
    )


def test_an_item_off_the_killer_list_does_not_block(conn):
    text = with_line("killer_items", 'killer_items = ["stop_in_order"]')
    rb = book(conn, text)
    answers = {**YES, "size_ok": False}
    assert checklist.verdict(rb, answers, checklist.auto_checks(conn, rb, NOW)) == (
        "go",
        [],
    )


# --- saving ------------------------------------------------------------------


def test_saving_stamps_the_time_and_the_rulebook_version(conn):
    rb = book(conn)
    cid = checklist.save(
        conn,
        rb,
        NOW,
        direction="long",
        setup="sweep-BOS-FVG",
        answers=YES,
        emotion_word="calm",
        confidence=4,
        asset="ABCUSD",
    )
    got = conn.execute("SELECT * FROM checklists WHERE id = ?", (cid,)).fetchone()
    assert got["filled_at"] == "2026-10-05T14:30:00+02:00"
    assert (got["verdict"], got["rulebook_version"], got["setup"]) == (
        "go",
        "v0",
        "sweep-BOS-FVG",
    )
    assert json.loads(got["answers_json"]) == YES
    assert json.loads(got["auto_json"])["trades_today"] == 0


def test_before_the_rulebook_starts_the_checklist_has_no_version(conn):
    rb = book(conn)
    cid = checklist.save(
        conn,
        rb,
        NOW.replace(day=2),
        direction="long",
        setup="sweep-BOS-FVG",
        answers=YES,
    )
    assert rows(conn, "select rulebook_version from checklists where id = ?", cid) == [
        (None,)
    ]


def test_taking_it_anyway_is_recorded_only_after_a_red_verdict(conn):
    rb = book(conn)
    red = checklist.save(
        conn,
        rb,
        NOW,
        direction="long",
        setup="sweep-BOS-FVG",
        answers={**YES, "size_ok": False},
    )
    green = checklist.save(
        conn, rb, NOW, direction="long", setup="sweep-BOS-FVG", answers=YES
    )
    checklist.take_anyway(conn, red)
    with pytest.raises(ValueError):
        checklist.take_anyway(conn, green)
    assert rows(conn, "select id, verdict from checklists order by id") == [
        (red, "taken-anyway"),
        (green, "go"),
    ]


def test_not_taking_it_marks_the_setup_skipped(conn):
    rb = book(conn)
    cid = checklist.save(
        conn,
        rb,
        NOW,
        direction="long",
        setup="sweep-BOS-FVG",
        answers={**YES, "size_ok": False},
    )
    checklist.skip(conn, cid)
    assert rows(conn, "select outcome from checklists") == [("skipped",)]


@pytest.mark.parametrize(
    "bad", [dict(direction="sideways"), dict(setup="vibes"), dict(confidence=9)]
)
def test_a_bad_answer_is_refused(conn, bad):
    rb = book(conn)
    fields = dict(direction="long", setup="sweep-BOS-FVG", answers=YES) | bad
    with pytest.raises(ValueError):
        checklist.save(conn, rb, NOW, **fields)
