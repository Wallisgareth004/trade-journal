"""Everything the journal works out from the stored facts, in one place.

After any change (an import, a checklist, a rulebook version, a fix to the
data) `refresh` recomputes, for every trade that isn't from the old tracker:

  * 1R, the result in R and money, and the planned R:R, from the initial
    stop (or the stop entered by hand when the export can't show it);
  * the weekday, the rulebook version in force on the entry day, and the
    session window the entry fell in;
  * the account balance at entry (from Account history) and the risk in %;
  * flags: things to look at, in plain words;
  * the checklist each trade belongs to, and the process grade (A-C);

then rewrites exports/trades.csv. Old-tracker trades keep their values as
written.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from . import export, grade, match, rulebook
from .legacy import SAST
from .settings import Settings

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
REALISED_TOLERANCE = 0.02
CLOSE_MATCH = timedelta(minutes=2)
ACTION = re.compile(
    r"close (long|short) position for symbol (\S+) at price", re.IGNORECASE
)

NO_STOP = "no stop order in the export"
STOP_UNKNOWN = (
    "the stop in the export is at or past the entry, so the initial stop isn't known"
    " (moved to break-even?): enter the initial stop on the trade page"
)
REALISED_DIFFERS = (
    "TradingView's realised P&L differs from the journal's by more than 2%: check the point value"
    " for this symbol in settings.toml"
)


def _letters(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", symbol.split(":")[-1].upper())


def money(t: sqlite3.Row, stop: float | None) -> dict:
    """1R, result and planned R:R. R is None when the initial stop isn't known."""
    sign = 1 if t["direction"] == "long" else -1
    pv = t["point_value"] or 1.0
    size = t["size"] or 0.0
    fees = t["fees"] or 0.0
    out = {
        "risk_currency": None,
        "result_currency": None,
        "result_r": None,
        "planned_rr": None,
    }
    if t["entry"] is not None and t["exit"] is not None:
        out["result_currency"] = (t["exit"] - t["entry"]) * sign * size * pv - fees
    if stop is None or t["entry"] is None:
        return out
    risk_unit = (t["entry"] - stop) * sign
    if risk_unit <= 0:
        return out
    out["risk_currency"] = risk_unit * size * pv
    if out["result_currency"] is not None and out["risk_currency"]:
        out["result_r"] = out["result_currency"] / out["risk_currency"]
    if t["target"] is not None:
        out["planned_rr"] = (t["target"] - t["entry"]) * sign / risk_unit
    return out


def _balance_at(balances: list, when: datetime) -> float | None:
    before = [b for b in balances if b["at"] <= when and b["balance_after"] is not None]
    if before:
        return max(before, key=lambda b: b["at"])["balance_after"]
    after = [b for b in balances if b["at"] >= when and b["balance_before"] is not None]
    if after:
        return min(after, key=lambda b: b["at"])["balance_before"]
    return None


def _realised(balances: list, t: sqlite3.Row) -> float | None:
    if not t["exit_time"]:
        return None
    closed = datetime.fromisoformat(t["exit_time"])
    sym = _letters(t["asset"])
    for b in balances:
        m = ACTION.search(b["action"] or "")
        if not m or m.group(1).lower() != t["direction"]:
            continue
        if _letters(m.group(2)) != sym or abs(b["at"] - closed) > CLOSE_MATCH:
            continue
        return b["realized_pnl"]
    return None


def refresh(conn: sqlite3.Connection, settings: Settings) -> None:
    balances = [
        {**dict(b), "at": datetime.fromisoformat(b["time"])}
        for b in conn.execute("SELECT * FROM balances ORDER BY time")
    ]
    trades = conn.execute(
        "SELECT * FROM trades WHERE source != 'legacy-csv'"
    ).fetchall()
    with conn:
        for t in trades:
            overrides = json.loads(t["overrides_json"] or "{}")
            stop = overrides.get("stop", t["stop"])
            fields = money(t, stop)
            flags = []
            if (
                t["source"] == "tradingview-csv"
                and t["stop"] is None
                and "stop" not in overrides
            ):
                flags.append(NO_STOP)
            elif stop is not None and fields["risk_currency"] is None:
                flags.append(STOP_UNKNOWN)

            fields.update(weekday=None, rulebook_version=None, session_window=None)
            fields.update(balance_at_entry=None, risk_pct=None)
            if t["entry_time"]:
                entered = datetime.fromisoformat(t["entry_time"]).astimezone(SAST)
                fields["weekday"] = WEEKDAYS[entered.weekday()]
                rb = rulebook.version_for(conn, entered.date())
                if rb is not None:
                    fields["rulebook_version"] = rb.version
                    if rb.windows_set:
                        fields["session_window"] = (
                            rb.window_at(entered.time()) or "outside"
                        )
                balance = _balance_at(balances, entered)
                fields["balance_at_entry"] = balance
                if balance and fields["risk_currency"]:
                    fields["risk_pct"] = fields["risk_currency"] / balance * 100

            realised = _realised(balances, t)
            gross = None
            if fields["result_currency"] is not None:
                gross = fields["result_currency"] + (t["fees"] or 0.0)
            if realised is not None and gross is not None:
                near = lambda a, b: abs(a - b) <= max(REALISED_TOLERANCE * abs(b), 0.01)  # noqa: E731
                if not (
                    near(realised, gross) or near(realised, fields["result_currency"])
                ):
                    flags.append(REALISED_DIFFERS)

            fields["flags_json"] = json.dumps(flags) if flags else None
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE trades SET {sets} WHERE id = ?", (*fields.values(), t["id"])
            )
    match.match_checklists(conn, settings.checklist_window_minutes)
    grade.grade_all(conn)
    export.write(conn)
