"""The process grade, A to C. It grades how the trade was taken, never what it made.

A  a checklist came before the entry with every killer item green; every rule
   held (trades a day, stop after losses, risk within tolerance, the stop in
   the order and never moved further away, planned R:R, the session window);
   and the trade was journaled within 24 h with an emotion word and a screenshot.
B  every rule held, with a housekeeping slip: no screenshot, no emotion word,
   or journaled late.
C  any rule broken, taken anyway after a red checklist, or no checklist.

Each trade is graded under the rulebook version in force on its entry day.
`grade_reasons` says, in plain words, what decided the grade. A note can be
added but a grade can't be changed: if the data is wrong, fix the data.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from . import rulebook
from .legacy import SAST

LATE = timedelta(hours=24)
EPS = 1e-9


def _when(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None


def _result(t) -> float | None:
    return t["result_r"] if t["result_r"] is not None else t["result_currency"]


def _num(x: float) -> str:
    return f"{round(x, 2):g}"


def judge(
    t, rb: rulebook.Rulebook, day: list, checklist, shots: list
) -> tuple[str, list[str]]:
    """The grade and its reasons for one trade, given the day's trades in entry order."""
    broken: list[str] = []
    notes: list[str] = []
    answers = (
        json.loads(checklist["answers_json"] or "{}") if checklist is not None else {}
    )

    if checklist is None:
        broken.append("There was no checklist before the entry")
    elif checklist["verdict"] in ("no-trade", "taken-anyway"):
        broken.append("The trade was taken anyway after a red checklist")

    entered = _when(t["entry_time"])
    position = next(i for i, d in enumerate(day, start=1) if d["id"] == t["id"])
    if position > rb.max_trades_per_day:
        broken.append(
            f"Trade {position} of the day; the max is {rb.max_trades_per_day}"
        )
    known_losses = sum(
        1
        for d in day
        if d["id"] != t["id"]
        and d["exit_time"]
        and entered is not None
        and _when(d["exit_time"]) <= entered
        and (_result(d) or 0) < 0
    )
    if known_losses >= rb.stop_after_losses:
        broken.append(
            f"Entered after {known_losses} losses today; the rulebook stops at {rb.stop_after_losses}"
        )

    if t["stop_placed"] == "none":
        broken.append("There was no stop in the order")
    elif t["stop_placed"] == "after-entry":
        broken.append("The stop was placed after the entry, not with the order")
    if t["stop_moved_further"] == 1:
        broken.append("The stop was moved further away")

    if t["planned_rr"] is not None:
        if t["planned_rr"] < rb.min_planned_rr - EPS:
            broken.append(
                f"The planned R:R {_num(t['planned_rr'])} is under the minimum of {rb.min_planned_rr:g}"
            )
    elif checklist is not None:
        if answers.get("planned_rr_ok") is False:
            broken.append(
                f"Your checklist said the planned R:R was under {rb.min_planned_rr:g}"
            )
        else:
            notes.append(
                "Note: no target order in the export, so the R:R check used your checklist answer"
            )

    if t["risk_pct"] is not None:
        allowed = rb.risk_pct * rb.risk_tolerance_pct / 100
        if abs(t["risk_pct"] - rb.risk_pct) > allowed + EPS:
            broken.append(
                f"The risk {_num(t['risk_pct'])}% is outside {rb.risk_pct:g}% ± {rb.risk_tolerance_pct:g}%"
            )
    elif checklist is not None:
        if answers.get("size_ok") is False:
            broken.append(f"Your checklist said the size wasn't 1R at {rb.risk_pct:g}%")
        else:
            notes.append(
                "Note: risk % unknown (no balance in the export), so the size check used your checklist answer"
            )

    if rb.windows_set and t["session_window"] == "outside":
        broken.append(
            f"Entered outside the session windows ({', '.join(rb.session_windows)})"
        )

    if broken:
        return "C", broken + notes

    slips: list[str] = []
    if not shots:
        slips.append("There is no screenshot")
    if not (t["emotion_word"] or (checklist is not None and checklist["emotion_word"])):
        slips.append("There is no emotion word")
    exited = _when(t["exit_time"])
    done = [
        x
        for x in [_when(t["journaled_at"]), *(_when(s["added_at"]) for s in shots)]
        if x is not None
    ]
    if exited is not None and done and max(done) - exited > LATE:
        slips.append("It was journaled more than 24 h after the exit")
    if slips:
        return "B", ["Every rule held", *slips, *notes]
    return "A", [
        "A checklist came before the entry, every killer item green",
        "Every rule held",
        "Journaled within 24 h with an emotion word and a screenshot",
        *notes,
    ]


def grading_start(
    rb: rulebook.Rulebook, first_checklist: datetime | None
) -> datetime | None:
    """When grading starts under this rulebook: None means from its first day.
    "first-checklist" waits for the first trade that came with a checklist."""
    if rb.grading_starts is None:
        return None
    if rb.grading_starts == "first-checklist":
        return first_checklist or datetime.max.replace(tzinfo=SAST)
    return datetime.fromisoformat(rb.grading_starts).replace(tzinfo=SAST)


def before_grading(t, rb: rulebook.Rulebook, first_checklist: datetime | None) -> bool:
    start = grading_start(rb, first_checklist)
    return start is not None and _when(t["entry_time"]) < start


def grade_all(conn: sqlite3.Connection) -> None:
    """(Re)grade every trade under its rulebook version; ungraded without one."""
    trades = conn.execute("SELECT * FROM trades").fetchall()
    by_day: dict = {}
    for t in trades:
        if t["entry_time"]:
            by_day.setdefault(
                _when(t["entry_time"]).astimezone(SAST).date(), []
            ).append(t)
    for day in by_day.values():
        day.sort(key=lambda d: (d["entry_time"], d["placed_time"] or "", d["id"]))
    books: dict = {}
    first_checklist = min(
        (
            _when(t["entry_time"])
            for t in trades
            if t["checklist_id"] and t["entry_time"] and t["source"] != "legacy-csv"
        ),
        default=None,
    )
    with conn:
        for t in trades:
            if t["rulebook_version"] and t["rulebook_version"] not in books:
                books[t["rulebook_version"]] = rulebook.get(conn, t["rulebook_version"])
            rb = books.get(t["rulebook_version"]) if t["rulebook_version"] else None
            if (
                t["source"] == "legacy-csv"
                or not t["rulebook_version"]
                or not t["entry_time"]
                or (rb is not None and before_grading(t, rb, first_checklist))
            ):
                conn.execute(
                    "UPDATE trades SET grade = NULL, followed_rules = NULL, grade_reasons_json = NULL WHERE id = ?",
                    (t["id"],),
                )
                continue
            if rb is None:
                continue
            checklist = (
                conn.execute(
                    "SELECT * FROM checklists WHERE id = ?", (t["checklist_id"],)
                ).fetchone()
                if t["checklist_id"]
                else None
            )
            shots = conn.execute(
                "SELECT * FROM screenshots WHERE trade_id = ?", (t["id"],)
            ).fetchall()
            day = by_day[_when(t["entry_time"]).astimezone(SAST).date()]
            g, reasons = judge(t, rb, day, checklist, shots)
            conn.execute(
                "UPDATE trades SET grade = ?, followed_rules = ?, grade_reasons_json = ? WHERE id = ?",
                (g, 1 if g in ("A", "B") else 0, json.dumps(reasons), t["id"]),
            )
