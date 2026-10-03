"""A checklist belongs to the first trade whose entry order was placed within
the window after it: same direction, and the same asset if one was named.

One checklist covers one trade. A trade that has none can't grade above C;
a checklist that never meets a trade is a skipped setup, a process result
too. Old-tracker trades never take a checklist: they came before it existed.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta


def _letters(text: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").split(":")[-1].upper())


def asset_fits(named: str | None, symbol: str) -> bool:
    if not named:
        return True
    a, b = _letters(named), _letters(symbol)
    return bool(a) and (a in b or b in a)


def match_checklists(conn: sqlite3.Connection, window_minutes: int) -> int:
    """Link unmatched checklists to unmatched trades. Returns how many were linked."""
    window = timedelta(minutes=window_minutes)
    trades = conn.execute(
        "SELECT * FROM trades WHERE source != 'legacy-csv' AND checklist_id IS NULL"
        " AND coalesce(placed_time, entry_time) IS NOT NULL"
    ).fetchall()
    trades = sorted(
        trades,
        key=lambda t: datetime.fromisoformat(t["placed_time"] or t["entry_time"]),
    )
    free = conn.execute(
        "SELECT * FROM checklists WHERE matched_trade_id IS NULL"
    ).fetchall()
    taken: set[int] = set()
    linked = 0
    for t in trades:
        anchor = datetime.fromisoformat(t["placed_time"] or t["entry_time"])
        fits = [
            c
            for c in free
            if c["id"] not in taken
            and anchor - window <= datetime.fromisoformat(c["filled_at"]) <= anchor
            and (c["direction"] is None or c["direction"] == t["direction"])
            and asset_fits(c["asset"], t["asset"])
        ]
        if not fits:
            continue
        c = max(fits, key=lambda c: datetime.fromisoformat(c["filled_at"]))
        taken.add(c["id"])
        conn.execute(
            "UPDATE trades SET checklist_id = ?, setup = coalesce(setup, ?), emotion_word = coalesce(emotion_word, ?),"
            " confidence = coalesce(confidence, ?) WHERE id = ?",
            (c["id"], c["setup"], c["emotion_word"], c["confidence"], t["id"]),
        )
        conn.execute(
            "UPDATE checklists SET matched_trade_id = ?, outcome = 'taken' WHERE id = ?",
            (t["id"], c["id"]),
        )
        linked += 1
    conn.commit()
    return linked
