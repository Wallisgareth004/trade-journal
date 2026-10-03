"""The old tracker (a 27-column CSV): read into the journal, once.

Its 27 columns are kept exactly as written (in `legacy_json`), so the
export gives the same values back. Rows whose id contains EXAMPLE are skipped,
as journal_stats.py does. Old trades are never graded: they predate the
checklist and the rulebook, so the journal shows them as "old tracker".

Error messages name columns and row numbers, never a value from the file.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db

SAST = ZoneInfo("Africa/Johannesburg")

LEGACY_COLUMNS = [
    "id",
    "asset",
    "direction",
    "setup",
    "grade",
    "entry_date",
    "entry",
    "stop",
    "size",
    "risk_pct",
    "risk_currency",
    "exit_date",
    "exit",
    "fees",
    "days_held",
    "result_currency",
    "result_R",
    "process",
    "followed_rules",
    "exit_reason",
    "confidence",
    "mistake",
    "lesson",
    "session_window_SAST",
    "planned_RR",
    "emotion_word",
    "screenshot",
]


class LayoutError(ValueError):
    """Not a file this importer knows. The message never quotes the file's rows."""


@dataclass
class MigrationResult:
    read: int
    examples: int
    new: int
    already: int
    matched: int = 0  # already in the journal from a TradingView import


MATCH_WINDOW = timedelta(minutes=30)


def same_trade(old, new) -> bool:
    """Is this old-tracker row (typed by hand) the same trade as this imported
    one? Same direction, entries within 30 minutes, the tracker's asset letters
    inside the TradingView symbol, and entry prices within 0.5%."""
    if (
        old["direction"] != new["direction"]
        or not old["entry_time"]
        or not new["entry_time"]
    ):
        return False
    gap = abs(
        datetime.fromisoformat(old["entry_time"])
        - datetime.fromisoformat(new["entry_time"])
    )
    if gap > MATCH_WINDOW:
        return False
    letters = re.sub(r"[^A-Z0-9]", "", (old["asset"] or "").upper())
    symbol = re.sub(r"[^A-Z0-9]", "", (new["asset"] or "").split(":")[-1].upper())
    if not letters or letters not in symbol:
        return False
    if (
        old["entry"]
        and new["entry"]
        and abs(old["entry"] - new["entry"]) > 0.005 * abs(new["entry"])
    ):
        return False
    return True


_TIME_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_time(value: str) -> str | None:
    """A SAST wall-clock time from the tracker, as ISO 8601 with its offset."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in _TIME_FORMATS:
        try:
            naive = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=SAST).isoformat(timespec="seconds")
    raise ValueError("unreadable time")


def _float(value: str) -> float | None:
    try:
        return float(value) if value.strip() else None
    except ValueError:
        return None


def _int(value: str) -> int | None:
    f = _float(value)
    return int(f) if f is not None and f.is_integer() else None


def read(path: Path | str) -> list[dict]:
    """The tracker's rows as dicts, after checking the layout is exactly the old one."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        body = list(reader)
    if header != LEGACY_COLUMNS:
        missing = [c for c in LEGACY_COLUMNS if c not in header]
        unexpected = len([c for c in header if c not in LEGACY_COLUMNS])
        detail = []
        if missing:
            detail.append(f"missing column(s): {', '.join(missing)}")
        if unexpected:
            detail.append(f"{unexpected} column(s) the old tracker never had")
        if not detail:
            detail.append("the columns are in a different order")
        raise LayoutError(
            f"not the old tracker's layout ({len(header)} columns where 27 are expected; "
            + "; ".join(detail)
            + ")"
        )
    out = []
    for n, cells in enumerate(body, start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != len(LEGACY_COLUMNS):
            raise LayoutError(f"line {n} has {len(cells)} fields where 27 are expected")
        out.append(dict(zip(LEGACY_COLUMNS, cells)))
    return out


def _fields(row: dict, line: int, now: str) -> dict:
    direction = row["direction"].strip().lower()
    if direction not in ("long", "short"):
        raise LayoutError(f"line {line}: direction must be long or short")
    try:
        entry_time = parse_time(row["entry_date"])
        exit_time = parse_time(row["exit_date"])
    except ValueError:
        raise LayoutError(
            f"line {line}: entry_date or exit_date isn't a time like 2026-09-30T15:42"
        ) from None
    return {
        "trade_ref": row["id"].strip(),
        "source": "legacy-csv",
        "asset": row["asset"].strip() or "?",
        "direction": direction,
        "setup": row["setup"].strip() or None,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry": _float(row["entry"]),
        "stop": _float(row["stop"]),
        "size": _float(row["size"]),
        "risk_pct": _float(row["risk_pct"]),
        "risk_currency": _float(row["risk_currency"]),
        "exit": _float(row["exit"]),
        "fees": _float(row["fees"]),
        "result_currency": _float(row["result_currency"]),
        "result_r": _float(row["result_R"]),
        "planned_rr": _float(row["planned_RR"]),
        "confidence": _int(row["confidence"]),
        "exit_reason": row["exit_reason"].strip() or None,
        "emotion_word": row["emotion_word"].strip() or None,
        "mistake": row["mistake"] or None,
        "lesson": row["lesson"] or None,
        "session_window": row["session_window_SAST"].strip() or None,
        "journaled_at": now,
        "legacy_json": json.dumps(row, ensure_ascii=False),
    }


def migrate(
    conn: sqlite3.Connection, path: Path | str, now: datetime | None = None
) -> MigrationResult:
    """Bring the tracker's real rows in. All or nothing; re-running adds nothing."""
    stamp = (now or datetime.now(SAST)).isoformat(timespec="seconds")
    raw = Path(path).read_bytes()
    rows = read(path)
    examples = [r for r in rows if "EXAMPLE" in r["id"].upper()]
    real = [
        (n, r) for n, r in enumerate(rows, start=2) if "EXAMPLE" not in r["id"].upper()
    ]
    prepared = []
    for line, row in real:
        if not row["id"].strip():
            raise LayoutError(f"line {line}: the id is empty")
        prepared.append(_fields(row, line, stamp))

    new = already = matched = 0
    with conn:
        imported = conn.execute(
            "SELECT * FROM trades WHERE source != 'legacy-csv'"
        ).fetchall()
        for fields in prepared:
            exists = conn.execute(
                "SELECT 1 FROM trades WHERE trade_ref = ?", (fields["trade_ref"],)
            ).fetchone()
            if exists:
                already += 1
                continue
            if sum(1 for t in imported if same_trade(fields, t)) == 1:
                matched += 1
                continue
            db.insert_trade(conn, fields, commit=False)
            new += 1
        conn.execute(
            "INSERT OR IGNORE INTO imports (sha256, kind, file_name, imported_at, rows, new_trades)"
            " VALUES (?, 'legacy-tracker', ?, ?, ?, ?)",
            (hashlib.sha256(raw).hexdigest(), Path(path).name, stamp, len(rows), new),
        )
    return MigrationResult(
        read=len(rows),
        examples=len(examples),
        new=new,
        already=already,
        matched=matched,
    )
