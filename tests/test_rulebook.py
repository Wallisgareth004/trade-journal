"""The rulebook lives in config, gets checked on load, and is versioned."""

from __future__ import annotations

from datetime import date, time

import pytest

from conftest import ROOT
from tradejournal import db, rulebook

BASE = """
version = "v0"
effective_from = "2026-10-03"
placeholder = true
max_trades_per_day = 3
stop_after_losses = 2
risk_pct = 0.5
min_planned_rr = 2.0
risk_tolerance_pct = 10
session_windows_sast = []
rule_following_threshold_pct = 90
checkpoint_trades = 50
setups = ["sweep-BOS-FVG", "trendline-FVG"]
emotion_words = ["calm", "unsure", "rushed", "bored", "FOMO", "revenge"]
killer_items = ["stop_in_order", "planned_rr_ok", "size_ok", "in_session", "under_max", "under_stop_after"]
"""


def with_line(key: str, line: str | None, text: str = BASE) -> str:
    out = [ln for ln in text.splitlines() if not ln.startswith(key + " ")]
    if line is not None:
        out.append(line)
    return "\n".join(out) + "\n"


def test_the_shipped_example_holds_his_placeholders():
    rb = rulebook.load(ROOT / "examples" / "rulebook.toml")
    assert rb.risk_pct == 0.5
    assert rb.min_planned_rr == 2.0
    assert rb.max_trades_per_day == 3
    assert rb.session_windows == ()
    assert rb.placeholder is True


@pytest.mark.parametrize(
    "key, line",
    [
        ("risk_pct", "risk_pct = 0"),
        ("risk_pct", "risk_pct = 25"),
        ("risk_pct", 'risk_pct = "half"'),
        ("max_trades_per_day", "max_trades_per_day = 0"),
        ("max_trades_per_day", "max_trades_per_day = 2.5"),
        ("stop_after_losses", "stop_after_losses = -1"),
        ("min_planned_rr", "min_planned_rr = -1"),
        ("session_windows_sast", 'session_windows_sast = ["25:00-26:00"]'),
        ("session_windows_sast", 'session_windows_sast = ["14:00"]'),
        ("session_windows_sast", 'session_windows_sast = ["14:00-14:00"]'),
        ("effective_from", 'effective_from = "3 Oct"'),
        ("setups", "setups = []"),
        ("checkpoint_trades", "checkpoint_trades = 0"),
    ],
)
def test_a_bad_value_is_refused_by_name(key, line):
    with pytest.raises(rulebook.RulebookError, match=key):
        rulebook.parse(with_line(key, line))


def test_an_unknown_killer_item_is_named():
    line = 'killer_items = ["stop_in_order", "vibes"]'
    with pytest.raises(rulebook.RulebookError, match="vibes"):
        rulebook.parse(with_line("killer_items", line))


def test_a_missing_number_is_named():
    with pytest.raises(rulebook.RulebookError, match="risk_pct"):
        rulebook.parse(with_line("risk_pct", None))


def test_a_misspelt_key_is_refused_not_ignored():
    with pytest.raises(rulebook.RulebookError, match="risk_pc"):
        rulebook.parse(BASE + "risk_pc = 1\n")


def test_windows_tell_inside_from_outside():
    rb = rulebook.parse(
        with_line(
            "session_windows_sast",
            'session_windows_sast = ["14:00-18:00", "09:00-11:00"]',
        )
    )
    assert rb.window_at(time(14, 0)) == "14:00-18:00"
    assert rb.window_at(time(17, 59)) == "14:00-18:00"
    assert rb.window_at(time(18, 0)) is None
    assert rb.window_at(time(10, 30)) == "09:00-11:00"
    assert rb.window_at(time(12, 0)) is None


def test_a_window_can_cross_midnight():
    rb = rulebook.parse(
        with_line("session_windows_sast", 'session_windows_sast = ["22:00-02:00"]')
    )
    assert rb.window_at(time(23, 30)) == "22:00-02:00"
    assert rb.window_at(time(1, 0)) == "22:00-02:00"
    assert rb.window_at(time(3, 0)) is None


def test_with_no_windows_the_session_rule_is_off():
    rb = rulebook.parse(BASE)
    assert rb.windows_set is False
    assert rb.window_at(time(12, 0)) is None


# --- versions -------------------------------------------------------------


def graded_trade(conn, version: str) -> None:
    db.insert_trade(
        conn,
        {
            "trade_ref": f"T-{version}",
            "asset": "TEST",
            "direction": "long",
            "source": "manual",
            "rulebook_version": version,
            "grade": "A",
        },
    )


def test_before_the_first_version_there_is_no_rulebook(conn):
    rulebook.record(conn, rulebook.parse(BASE))
    assert rulebook.version_for(conn, date(2026, 10, 2)) is None
    assert rulebook.version_for(conn, date(2026, 10, 3)).version == "v0"


def test_placeholder_numbers_may_change_after_trades_are_graded(conn):
    rulebook.record(conn, rulebook.parse(BASE))
    graded_trade(conn, "v0")
    real = with_line(
        "placeholder", "placeholder = false", with_line("risk_pct", "risk_pct = 1.0")
    )
    rulebook.record(conn, rulebook.parse(real))
    got = rulebook.version_for(conn, date(2026, 10, 3))
    assert got.risk_pct == 1.0 and got.placeholder is False


def test_set_numbers_lock_once_a_trade_is_graded_under_them(conn):
    set_v0 = with_line("placeholder", "placeholder = false")
    rulebook.record(conn, rulebook.parse(set_v0))
    graded_trade(conn, "v0")
    changed = with_line("risk_pct", "risk_pct = 1.0", set_v0)
    with pytest.raises(rulebook.RulebookError, match="new version"):
        rulebook.record(conn, rulebook.parse(changed))
    assert rulebook.version_for(conn, date(2026, 10, 3)).risk_pct == 0.5


def test_set_numbers_may_change_while_nothing_is_graded(conn):
    set_v0 = with_line("placeholder", "placeholder = false")
    rulebook.record(conn, rulebook.parse(set_v0))
    rulebook.record(
        conn, rulebook.parse(with_line("risk_pct", "risk_pct = 0.75", set_v0))
    )
    assert rulebook.version_for(conn, date(2026, 10, 3)).risk_pct == 0.75


def test_a_new_version_takes_over_from_its_date(conn):
    rulebook.record(conn, rulebook.parse(BASE))
    v1 = with_line(
        "version",
        'version = "v1"',
        with_line("effective_from", 'effective_from = "2026-10-20"'),
    )
    rulebook.record(
        conn,
        rulebook.parse(with_line("max_trades_per_day", "max_trades_per_day = 2", v1)),
    )
    assert rulebook.version_for(conn, date(2026, 10, 19)).version == "v0"
    assert rulebook.version_for(conn, date(2026, 10, 20)).max_trades_per_day == 2


def test_a_new_version_cannot_start_before_the_last_one(conn):
    rulebook.record(conn, rulebook.parse(BASE))
    early = with_line(
        "version",
        'version = "v1"',
        with_line("effective_from", 'effective_from = "2026-10-01"'),
    )
    with pytest.raises(rulebook.RulebookError, match="effective_from"):
        rulebook.record(conn, rulebook.parse(early))


def test_the_checkpoint_reading_defaults_to_v0_as_written():
    assert rulebook.parse(BASE).checkpoint_reading == "point"
    assert (
        rulebook.parse(BASE + 'checkpoint_reading = "interval"\n').checkpoint_reading
        == "interval"
    )
    with pytest.raises(rulebook.RulebookError, match="checkpoint_reading"):
        rulebook.parse(BASE + 'checkpoint_reading = "vibes"\n')


@pytest.mark.parametrize(
    "value, want",
    [
        ('"first-checklist"', "first-checklist"),
        ('"2026-10-05"', "2026-10-05T00:00"),
        ('"2026-10-05T14:00"', "2026-10-05T14:00"),
    ],
)
def test_grading_can_start_at_the_first_checklist_trade_or_a_set_time(value, want):
    assert rulebook.parse(BASE + f"grading_starts = {value}\n").grading_starts == want


def test_without_a_grading_start_every_trade_under_the_rulebook_is_graded():
    assert rulebook.parse(BASE).grading_starts is None


def test_a_grading_start_that_is_not_a_time_is_refused():
    with pytest.raises(rulebook.RulebookError, match="grading_starts"):
        rulebook.parse(BASE + 'grading_starts = "tomorrow"\n')
