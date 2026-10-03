"""The pre-entry checklist.

Before an entry the trader answers three questions (stop in the order, planned R:R,
size), and the journal adds three checks from its own data (trades today,
losses today, inside a session window). Every killer item green says "Go";
any red says "Rulebook says no trade". The trader can still take it ("taking it
anyway"), and that trade will grade C. The journal can't block TradingView:
the checklist is a pause point, and its timestamp is the proof it came first.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime

from . import rulebook
from .legacy import SAST
from .rulebook import TAPPED_ITEMS, Rulebook


@dataclass
class AutoChecks:
    trades_today: int
    max_trades: int
    losses_today: int
    stop_after: int
    in_session: bool | None  # None: no window is set, so the check is off
    window: str | None
    last_import: str | None

    @property
    def under_max(self) -> bool:
        return self.trades_today < self.max_trades

    @property
    def under_stop_after(self) -> bool:
        return self.losses_today < self.stop_after


def _day(iso: str | None):
    return datetime.fromisoformat(iso).astimezone(SAST).date() if iso else None


def auto_checks(conn: sqlite3.Connection, rb: Rulebook, now: datetime) -> AutoChecks:
    today = now.astimezone(SAST).date()
    trades = conn.execute(
        "SELECT entry_time, exit_time, result_r, result_currency FROM trades"
    ).fetchall()
    imported = sum(1 for t in trades if _day(t["entry_time"]) == today)
    pending = sum(
        1
        for c in conn.execute(
            "SELECT filled_at FROM checklists WHERE verdict IN ('go', 'taken-anyway')"
            " AND matched_trade_id IS NULL AND (outcome IS NULL OR outcome = 'taken')"
        )
        if _day(c["filled_at"]) == today
    )
    losses = 0
    for t in trades:
        if (
            _day(t["exit_time"]) != today
            or datetime.fromisoformat(t["exit_time"]) > now
        ):
            continue
        result = t["result_r"] if t["result_r"] is not None else t["result_currency"]
        if result is not None and result < 0:
            losses += 1
    window = rb.window_at(now.astimezone(SAST).time()) if rb.windows_set else None
    last = conn.execute("SELECT max(imported_at) FROM imports").fetchone()[0]
    return AutoChecks(
        trades_today=imported + pending,
        max_trades=rb.max_trades_per_day,
        losses_today=losses,
        stop_after=rb.stop_after_losses,
        in_session=(window is not None) if rb.windows_set else None,
        window=window,
        last_import=last,
    )


def verdict(rb: Rulebook, answers: dict, auto: AutoChecks) -> tuple[str, list[str]]:
    """("go", []) or ("no-trade", the red killer items in the rulebook's order)."""
    green = {
        "under_max": auto.under_max,
        "under_stop_after": auto.under_stop_after,
        "in_session": auto.in_session is not False,
    }
    reds = []
    for item in rb.killer_items:
        ok = bool(answers.get(item)) if item in TAPPED_ITEMS else green[item]
        if not ok:
            reds.append(item)
    return ("no-trade" if reds else "go"), reds


def label(item: str, rb: Rulebook, auto: AutoChecks | None = None) -> str:
    """The item in plain words, as the page shows it."""
    a = auto
    return {
        "stop_in_order": "Stop set inside the order (bracket)",
        "planned_rr_ok": f"Planned R:R at least {rb.min_planned_rr:g} on the position tool",
        "size_ok": f"Size = 1R at {rb.risk_pct:g}% of the account",
        "under_max": f"Trades today: {a.trades_today} of {a.max_trades}"
        if a
        else "Under the trades-a-day max",
        "under_stop_after": f"Losses today: {a.losses_today} of {a.stop_after}"
        if a
        else "Under the stop-after-losses count",
        "in_session": (
            "No session window set (check off)"
            if a and a.in_session is None
            else f"Inside the session window {a.window}"
            if a and a.in_session
            else "Outside every session window"
            if a
            else "Inside a session window"
        ),
    }[item]


def save(
    conn: sqlite3.Connection,
    rb: Rulebook,
    now: datetime,
    *,
    direction: str,
    setup: str,
    answers: dict,
    emotion_word: str | None = None,
    confidence: int | None = None,
    asset: str | None = None,
) -> int:
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    if setup not in rb.setups:
        raise ValueError("setup must be one of the rulebook's setups")
    if any(not isinstance(answers.get(k), bool) for k in TAPPED_ITEMS):
        raise ValueError("answer every question")
    if confidence is not None and not (
        isinstance(confidence, int) and 1 <= confidence <= 5
    ):
        raise ValueError("confidence is 1 to 5")
    emotion_word = (emotion_word or "").strip()[:30] or None
    asset = (asset or "").strip()[:30] or None

    auto = auto_checks(conn, rb, now)
    v, _ = verdict(rb, answers, auto)
    in_force = rulebook.version_for(conn, now.astimezone(SAST).date())
    cur = conn.execute(
        "INSERT INTO checklists (filled_at, asset, direction, setup, answers_json, auto_json, emotion_word,"
        " confidence, verdict, rulebook_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now.astimezone(SAST).isoformat(timespec="seconds"),
            asset,
            direction,
            setup,
            json.dumps({k: answers[k] for k in TAPPED_ITEMS}),
            json.dumps(asdict(auto)),
            emotion_word,
            confidence,
            v,
            in_force.version if in_force else None,
        ),
    )
    conn.commit()
    return cur.lastrowid


def take_anyway(conn: sqlite3.Connection, checklist_id: int) -> None:
    row = conn.execute(
        "SELECT verdict FROM checklists WHERE id = ?", (checklist_id,)
    ).fetchone()
    if row is None or row["verdict"] != "no-trade":
        raise ValueError("only a red checklist can be taken anyway")
    conn.execute(
        "UPDATE checklists SET verdict = 'taken-anyway' WHERE id = ?", (checklist_id,)
    )
    conn.commit()


def skip(conn: sqlite3.Connection, checklist_id: int) -> None:
    row = conn.execute(
        "SELECT matched_trade_id FROM checklists WHERE id = ?", (checklist_id,)
    ).fetchone()
    if row is None or row["matched_trade_id"] is not None:
        raise ValueError("that checklist already belongs to a trade")
    conn.execute(
        "UPDATE checklists SET outcome = 'skipped' WHERE id = ?", (checklist_id,)
    )
    conn.commit()
