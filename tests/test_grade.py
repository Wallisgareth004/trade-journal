"""The process grade, A-C, from the rulebook in force on the entry day.

A: a checklist before entry with every killer item green, every rule held,
   and the trade journaled within 24 h (emotion word and screenshot).
B: every rule held, with a housekeeping slip.
C: any rule broken, taken anyway after a red checklist, or no checklist.
The money never enters into it.
"""

from __future__ import annotations

import json

import pytest

from test_rulebook import BASE, with_line
from tradejournal import db, grade, rulebook

V0 = with_line("placeholder", "placeholder = false")
WINDOWS = with_line(
    "session_windows_sast", 'session_windows_sast = ["14:00-18:00"]', V0
)


def setup_book(conn, text=V0):
    rulebook.record(conn, rulebook.parse(text))


def a_checklist(conn, filled="2026-10-05T14:00:00+02:00", verdict="go", emotion="calm"):
    cur = conn.execute(
        "INSERT INTO checklists (filled_at, direction, setup, answers_json, auto_json, emotion_word, verdict)"
        " VALUES (?, 'long', 'sweep-BOS-FVG', '{}', '{}', ?, ?)",
        (filled, emotion, verdict),
    )
    conn.commit()
    return cur.lastrowid


def a_trade(
    conn,
    ref="T1",
    *,
    checklist=True,
    verdict="go",
    shot=True,
    checklist_emotion="calm",
    **over,
):
    cid = (
        a_checklist(conn, verdict=verdict, emotion=checklist_emotion)
        if checklist
        else None
    )
    fields = {
        "trade_ref": ref,
        "asset": "TEST:ABCUSD",
        "direction": "long",
        "source": "tradingview-csv",
        "setup": "sweep-BOS-FVG",
        "placed_time": "2026-10-05T14:05:00+02:00",
        "entry_time": "2026-10-05T14:05:00+02:00",
        "exit_time": "2026-10-05T15:00:00+02:00",
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "exit": 102.0,
        "size": 2.0,
        "risk_currency": 2.0,
        "risk_pct": 0.5,
        "result_currency": 4.0,
        "result_r": 2.0,
        "planned_rr": 2.0,
        "stop_placed": "with-order",
        "stop_moved_further": None,
        "emotion_word": "calm",
        "journaled_at": "2026-10-05T16:00:00+02:00",
        "rulebook_version": "v0",
        "session_window": None,
        "checklist_id": cid,
    }
    fields.update(over)
    tid = db.insert_trade(conn, fields)
    if shot:
        conn.execute(
            "INSERT INTO screenshots (trade_id, phase, path, sha256, added_at) VALUES (?, 'entry', 'x.png', ?, ?)",
            (tid, f"sha{tid}", "2026-10-05T16:00:00+02:00"),
        )
        conn.commit()
    return tid


def graded(conn, tid=None):
    grade.grade_all(conn)
    row = conn.execute(
        "SELECT grade, followed_rules, grade_reasons_json FROM trades WHERE id = ?",
        (tid or 1,),
    ).fetchone()
    return (
        row["grade"],
        row["followed_rules"],
        json.loads(row["grade_reasons_json"] or "[]"),
    )


def test_a_clean_trade_grades_a(conn):
    setup_book(conn)
    assert graded(conn, a_trade(conn))[:2] == ("A", 1)


def test_the_money_does_not_change_the_grade(conn):
    setup_book(conn)
    loser = a_trade(conn, exit=99.0, result_r=-1.0, result_currency=-2.0)
    assert graded(conn, loser)[0] == "A"


@pytest.mark.parametrize(
    "over, words",
    [
        (dict(shot=False), "no screenshot"),
        (dict(emotion_word=None, checklist_emotion=None), "no emotion word"),
        (dict(journaled_at="2026-10-06T16:00:01+02:00"), "journaled more than 24 h"),
    ],
)
def test_a_housekeeping_slip_grades_b(conn, over, words):
    setup_book(conn)
    tid = a_trade(conn, **over)
    g, followed, reasons = graded(conn, tid)
    assert (g, followed) == ("B", 1)
    assert any(words in r for r in reasons), reasons


@pytest.mark.parametrize(
    "over, words",
    [
        (dict(checklist=False), "no checklist"),
        (dict(verdict="taken-anyway"), "taken anyway"),
        (dict(verdict="no-trade"), "taken anyway"),
        (
            dict(stop_placed="none", stop=None, result_r=None, risk_currency=None),
            "no stop",
        ),
        (dict(stop_placed="after-entry"), "stop was placed after the entry"),
        (dict(stop_moved_further=1), "stop was moved further away"),
        (dict(planned_rr=1.6), "planned R:R 1.6"),
        (dict(risk_pct=0.8), "risk 0.8%"),
        (dict(risk_pct=0.44), "risk 0.44%"),
    ],
)
def test_a_broken_rule_grades_c_and_says_which(conn, over, words):
    setup_book(conn)
    tid = a_trade(conn, **over)
    g, followed, reasons = graded(conn, tid)
    assert (g, followed) == ("C", 0)
    assert any(words in r for r in reasons), reasons


def test_risk_inside_the_tolerance_is_on_plan(conn):
    setup_book(conn)
    assert graded(conn, a_trade(conn, risk_pct=0.54))[0] == "A"  # within 10% of 0.5


def test_without_a_balance_the_risk_check_uses_his_checklist_answer(conn):
    setup_book(conn)
    tid = a_trade(conn, risk_pct=None)
    conn.execute(
        "UPDATE checklists SET answers_json = ? WHERE id = 1",
        (json.dumps({"size_ok": True}),),
    )
    conn.commit()
    g, _, reasons = graded(conn, tid)
    assert g == "A"
    assert any("your checklist answer" in r for r in reasons)


def test_outside_the_session_window_grades_c(conn):
    setup_book(conn, WINDOWS)
    tid = a_trade(conn, session_window="outside")
    g, _, reasons = graded(conn, tid)
    assert g == "C" and any("outside the session" in r for r in reasons)


def test_the_fourth_trade_of_a_day_grades_c(conn):
    setup_book(conn)
    ids = []
    for n in range(4):
        hh = 10 + n
        ids.append(
            a_trade(
                conn,
                f"T{n}",
                placed_time=f"2026-10-05T{hh}:00:00+02:00",
                entry_time=f"2026-10-05T{hh}:00:00+02:00",
                exit_time=f"2026-10-05T{hh}:30:00+02:00",
            )
        )
    grade.grade_all(conn)
    got = [
        conn.execute("SELECT grade FROM trades WHERE id = ?", (i,)).fetchone()[0]
        for i in ids
    ]
    assert got == ["A", "A", "A", "C"]


def test_a_trade_after_two_losses_grades_c(conn):
    setup_book(conn)
    loss = dict(exit=99.0, result_r=-1.0, result_currency=-2.0)
    a_trade(
        conn,
        "L1",
        placed_time="2026-10-05T10:00:00+02:00",
        entry_time="2026-10-05T10:00:00+02:00",
        exit_time="2026-10-05T10:30:00+02:00",
        **loss,
    )
    a_trade(
        conn,
        "L2",
        placed_time="2026-10-05T11:00:00+02:00",
        entry_time="2026-10-05T11:00:00+02:00",
        exit_time="2026-10-05T11:30:00+02:00",
        **loss,
    )
    third = a_trade(conn, "T3")  # 14:05, after both losses closed
    g, _, reasons = graded(conn, third)
    assert g == "C" and any("after 2 losses" in r for r in reasons)


def test_a_loss_still_open_at_entry_does_not_count(conn):
    setup_book(conn)
    loss = dict(exit=99.0, result_r=-1.0, result_currency=-2.0)
    a_trade(
        conn,
        "L1",
        placed_time="2026-10-05T10:00:00+02:00",
        entry_time="2026-10-05T10:00:00+02:00",
        exit_time="2026-10-05T10:30:00+02:00",
        **loss,
    )
    a_trade(
        conn,
        "L2",
        placed_time="2026-10-05T13:00:00+02:00",
        entry_time="2026-10-05T13:00:00+02:00",
        exit_time="2026-10-05T14:30:00+02:00",
        **loss,
    )  # still open at 14:05
    assert graded(conn, a_trade(conn, "T3"))[0] == "A"


def test_trades_before_the_rulebook_and_old_tracker_trades_stay_ungraded(conn):
    setup_book(conn)
    early = a_trade(conn, "E", rulebook_version=None)
    old = db.insert_trade(
        conn,
        {
            "trade_ref": "OLD",
            "asset": "X",
            "direction": "long",
            "source": "legacy-csv",
            "legacy_json": "{}",
        },
    )
    grade.grade_all(conn)
    for tid in (early, old):
        assert conn.execute(
            "SELECT grade, followed_rules FROM trades WHERE id = ?", (tid,)
        ).fetchone()[:] == (None, None)


def test_every_reason_is_plain_words(conn):
    setup_book(conn)
    tid = a_trade(conn, checklist=False, planned_rr=1.5, shot=False)
    reasons = graded(conn, tid)[2]
    assert reasons and all(isinstance(r, str) and r[0].isupper() for r in reasons)


def test_an_import_grades_its_trades_straight_away(conn, tmp_path):
    from datetime import datetime

    from tradejournal import settings, tvimport
    from tradejournal.synth import HISTORY_HEADER, Spec, history_rows, to_csv

    setup_book(conn)
    a_checklist(conn, filled="2026-10-05T14:03:00+02:00")
    specs = [
        Spec(
            symbol="TEST:ABCUSD",
            direction="long",
            qty=1,
            entry=100,
            stop=99,
            target=102,
            placed=datetime(2026, 10, 5, 14, 5),
            exit_kind="target",
        ),
        Spec(
            symbol="TEST:ABCUSD",
            direction="long",
            qty=1,
            entry=100,
            stop=99,
            target=102,
            placed=datetime(2026, 10, 5, 16, 0),
            exit_kind="stop",
        ),
    ]
    path = tmp_path / "h.csv"
    path.write_bytes(to_csv(HISTORY_HEADER, history_rows(specs)))
    tvimport.import_file(conn, path, settings.parse(""))
    got = conn.execute(
        "SELECT grade, followed_rules FROM trades ORDER BY entry_time"
    ).fetchall()
    assert [tuple(r) for r in got] == [
        ("B", 1),
        ("C", 0),
    ]  # B: no screenshot yet; C: no checklist


# --- the grading start: trades before it are shown, not graded ----


FIRST = (
    with_line("placeholder", "placeholder = false")
    + 'grading_starts = "first-checklist"\n'
)


def test_grading_starts_with_the_first_trade_that_had_a_checklist(conn):
    setup_book(conn, FIRST)
    before = a_trade(
        conn,
        "T0",
        checklist=False,
        placed_time="2026-10-05T09:00:00+02:00",
        entry_time="2026-10-05T09:00:00+02:00",
        exit_time="2026-10-05T09:30:00+02:00",
    )
    first = a_trade(conn, "T1")  # 14:05, with its checklist
    after = a_trade(
        conn,
        "T2",
        checklist=False,
        placed_time="2026-10-05T16:00:00+02:00",
        entry_time="2026-10-05T16:00:00+02:00",
        exit_time="2026-10-05T16:30:00+02:00",
    )
    grade.grade_all(conn)
    got = {
        tid: tuple(
            conn.execute(
                "SELECT grade, followed_rules FROM trades WHERE id = ?", (tid,)
            ).fetchone()
        )
        for tid in (before, first, after)
    }
    assert got == {before: (None, None), first: ("A", 1), after: ("C", 0)}


def test_with_no_checklist_trade_yet_nothing_is_graded(conn):
    setup_book(conn, FIRST)
    tid = a_trade(conn, "T0", checklist=False)
    grade.grade_all(conn)
    assert (
        conn.execute("SELECT grade FROM trades WHERE id = ?", (tid,)).fetchone()[0]
        is None
    )


def test_a_set_grading_start_time(conn):
    setup_book(
        conn,
        with_line("placeholder", "placeholder = false")
        + 'grading_starts = "2026-10-05T12:00"\n',
    )
    early = a_trade(
        conn,
        "T0",
        placed_time="2026-10-05T10:00:00+02:00",
        entry_time="2026-10-05T10:00:00+02:00",
        exit_time="2026-10-05T10:30:00+02:00",
    )
    late = a_trade(conn, "T1")
    grade.grade_all(conn)
    assert (
        conn.execute("SELECT grade FROM trades WHERE id = ?", (early,)).fetchone()[0]
        is None
    )
    assert (
        conn.execute("SELECT grade FROM trades WHERE id = ?", (late,)).fetchone()[0]
        == "A"
    )
