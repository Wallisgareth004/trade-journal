"""Synthetic TradingView paper-trading exports, for tests and the public demo.

Nothing here is a real trade. The History layout follows TradingView's
documented export (its column headers are confirmed against a real export
before the importer is trusted). Each made-up trade is a bracket order: an
entry, a stop and a target placed with it; one of them closes the trade and
the other is cancelled, or a market order closes it by hand.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta

HISTORY_HEADER = [
    "Symbol",
    "Side",
    "Type",
    "Qty",
    "Limit Price",
    "Stop Price",
    "Fill Price",
    "Status",
    "Commission",
    "Placing Time",
    "Closing Time",
    "Order ID",
    "Level ID",
    "Leverage",
    "Margin",
]
ACCOUNT_HEADER = [
    "Time",
    "Balance Before",
    "Balance After",
    "Realized P&L (value)",
    "Action",
]
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class Spec:
    """One made-up bracket trade. Times are naive, in the export's timezone."""

    symbol: str
    direction: str  # long | short
    qty: float
    entry: float
    stop: float | None
    target: float | None
    placed: datetime
    exit_kind: str  # target | stop | manual | open
    exit_price: float | None = None
    filled: datetime | None = None  # entry fill time (default: placed)
    closed: datetime | None = None  # exit time (default: placed + 30 min)
    entry_type: str = "Market"
    stop_placed: datetime | None = None  # default: with the entry order
    commission: float = 0.0  # per filled order
    extra_stops: list = field(
        default_factory=list
    )  # [(placed, price)] later stop orders


def _t(dt: datetime | None) -> str:
    return dt.strftime(TIME_FORMAT) if dt else ""


def _p(x: float | None) -> str:
    return "" if x is None else f"{x:g}"


def history_rows(specs: list[Spec], first_id: int = 1000) -> list[dict]:
    rows: list[dict] = []
    next_id = first_id
    for s in specs:
        side_in = "Buy" if s.direction == "long" else "Sell"
        side_out = "Sell" if s.direction == "long" else "Buy"
        filled = s.filled or s.placed
        closed = s.closed or (filled + timedelta(minutes=30))
        stop_placed = s.stop_placed or s.placed

        def row(**kw) -> dict:
            nonlocal next_id
            next_id += 1
            base = {h: "" for h in HISTORY_HEADER}
            base.update(
                {
                    "Symbol": s.symbol,
                    "Qty": _p(s.qty),
                    "Order ID": str(next_id),
                    "Leverage": "1:1",
                }
            )
            base.update(kw)
            return base

        rows.append(
            row(
                Side=side_in,
                Type=s.entry_type,
                **{
                    "Limit Price": _p(s.entry) if s.entry_type == "Limit" else "",
                    "Fill Price": _p(s.entry),
                    "Status": "Filled",
                    "Commission": _p(s.commission) if s.commission else "",
                    "Placing Time": _t(s.placed),
                    "Closing Time": _t(filled),
                },
            )
        )
        stop_hit = s.exit_kind == "stop"
        target_hit = s.exit_kind == "target"
        ends = s.exit_kind != "open"
        if s.stop is not None:
            rows.append(
                row(
                    Side=side_out,
                    Type="Stop",
                    **{
                        "Stop Price": _p(s.stop),
                        "Fill Price": _p(s.stop) if stop_hit else "",
                        "Status": "Filled"
                        if stop_hit
                        else ("Cancelled" if ends else "Working"),
                        "Commission": _p(s.commission)
                        if stop_hit and s.commission
                        else "",
                        "Placing Time": _t(stop_placed),
                        "Closing Time": _t(closed) if ends else "",
                    },
                )
            )
        for when, price in s.extra_stops:
            rows.append(
                row(
                    Side=side_out,
                    Type="Stop",
                    **{
                        "Stop Price": _p(price),
                        "Status": "Cancelled",
                        "Placing Time": _t(when),
                        "Closing Time": _t(closed),
                    },
                )
            )
        if s.target is not None:
            rows.append(
                row(
                    Side=side_out,
                    Type="Limit",
                    **{
                        "Limit Price": _p(s.target),
                        "Fill Price": _p(s.target) if target_hit else "",
                        "Status": "Filled"
                        if target_hit
                        else ("Cancelled" if ends else "Working"),
                        "Commission": _p(s.commission)
                        if target_hit and s.commission
                        else "",
                        "Placing Time": _t(s.placed),
                        "Closing Time": _t(closed) if ends else "",
                    },
                )
            )
        if s.exit_kind == "manual":
            rows.append(
                row(
                    Side=side_out,
                    Type="Market",
                    **{
                        "Fill Price": _p(s.exit_price),
                        "Status": "Filled",
                        "Commission": _p(s.commission) if s.commission else "",
                        "Placing Time": _t(closed),
                        "Closing Time": _t(closed),
                    },
                )
            )
    rows.reverse()  # TradingView lists the newest first
    return rows


def account_rows(
    entries: list[tuple[datetime, float, float, str, float, float, float]],
) -> list[dict]:
    """(time, balance_before, realized, symbol, close_price, qty, avg_price) per closed position."""
    out = []
    for when, before, realized, symbol, close_price, qty, avg in entries:
        side = "long" if realized * (close_price - avg) >= 0 else "short"
        out.append(
            {
                "Time": _t(when),
                "Balance Before": f"{before:.2f}",
                "Balance After": f"{before + realized:.2f}",
                "Realized P&L (value)": f"{realized:.2f}",
                "Action": (
                    f"Close {side} position for symbol {symbol} at price {close_price:g} for {qty:g} units."
                    f" Position AVG Price was {avg:g}"
                ),
            }
        )
    return out


def to_csv(header: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


SLOTS = ((9, 30), (12, 0), (14, 30), (16, 45))


def generate(
    n: int,
    seed: int = 0,
    start: datetime = datetime(2026, 10, 5),
    symbol: str = "DEMO:XYZUSD",
) -> tuple[list[Spec], list[dict]]:
    """n made-up bracket trades on weekdays, two to four a day, never overlapping,
    and the truth about each: its direction, how it closed and the R it made."""
    import random

    rng = random.Random(seed)
    price = 2000.0
    day = start
    specs: list[Spec] = []
    truth: list[dict] = []
    while len(specs) < n:
        if day.weekday() < 5:
            for hour, minute in sorted(rng.sample(SLOTS, rng.randint(2, 4))):
                if len(specs) == n:
                    break
                price = max(100.0, price + rng.gauss(0, 6))
                direction = rng.choice(("long", "short"))
                sign = 1 if direction == "long" else -1
                entry = round(price, 2)
                risk = round(rng.uniform(1, 5), 2)
                rr = rng.choice((1.5, 2.0, 2.5, 3.0))
                stop = round(entry - sign * risk, 2)
                target = round(entry + sign * rr * risk, 2)
                kind = rng.choices(("target", "stop", "manual"), weights=(38, 47, 15))[
                    0
                ]
                exit_price = {"target": target, "stop": stop}.get(kind)
                if kind == "manual":
                    exit_price = round(
                        rng.uniform(min(stop, target), max(stop, target)), 2
                    )
                placed = day.replace(hour=hour, minute=minute)
                specs.append(
                    Spec(
                        symbol=symbol,
                        direction=direction,
                        qty=rng.choice((1, 2, 5)),
                        entry=entry,
                        stop=stop,
                        target=target,
                        placed=placed,
                        exit_kind=kind,
                        exit_price=exit_price if kind == "manual" else None,
                        closed=placed + timedelta(minutes=rng.randint(15, 80)),
                    )
                )
                risk_unit = (entry - stop) * sign
                truth.append(
                    {
                        "direction": direction,
                        "exit_kind": kind,
                        "placed": placed.isoformat(),
                        "r": (exit_price - entry) * sign / risk_unit,
                        "planned_rr": (target - entry) * sign / risk_unit,
                    }
                )
        day += timedelta(days=1)
    return specs, truth


@dataclass
class Journal:
    specs: list
    truth: list
    checklists: list


EMOTIONS = ("calm", "unsure", "rushed", "bored", "FOMO", "revenge")
SETUPS = ("sweep-BOS-FVG", "trendline-FVG")


def journal(
    n: int,
    seed: int = 0,
    start: datetime = datetime(2026, 10, 5),
    max_per_day: int = 3,
    stop_after: int = 2,
    min_rr: float = 2.0,
) -> Journal:
    """A made-up journal: n trades with a checklist before most of them, a few
    deliberate rule breaks, and some checklists that never became trades.

    Each trade's truth carries the breaks the generator chose and the grade it
    therefore expects (B when nothing broke: made-up imports have no screenshot).
    """
    import random

    from .legacy import SAST

    specs, truth = generate(n, seed=seed, start=start)
    rng = random.Random(seed + 1)
    checklists: list[dict] = []
    per_day: dict[str, list[dict]] = {}
    for spec, t in zip(specs, truth):
        day = t["placed"][:10]
        earlier = per_day.setdefault(day, [])
        breaks = []
        roll = rng.random()
        no_checklist = roll < 0.08
        anyway = 0.08 <= roll < 0.13
        if no_checklist:
            breaks.append("no checklist")
        if anyway:
            breaks.append("taken anyway")
        if t["planned_rr"] < min_rr - 1e-9:
            breaks.append("planned R:R")
        if rng.random() < 0.06:
            spec.stop_placed = spec.placed + timedelta(minutes=2)
            breaks.append("stop after entry")
        if len(earlier) + 1 > max_per_day:
            breaks.append("max a day")
        if sum(1 for e in earlier if e["r"] < 0) >= stop_after:
            breaks.append("after losses")
        if not no_checklist:
            checklists.append(
                {
                    "filled_at": (
                        spec.placed - timedelta(minutes=rng.randint(1, 8))
                    ).replace(tzinfo=SAST),
                    "direction": spec.direction,
                    "setup": rng.choice(SETUPS),
                    "asset": rng.choice((None, spec.symbol.split(":")[-1])),
                    "answers": {
                        "stop_in_order": True,
                        "planned_rr_ok": True,
                        "size_ok": not anyway,
                    },
                    "verdict": "taken-anyway" if anyway else "go",
                    "emotion_word": rng.choice(EMOTIONS),
                    "confidence": rng.randint(1, 5),
                    "skipped": False,
                }
            )
        t["breaks"] = breaks
        t["expected_grade"] = "C" if breaks else "B"
        earlier.append(t)
    for day in sorted(per_day):
        if rng.random() < 0.35:
            when = datetime.fromisoformat(day).replace(hour=11, minute=15, tzinfo=SAST)
            go = rng.random() < 0.3
            checklists.append(
                {
                    "filled_at": when,
                    "direction": rng.choice(("long", "short")),
                    "setup": rng.choice(SETUPS),
                    "asset": None,
                    "answers": {
                        "stop_in_order": True,
                        "planned_rr_ok": go,
                        "size_ok": True,
                    },
                    "verdict": "go" if go else "no-trade",
                    "emotion_word": rng.choice(EMOTIONS),
                    "confidence": rng.randint(1, 5),
                    "skipped": True,
                }
            )
    checklists.sort(key=lambda c: c["filled_at"])
    return Journal(specs, truth, checklists)


def insert_checklists(conn, checklists: list[dict]) -> None:
    """Write made-up checklists straight into a journal (tests and the demo)."""
    import json

    for c in checklists:
        conn.execute(
            "INSERT INTO checklists (filled_at, asset, direction, setup, answers_json, auto_json, emotion_word,"
            " confidence, verdict, outcome) VALUES (?, ?, ?, ?, ?, '{}', ?, ?, ?, ?)",
            (
                c["filled_at"].isoformat(timespec="seconds"),
                c["asset"],
                c["direction"],
                c["setup"],
                json.dumps(c["answers"]),
                c["emotion_word"],
                c["confidence"],
                c["verdict"],
                "skipped" if c["skipped"] else None,
            ),
        )
    conn.commit()
