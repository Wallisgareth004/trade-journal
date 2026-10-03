"""The rulebook: the trading rules, in config, never in code.

~/Library/Application Support/trade-journal/rulebook.toml holds the current
version. Every version the journal has seen is recorded in the database, and
each trade is graded under the version in force on its entry day.

A version marked `placeholder = true` holds stand-in numbers: they may change
at any time, and the trades under them are re-graded. Once the real
numbers are set (`placeholder = false`) and a trade is graded under them, they lock:
new numbers need a new version with its own effective_from date.
"""

from __future__ import annotations

import json
import re
import sqlite3
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from . import db

KILLER_ITEMS = (
    "stop_in_order",
    "planned_rr_ok",
    "size_ok",
    "in_session",
    "under_max",
    "under_stop_after",
)
TAPPED_ITEMS = ("stop_in_order", "planned_rr_ok", "size_ok")
AUTO_ITEMS = ("in_session", "under_max", "under_stop_after")

_WINDOW = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,20}$")


class RulebookError(ValueError):
    """The rulebook can't be used as written; the message names the key."""


@dataclass(frozen=True)
class Rulebook:
    version: str
    effective_from: date
    placeholder: bool
    max_trades_per_day: int
    stop_after_losses: int
    risk_pct: float
    min_planned_rr: float
    risk_tolerance_pct: float
    session_windows: tuple[str, ...]
    rule_following_threshold_pct: float
    checkpoint_trades: int
    setups: tuple[str, ...]
    emotion_words: tuple[str, ...]
    killer_items: tuple[str, ...]
    checkpoint_reading: str = "point"
    grading_starts: str | None = None  # "first-checklist" or "YYYY-MM-DDTHH:MM" (SAST)

    @property
    def windows_set(self) -> bool:
        return bool(self.session_windows)

    def window_at(self, t: time) -> str | None:
        """The window (its "HH:MM-HH:MM" label) that holds this SAST time, if any."""
        for label in self.session_windows:
            start, end = _window_times(label)
            inside = start <= t < end if start < end else (t >= start or t < end)
            if inside:
                return label
        return None

    def numbers(self) -> dict:
        return {
            "version": self.version,
            "effective_from": self.effective_from.isoformat(),
            "placeholder": self.placeholder,
            "max_trades_per_day": self.max_trades_per_day,
            "stop_after_losses": self.stop_after_losses,
            "risk_pct": self.risk_pct,
            "min_planned_rr": self.min_planned_rr,
            "risk_tolerance_pct": self.risk_tolerance_pct,
            "session_windows_sast": list(self.session_windows),
            "rule_following_threshold_pct": self.rule_following_threshold_pct,
            "checkpoint_trades": self.checkpoint_trades,
            "setups": list(self.setups),
            "emotion_words": list(self.emotion_words),
            "killer_items": list(self.killer_items),
            "checkpoint_reading": self.checkpoint_reading,
            "grading_starts": self.grading_starts,
        }


def _window_times(label: str) -> tuple[time, time]:
    m = _WINDOW.match(label)
    if not m:
        raise ValueError(label)
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    return time(h1, m1), time(h2, m2)


def _int(raw: dict, key: str, low: int) -> int:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, int) or v < low:
        raise RulebookError(
            f"{key} must be a whole number of at least {low} (got {v!r})"
        )
    return v


def _num(
    raw: dict, key: str, low: float, high: float, *, low_open: bool = False
) -> float:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RulebookError(f"{key} must be a number (got {v!r})")
    too_low = v <= low if low_open else v < low
    if too_low or v > high:
        bound = f"above {low}" if low_open else f"at least {low}"
        raise RulebookError(f"{key} must be {bound} and at most {high} (got {v!r})")
    return float(v)


def _words(raw: dict, key: str, *, allow_empty: bool) -> tuple[str, ...]:
    v = raw[key]
    if not isinstance(v, list) or not all(isinstance(w, str) and w.strip() for w in v):
        raise RulebookError(f"{key} must be a list of words")
    if not v and not allow_empty:
        raise RulebookError(f"{key} needs at least one entry")
    return tuple(w.strip() for w in v)


REQUIRED = (
    "version",
    "effective_from",
    "max_trades_per_day",
    "stop_after_losses",
    "risk_pct",
    "min_planned_rr",
    "risk_tolerance_pct",
    "session_windows_sast",
    "rule_following_threshold_pct",
    "checkpoint_trades",
    "setups",
    "emotion_words",
    "killer_items",
)
OPTIONAL = ("placeholder", "checkpoint_reading", "grading_starts")
READINGS = ("point", "interval")


def _grading_start(value) -> str | None:
    """None, "first-checklist", or a SAST time as YYYY-MM-DDTHH:MM. Trades
    entered before it are shown but not graded or counted."""
    if value is None or value == "first-checklist":
        return value
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M")
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00"
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            return when.strftime("%Y-%m-%dT%H:%M")
    raise RulebookError(
        f'grading_starts must be "first-checklist" or a time like "2026-10-05T14:00" (got {value!r})'
    )


def from_dict(raw: dict) -> Rulebook:
    unknown = sorted(set(raw) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        raise RulebookError(
            f"unknown key(s): {', '.join(unknown)} (a typo? known keys: {', '.join(REQUIRED + OPTIONAL)})"
        )
    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise RulebookError(f"missing key(s): {', '.join(missing)}")

    version = raw["version"]
    if not isinstance(version, str) or not _VERSION.match(version):
        raise RulebookError(f"version must be a short name like v0 (got {version!r})")

    eff = raw["effective_from"]
    if isinstance(eff, date):
        effective_from = eff
    else:
        try:
            effective_from = date.fromisoformat(str(eff))
        except ValueError:
            raise RulebookError(
                f"effective_from must be a date like 2026-10-03 (got {eff!r})"
            ) from None

    placeholder = raw.get("placeholder", False)
    if not isinstance(placeholder, bool):
        raise RulebookError("placeholder must be true or false")

    windows = raw["session_windows_sast"]
    if not isinstance(windows, list):
        raise RulebookError('session_windows_sast must be a list like ["14:00-18:00"]')
    for w in windows:
        try:
            start, end = _window_times(w if isinstance(w, str) else "")
        except ValueError:
            raise RulebookError(
                f'session_windows_sast: {w!r} isn\'t a window like "14:00-18:00"'
            ) from None
        if start == end:
            raise RulebookError(
                f"session_windows_sast: {w!r} starts and ends at the same time"
            )

    killers = _words(raw, "killer_items", allow_empty=False)
    strange = [k for k in killers if k not in KILLER_ITEMS]
    if strange:
        raise RulebookError(
            f"killer_items: unknown item(s) {', '.join(strange)} (known: {', '.join(KILLER_ITEMS)})"
        )

    reading = raw.get("checkpoint_reading", "point")
    if reading not in READINGS:
        raise RulebookError(
            f"checkpoint_reading must be point or interval (got {reading!r})"
        )

    return Rulebook(
        version=version,
        effective_from=effective_from,
        placeholder=placeholder,
        max_trades_per_day=_int(raw, "max_trades_per_day", 1),
        stop_after_losses=_int(raw, "stop_after_losses", 1),
        risk_pct=_num(raw, "risk_pct", 0, 10, low_open=True),
        min_planned_rr=_num(raw, "min_planned_rr", 0, 100),
        risk_tolerance_pct=_num(raw, "risk_tolerance_pct", 0, 100),
        session_windows=tuple(windows),
        rule_following_threshold_pct=_num(
            raw, "rule_following_threshold_pct", 0, 100, low_open=True
        ),
        checkpoint_trades=_int(raw, "checkpoint_trades", 1),
        setups=_words(raw, "setups", allow_empty=False),
        emotion_words=_words(raw, "emotion_words", allow_empty=True),
        killer_items=killers,
        checkpoint_reading=reading,
        grading_starts=_grading_start(raw.get("grading_starts")),
    )


def parse(text: str) -> Rulebook:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise RulebookError(f"rulebook.toml isn't valid TOML: {e}") from None
    return from_dict(raw)


def load(path: Path | str) -> Rulebook:
    return parse(Path(path).read_text(encoding="utf-8"))


# --- versions in the journal ------------------------------------------------


def record(conn: sqlite3.Connection, rb: Rulebook) -> str:
    """Remember this version. Returns "new", "same" or "updated"."""
    numbers = json.dumps(rb.numbers(), sort_keys=True)
    row = conn.execute(
        "SELECT * FROM rulebook_versions WHERE version = ?", (rb.version,)
    ).fetchone()
    others = conn.execute(
        "SELECT version, effective_from FROM rulebook_versions WHERE version != ?",
        (rb.version,),
    ).fetchall()

    if row is None:
        latest = max(others, key=lambda r: r["effective_from"], default=None)
        if latest and rb.effective_from.isoformat() <= latest["effective_from"]:
            raise RulebookError(
                f"effective_from {rb.effective_from} must come after "
                f"{latest['version']}'s {latest['effective_from']}"
            )
        conn.execute(
            "INSERT INTO rulebook_versions (version, effective_from, placeholder, numbers_json, recorded_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                rb.version,
                rb.effective_from.isoformat(),
                int(rb.placeholder),
                numbers,
                db.now_iso(),
            ),
        )
        conn.commit()
        return "new"

    if row["numbers_json"] == numbers:
        return "same"

    if not row["placeholder"]:
        graded = conn.execute(
            "SELECT count(*) FROM trades WHERE rulebook_version = ? AND grade IS NOT NULL",
            (rb.version,),
        ).fetchone()[0]
        if graded:
            raise RulebookError(
                f"{rb.version} has already graded {graded} trade(s) with its numbers. Give the new numbers"
                f" a new version (for example v1) with effective_from set to the day they start."
            )

    clash = [o for o in others if o["effective_from"] == rb.effective_from.isoformat()]
    if clash:
        raise RulebookError(
            f"effective_from {rb.effective_from} is already {clash[0]['version']}'s start date"
        )
    conn.execute(
        "UPDATE rulebook_versions SET effective_from = ?, placeholder = ?, numbers_json = ?, recorded_at = ?"
        " WHERE version = ?",
        (
            rb.effective_from.isoformat(),
            int(rb.placeholder),
            numbers,
            db.now_iso(),
            rb.version,
        ),
    )
    conn.commit()
    return "updated"


def version_for(conn: sqlite3.Connection, day: date) -> Rulebook | None:
    """The version in force on this (SAST) day, or None before the first one."""
    row = conn.execute(
        "SELECT numbers_json FROM rulebook_versions WHERE effective_from <= ?"
        " ORDER BY effective_from DESC LIMIT 1",
        (day.isoformat(),),
    ).fetchone()
    return from_dict(json.loads(row[0])) if row else None


def get(conn: sqlite3.Connection, version: str) -> Rulebook | None:
    """A recorded version by name."""
    row = conn.execute(
        "SELECT numbers_json FROM rulebook_versions WHERE version = ?", (version,)
    ).fetchone()
    return from_dict(json.loads(row[0])) if row else None
