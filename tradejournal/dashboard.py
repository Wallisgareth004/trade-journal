"""The dashboard's numbers and the weekly review's, from the journal.

Rule adherence comes first; money stays inside a trade's own page; nothing
here counts days in a row.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta

from . import checklist, stats
from .legacy import SAST
from .rulebook import Rulebook

BUCKET_KEYS = (  # key, title, the empty group's name, a fixed order
    ("setup", "By setup", "no setup", ()),
    ("session_window", "By session window", "no window set", ()),
    (
        "weekday",
        "By weekday",
        "no day",
        ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    ),
    ("emotion_word", "By emotion word", "no emotion word", ()),
)
PENDING = timedelta(hours=24)


def _when(t) -> str:
    return t["exit_time"] or t["entry_time"] or ""


def skipped_setups(
    conn: sqlite3.Connection,
    now: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
) -> int:
    """Checklists that never became a trade: marked skipped, or unmatched for a day."""
    n = 0
    for c in conn.execute(
        "SELECT filled_at, outcome, matched_trade_id FROM checklists"
    ):
        filled = datetime.fromisoformat(c["filled_at"])
        if start is not None and not (start <= filled < end):
            continue
        if c["outcome"] == "skipped" or (
            c["matched_trade_id"] is None and now - filled > PENDING
        ):
            n += 1
    return n


def build(conn: sqlite3.Connection, rb: Rulebook, now: datetime) -> dict:
    trades = sorted(conn.execute("SELECT * FROM trades").fetchall(), key=_when)
    graded = [t for t in trades if t["grade"] is not None]
    rs = [t["result_r"] for t in graded if t["result_r"] is not None and t["exit_time"]]
    return {
        "adherence": stats.adherence(graded),
        "grades": Counter(t["grade"] for t in graded),
        "checkpoint": stats.checkpoint(
            graded,
            rb.checkpoint_trades,
            rb.rule_following_threshold_pct,
            rb.checkpoint_reading,
        ),
        "today": checklist.auto_checks(conn, rb, now),
        "completion": stats.checklist_completion(graded),
        "skipped": skipped_setups(conn, now),
        "summary": stats.summary([t["result_r"] for t in graded]),
        "curve": stats.equity_curve(rs),
        "max_drawdown": stats.max_drawdown(rs),
        "histogram": stats.histogram(rs),
        "four": stats.four_types(graded),
        "buckets": [
            (title, stats.buckets(graded, key, order, empty))
            for key, title, empty, order in BUCKET_KEYS
        ],
        "flagged": [t for t in trades if t["flags_json"]],
        "old_tracker": sum(1 for t in trades if t["source"] == "legacy-csv"),
        "before_rulebook": sum(
            1
            for t in trades
            if t["source"] != "legacy-csv" and t["rulebook_version"] is None
        ),
        "before_grading": sum(
            1
            for t in trades
            if t["source"] != "legacy-csv"
            and t["rulebook_version"]
            and t["grade"] is None
        ),
    }


def week_bounds(week: str) -> tuple[date, date]:
    """'2026-W41' -> (Monday, the Monday after). ValueError if it isn't a week."""
    year, _, num = week.partition("-W")
    monday = date.fromisocalendar(int(year), int(num), 1)
    return monday, monday + timedelta(days=7)


def week_of(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def week(conn: sqlite3.Connection, week_id: str, now: datetime) -> dict:
    monday, next_monday = week_bounds(week_id)
    start = datetime.combine(monday, datetime.min.time(), SAST)
    end = datetime.combine(next_monday, datetime.min.time(), SAST)
    trades = [
        t
        for t in conn.execute("SELECT * FROM trades ORDER BY entry_time").fetchall()
        if t["entry_time"] and start <= datetime.fromisoformat(t["entry_time"]) < end
    ]
    graded = [t for t in trades if t["grade"] is not None]
    row = conn.execute(
        "SELECT answers_json FROM reviews WHERE week = ?", (week_id,)
    ).fetchone()
    return {
        "week": week_id,
        "monday": monday,
        "trades": trades,
        "by_grade": {
            g: [t for t in graded if t["grade"] == g] for g in ("C", "B", "A")
        },
        "ungraded": [t for t in trades if t["grade"] is None],
        "adherence": stats.adherence(graded),
        "completion": stats.checklist_completion(graded),
        "four": stats.four_types(graded),
        "skipped": skipped_setups(conn, now, start, end),
        "answers": json.loads(row[0]) if row else {},
        "previous": week_of(monday - timedelta(days=7)),
        "next": week_of(next_monday),
    }
