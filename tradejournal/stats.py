"""The journal's statistics, honest about small samples.

* Win rate carries a 95% Wilson interval (the Wald interval "should not be
  used" at small n: Brown, Cai & DasGupta 2001).
* Expectancy (mean R) carries a studentised bootstrap (bootstrap-t) range,
  shown as rough and never used as a gate: the percentile bootstrap
  under-covers at small n, and no bootstrap fixes a small sample (Hesterberg 2015).
* Anything under ten trades says "too few to tell".
* Rule adherence and checklist completion are counts of what the trader did.
"""

from __future__ import annotations

import functools
import math
import random
import statistics

Z95 = 1.959963984540054
TOO_FEW = 10


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    # at k = 0 the lower bound is exactly 0 and at k = n the upper is exactly 1;
    # floating point lands a hair off, so pin them
    lo = 0.0 if k == 0 else max(0.0, centre - margin)
    hi = 1.0 if k == n else min(1.0, centre + margin)
    return lo, hi


def _quantile(sorted_xs: list[float], q: float) -> float:
    """Linear interpolation between order statistics."""
    pos = q * (len(sorted_xs) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def _bootstrap_t(
    xs: list[float], reps: int = 10_000, seed: int = 0, level: float = 0.95
) -> tuple[float, float] | None:
    """A rough range for the mean: studentised bootstrap. None under ten values
    or when the values don't vary."""
    n = len(xs)
    if n < TOO_FEW:
        return None
    mean = statistics.fmean(xs)
    sd = statistics.stdev(xs)
    if sd == 0:
        return None
    se = sd / math.sqrt(n)
    rng = random.Random(seed)
    ts = []
    for _ in range(reps):
        sample = rng.choices(xs, k=n)
        m = statistics.fmean(sample)
        s = statistics.stdev(sample)
        if s == 0:
            continue
        ts.append((m - mean) / (s / math.sqrt(n)))
    if len(ts) < reps * 0.95:
        return None
    ts.sort()
    tail = (1 - level) / 2
    return mean - _quantile(ts, 1 - tail) * se, mean - _quantile(ts, tail) * se


def profit_factor(rs: list[float]) -> float | None:
    gains = sum(r for r in rs if r > 0)
    losses = -sum(r for r in rs if r < 0)
    return gains / losses if losses > 0 else None


def equity_curve(rs: list[float]) -> list[float]:
    out = [0.0]
    for r in rs:
        out.append(out[-1] + r)
    return out


def max_drawdown(rs: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in equity_curve(rs):
        peak = max(peak, value)
        worst = max(worst, peak - value)
    return worst


def histogram(rs: list[float], width: float = 0.5) -> list[tuple[float, int]]:
    counts: dict[float, int] = {}
    for r in rs:
        left = math.floor(r / width + 1e-9) * width
        counts[left] = counts.get(left, 0) + 1
    return sorted((round(k, 6) + 0.0, v) for k, v in counts.items())


def summary(rs_maybe: list[float | None]) -> dict:
    rs = [r for r in rs_maybe if r is not None]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    avg_win = statistics.fmean(wins) if wins else None
    avg_loss = statistics.fmean(losses) if losses else None
    return {
        "n": len(rs),
        "no_r": len(rs_maybe) - len(rs),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(rs) if rs else None,
        "wilson": wilson(len(wins), len(rs)),
        "expectancy": statistics.fmean(rs) if rs else None,
        "expectancy_range": bootstrap_t(rs),
        "profit_factor": profit_factor(rs),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": (avg_win / -avg_loss) if avg_win is not None and avg_loss else None,
        "max_drawdown": max_drawdown(rs),
        "total_r": sum(rs),
    }


def buckets(
    trades: list, key: str, order: tuple = (), empty: str = "(none)"
) -> list[dict]:
    """n, average R with its rough range, and win rate with its Wilson range, per
    value of `key`. Largest first, or in `order` when given (unlisted values and
    the empty group last)."""
    groups: dict[str, list[float]] = {}
    for t in trades:
        if t["result_r"] is None:
            continue
        groups.setdefault(t[key] or empty, []).append(t["result_r"])

    def rank(kv):
        name, rs = kv
        if order:
            return (
                order.index(name) if name in order else len(order),
                name == empty,
                name,
            )
        return (name == empty, -len(rs), name)

    out = []
    for name, rs in sorted(groups.items(), key=rank):
        wins = sum(1 for r in rs if r > 0)
        out.append(
            {
                "key": name,
                "n": len(rs),
                "too_few": len(rs) < TOO_FEW,
                "expectancy": statistics.fmean(rs),
                "expectancy_range": bootstrap_t(rs, reps=2000),
                "win_rate": wins / len(rs),
                "wilson": wilson(wins, len(rs)),
            }
        )
    return out


def four_types(trades: list) -> dict:
    out = {"followed_won": 0, "followed_lost": 0, "broke_won": 0, "broke_lost": 0}
    for t in trades:
        if t["result_r"] is None or t["followed_rules"] is None:
            continue
        side = "followed" if t["followed_rules"] else "broke"
        out[f"{side}_{'won' if t['result_r'] > 0 else 'lost'}"] += 1
    return out


def adherence(trades: list) -> dict:
    graded = [t for t in trades if t["grade"] is not None]
    followed = sum(1 for t in graded if t["followed_rules"])
    return {
        "followed": followed,
        "graded": len(graded),
        "pct": 100 * followed / len(graded) if graded else None,
    }


def checklist_completion(trades: list) -> dict:
    graded = [t for t in trades if t["grade"] is not None]
    with_one = sum(1 for t in graded if t["checklist_id"] is not None)
    return {
        "with": with_one,
        "graded": len(graded),
        "pct": 100 * with_one / len(graded) if graded else None,
    }


INTERVAL_SPANS_ZERO = (
    "too close to call: keep going to 100 trades, or test the setup mechanically"
)


def checkpoint(
    trades: list, target: int, threshold_pct: float, reading: str = "point"
) -> dict:
    """Progress to the checkpoint and, once reached, the rulebook's pre-written reading.

    point: rule-following under the threshold -> fix the
    process first; else expectancy under 0 -> change the setup; else keep going.
    interval: the same, but expectancy is read by its rough range.
    """
    graded = [t for t in trades if t["grade"] is not None]
    rs = [t["result_r"] for t in graded if t["result_r"] is not None]
    adh = adherence(graded)
    out = {
        "n": len(graded),
        "target": target,
        "reached": len(graded) >= target,
        "rule_following_pct": adh["pct"],
        "expectancy": statistics.fmean(rs) if rs else None,
        "expectancy_range": bootstrap_t(rs),
        "reading": None,
    }
    if not out["reached"]:
        return out
    if adh["pct"] is None or adh["pct"] < threshold_pct:
        out["reading"] = "fix the process first"
    elif reading == "interval" and out["expectancy_range"] is not None:
        lo, hi = out["expectancy_range"]
        out["reading"] = (
            "change the setup"
            if hi < 0
            else "keep going"
            if lo > 0
            else INTERVAL_SPANS_ZERO
        )
    else:
        out["reading"] = (
            "change the setup" if (out["expectancy"] or 0) < 0 else "keep going"
        )
    return out


@functools.lru_cache(maxsize=512)
def _bootstrap_cached(xs: tuple, reps: int, seed: int, level: float):
    return _bootstrap_t(list(xs), reps=reps, seed=seed, level=level)


def bootstrap_t(
    xs: list[float], reps: int = 10_000, seed: int = 0, level: float = 0.95
) -> tuple[float, float] | None:
    """A rough range for the mean: studentised bootstrap. None under ten values
    or when the values don't vary. Cached by its inputs, so a page that shows
    the same trades again doesn't redo ten thousand resamples."""
    return _bootstrap_cached(tuple(xs), reps, seed, level)
