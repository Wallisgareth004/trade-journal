"""The numbers on the dashboard, checked against hand-worked values.

Wilson 25/50 = 36.6%-63.4% is the spec's own example; the drawdown and
profit factor cases are small enough to do on paper.
"""

from __future__ import annotations

import random
import statistics

import pytest

from tradejournal import stats


# --- Wilson ------------------------------------------------------------------


def test_wilson_25_of_50_is_the_specs_range():
    lo, hi = stats.wilson(25, 50)
    assert lo == pytest.approx(0.366446, abs=5e-6)
    assert hi == pytest.approx(0.633554, abs=5e-6)


def test_wilson_at_the_edges_stays_inside_0_and_1():
    lo, hi = stats.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.277533, abs=2e-6)  # z^2 / (n + z^2)
    lo, hi = stats.wilson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.722467, abs=2e-6)


def test_wilson_with_no_trades_is_none():
    assert stats.wilson(0, 0) is None


# --- expectancy's rough range (bootstrap-t) ---------------------------------


def test_bootstrap_t_needs_ten_trades():
    assert stats.bootstrap_t([1.0, -1.0] * 4) is None


def test_bootstrap_t_is_repeatable():
    rs = [2.0, -1.0, -1.0, 1.5, -1.0, 2.5, -1.0, -1.0, 2.0, -0.5, 1.0, -1.0]
    assert stats.bootstrap_t(rs) == stats.bootstrap_t(rs)


def test_bootstrap_t_matches_the_t_interval_on_a_large_symmetric_sample():
    rng = random.Random(11)
    xs = [rng.gauss(0.2, 1.0) for _ in range(400)]
    lo, hi = stats.bootstrap_t(xs, reps=4000)
    m, se = statistics.fmean(xs), statistics.stdev(xs) / 400**0.5
    assert lo == pytest.approx(m - 1.966 * se, abs=0.25 * se)
    assert hi == pytest.approx(m + 1.966 * se, abs=0.25 * se)


def test_bootstrap_t_covers_the_true_mean_about_95_percent_of_the_time():
    """A sign slip in the studentised formula would wreck this coverage."""
    rng = random.Random(5)
    true_mean = 0.2  # 40% wins at +2R, 60% losses at -1R
    covered = 0
    trials = 60
    for _ in range(trials):
        sample = [2.0 if rng.random() < 0.4 else -1.0 for _ in range(50)]
        lo, hi = stats.bootstrap_t(sample, reps=1000, seed=rng.randrange(10**6))
        covered += lo <= true_mean <= hi
    assert covered >= 51  # >= 85% of 60; an honest 95% interval rarely falls below that


def test_a_sample_with_no_spread_has_no_range():
    assert stats.bootstrap_t([1.0] * 12) is None


# --- profit factor, drawdown, histogram -------------------------------------


def test_profit_factor():
    assert stats.profit_factor([2.0, -1.0, 1.5, -1.0]) == pytest.approx(1.75)
    assert stats.profit_factor([1.0, 2.0]) is None  # no losses yet


def test_max_drawdown_is_the_largest_fall_from_a_peak():
    assert stats.max_drawdown([1, -1, -1, 2, -3, 1]) == pytest.approx(3.0)
    assert stats.max_drawdown([-1, -1]) == pytest.approx(2.0)
    assert stats.max_drawdown([1, 2]) == 0.0


def test_the_equity_curve_starts_at_zero():
    assert stats.equity_curve([1, -0.5, 2]) == [0.0, 1.0, 0.5, 2.5]


def test_the_histogram_uses_half_r_bins():
    got = stats.histogram([-1.0, -1.0, -0.2, 0.0, 0.4, 2.0, 2.2])
    assert got == [(-1.0, 2), (-0.5, 1), (0.0, 2), (2.0, 2)]


# --- the process numbers -----------------------------------------------------


def T(
    r,
    followed=1,
    setup="A",
    window="14:00-18:00",
    weekday="Mon",
    emotion="calm",
    checklist=1,
    grade="A",
):
    return {
        "result_r": r,
        "followed_rules": followed,
        "setup": setup,
        "session_window": window,
        "weekday": weekday,
        "emotion_word": emotion,
        "checklist_id": checklist,
        "grade": grade,
    }


def test_the_four_trade_types():
    trades = [
        T(2.0),
        T(-1.0),
        T(1.0, followed=0, grade="C"),
        T(-1.0, followed=0, grade="C"),
        T(-0.5),
    ]
    assert stats.four_types(trades) == {
        "followed_won": 1,
        "followed_lost": 2,
        "broke_won": 1,
        "broke_lost": 1,
    }


def test_rule_adherence_counts_a_and_b_over_all_graded():
    trades = [
        T(1.0, grade="A"),
        T(-1.0, grade="B"),
        T(1.0, followed=0, grade="C"),
        T(None, grade="A"),
    ]
    assert stats.adherence(trades) == {"followed": 3, "graded": 4, "pct": 75.0}


def test_checklist_completion():
    trades = [T(1.0), T(1.0, checklist=None), T(-1.0), T(None)]
    assert stats.checklist_completion(trades) == {"with": 3, "graded": 4, "pct": 75.0}


def test_summary_of_r():
    s = stats.summary([2.0, -1.0, -1.0, 1.5, None])
    assert (s["n"], s["wins"], s["losses"], s["no_r"]) == (4, 2, 2, 1)
    assert s["expectancy"] == pytest.approx(0.375)
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["avg_win"] == pytest.approx(1.75) and s["avg_loss"] == pytest.approx(-1.0)
    assert s["payoff"] == pytest.approx(1.75)
    assert s["expectancy_range"] is None  # under 10 trades: too few to tell


def test_buckets_say_too_few_under_ten():
    trades = [T(1.0, setup="A")] * 10 + [T(-1.0, setup="B")] * 3
    got = {b["key"]: b for b in stats.buckets(trades, "setup")}
    assert got["A"]["n"] == 10 and got["A"]["too_few"] is False
    assert got["B"]["n"] == 3 and got["B"]["too_few"] is True
    assert got["A"]["win_rate"] == 1.0 and got["A"]["wilson"][1] == 1.0


# --- the checkpoint, read as the rulebook says ------------------------------


def checkpoint(rs, followed_pct, reading="point"):
    n_followed = round(len(rs) * followed_pct / 100)
    trades = [
        T(r, followed=1 if i < n_followed else 0, grade="A" if i < n_followed else "C")
        for i, r in enumerate(rs)
    ]
    return stats.checkpoint(trades, target=50, threshold_pct=90, reading=reading)


def test_before_fifty_trades_it_only_counts():
    c = checkpoint([1.0] * 23, 100)
    assert (c["n"], c["target"], c["reached"]) == (23, 50, False)


def test_under_the_rule_threshold_it_says_fix_the_process_first():
    assert checkpoint([1.0] * 50, 80)["reading"] == "fix the process first"


def test_negative_expectancy_with_rules_followed_says_change_the_setup():
    assert checkpoint([-1.0] * 30 + [1.0] * 20, 96)["reading"] == "change the setup"


def test_positive_expectancy_with_rules_followed_says_keep_going():
    assert checkpoint([2.0] * 25 + [-1.0] * 25, 96)["reading"] == "keep going"


def test_the_interval_reading_waits_when_the_range_spans_zero():
    rs = [2.0] * 17 + [-1.0] * 33  # mean +0.02, range spans 0
    assert (
        checkpoint(rs, 96, reading="interval")["reading"]
        == "too close to call: keep going to 100 trades, or test the setup mechanically"
    )
    assert checkpoint(rs, 96, reading="point")["reading"] == "keep going"


def test_buckets_can_follow_a_fixed_order_and_name_the_empty_group():
    trades = [
        T(1.0, weekday="Wed"),
        T(1.0, weekday="Mon"),
        T(1.0, weekday="Mon"),
        T(-1.0, weekday=None),
    ]
    got = stats.buckets(
        trades, "weekday", order=("Mon", "Tue", "Wed"), empty="no weekday"
    )
    assert [b["key"] for b in got] == ["Mon", "Wed", "no weekday"]
