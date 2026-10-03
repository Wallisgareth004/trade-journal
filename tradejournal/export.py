"""exports/trades.csv: the journal as one CSV, compatible with the old tracker.

The old tracker's 27 columns come first, in their order and formats (SAST
times as YYYY-MM-DDTHH:MM, followed_rules as true/false), so tools written
for that tracker read it unchanged. The journal's own columns follow. Old-tracker rows give
back exactly the values as written.

`grade` is the process grade (A-C) and `process` its colour. Text a
spreadsheet would run as a formula gets a leading apostrophe.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from . import paths
from .legacy import LEGACY_COLUMNS, SAST

NEW_COLUMNS = [
    "target_price",
    "leverage",
    "weekday",
    "placed_time",
    "checklist_id",
    "grade_reasons",
    "rulebook_version",
    "source",
    "source_ref",
    "stop_placed",
    "stop_moved_further",
    "point_value",
    "balance_at_entry",
    "note",
    "flags",
    "created_at",
    "updated_at",
]
COLUMNS = LEGACY_COLUMNS + NEW_COLUMNS

COLOUR = {"A": "green", "B": "yellow", "C": "red"}
TEXT_COLUMNS = {
    "asset",
    "setup",
    "mistake",
    "lesson",
    "note",
    "emotion_word",
    "exit_reason",
    "session_window_SAST",
    "screenshot",
}


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def defuse(text: str) -> str:
    """Stop a spreadsheet from running typed text as a formula."""
    if text and (text[0] in "=+@\t\r" or (text[0] == "-" and not _is_number(text))):
        return "'" + text
    return text


def num(value, places: int | None = None) -> str:
    """A number as plain text: no exponent, no trailing zeros."""
    if value is None:
        return ""
    if places is not None:
        value = round(value, places)
    text = format(Decimal(repr(float(value))), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def minute(iso: str | None) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone(SAST).strftime("%Y-%m-%dT%H:%M")


def days_held(entry: str | None, exit_: str | None) -> str:
    if not entry or not exit_:
        return ""
    a = datetime.fromisoformat(entry).astimezone(SAST).date()
    b = datetime.fromisoformat(exit_).astimezone(SAST).date()
    return str((b - a).days)


def _old_columns(t: sqlite3.Row, screenshot: str) -> dict:
    computed = _computed_old_columns(t, screenshot)
    if t["legacy_json"]:
        # an old-tracker row: exactly what was written, wherever it was written
        return {**computed, **json.loads(t["legacy_json"])}
    return computed


def _computed_old_columns(t: sqlite3.Row, screenshot: str) -> dict:
    followed = t["followed_rules"]
    return {
        "id": t["trade_ref"],
        "asset": t["asset"],
        "direction": t["direction"],
        "setup": t["setup"] or "",
        "grade": t["grade"] or "",
        "entry_date": minute(t["entry_time"]),
        "entry": num(t["entry"]),
        "stop": num(t["stop"]),
        "size": num(t["size"]),
        "risk_pct": num(t["risk_pct"], 4),
        "risk_currency": num(t["risk_currency"], 4),
        "exit_date": minute(t["exit_time"]),
        "exit": num(t["exit"]),
        "fees": num(t["fees"], 4),
        "days_held": days_held(t["entry_time"], t["exit_time"]),
        "result_currency": num(t["result_currency"], 4),
        "result_R": num(t["result_r"], 4),
        "process": COLOUR.get(t["grade"] or "", ""),
        "followed_rules": "" if followed is None else ("true" if followed else "false"),
        "exit_reason": t["exit_reason"] or "",
        "confidence": "" if t["confidence"] is None else str(t["confidence"]),
        "mistake": t["mistake"] or "",
        "lesson": t["lesson"] or "",
        "session_window_SAST": t["session_window"] or "",
        "planned_RR": num(t["planned_rr"], 4),
        "emotion_word": t["emotion_word"] or "",
        "screenshot": screenshot,
    }


def _new_columns(t: sqlite3.Row) -> dict:
    moved = t["stop_moved_further"]
    return {
        "target_price": num(t["target"]),
        "leverage": t["leverage"] or "",
        "weekday": t["weekday"] or "",
        "placed_time": t["placed_time"] or "",
        "checklist_id": "" if t["checklist_id"] is None else str(t["checklist_id"]),
        "grade_reasons": t["grade_reasons_json"] or "",
        "rulebook_version": t["rulebook_version"] or "",
        "source": t["source"],
        "source_ref": t["source_ref"] or "",
        "stop_placed": t["stop_placed"] or "",
        "stop_moved_further": "" if moved is None else ("true" if moved else "false"),
        "point_value": num(t["point_value"]),
        "balance_at_entry": num(t["balance_at_entry"], 2),
        "note": t["note"] or "",
        "flags": t["flags_json"] or "",
        "created_at": t["created_at"],
        "updated_at": t["updated_at"],
    }


def rows(conn: sqlite3.Connection) -> list[dict]:
    shots = {
        r["trade_id"]: r["path"]
        for r in conn.execute(
            "SELECT trade_id, path FROM screenshots WHERE phase = 'entry' ORDER BY added_at DESC"
        )
    }
    out = []
    trades = conn.execute(
        "SELECT * FROM trades ORDER BY entry_time IS NULL, entry_time, trade_ref"
    ).fetchall()
    for t in trades:
        row = {**_old_columns(t, shots.get(t["id"], "")), **_new_columns(t)}
        out.append(
            {c: defuse(row[c]) if c in TEXT_COLUMNS else row[c] for c in COLUMNS}
        )
    return out


def write(conn: sqlite3.Connection) -> Path:
    """Rewrite exports/trades.csv (owner-only, atomic) and return its path."""
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows(conn))
    return paths.write_private(
        paths.exports_dir() / "trades.csv", buf.getvalue().encode("utf-8")
    )
