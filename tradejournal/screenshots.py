"""Screenshots, kept beside the journal: never iCloud, never git.

They live in ~/Library/Application Support/trade-journal/screenshots/<trade>/
as entry.png, exit.png and other-N.png (owner-only), arrive by a drop on the
trade's page (or a pick from Photos on the iPhone), or are matched from
~/Downloads by time. Only PNG and JPEG are taken; the same image twice is
kept once. The entry image's path fills the export's `screenshot` column.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import db, paths

MAX_BYTES = 25 * 1024 * 1024
KINDS = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"))
BEFORE = timedelta(hours=1)
AFTER = timedelta(hours=24)
PHASES = ("entry", "exit", "other")


class NotAnImage(ValueError):
    """Not a PNG or JPEG the journal keeps."""


def kind(data: bytes) -> str | None:
    return next((ext for magic, ext in KINDS if data.startswith(magic)), None)


def _name(conn: sqlite3.Connection, trade_id: int, phase: str, ext: str) -> str:
    taken = {
        Path(r[0]).stem
        for r in conn.execute(
            "SELECT path FROM screenshots WHERE trade_id = ?", (trade_id,)
        )
    }
    if phase != "other" and phase not in taken:
        return f"{phase}.{ext}"
    n = 1
    while f"{phase}-{n}" in taken:
        n += 1
    return f"{phase}-{n}.{ext}"


def add(
    conn: sqlite3.Connection,
    trade_id: int,
    data: bytes,
    phase: str = "entry",
    added_at: str | None = None,
) -> int | None:
    """Keep one screenshot for a trade. None if this exact image is already there."""
    trade = conn.execute(
        "SELECT source FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    if trade is None:
        raise ValueError("no such trade")
    if trade["source"] == "legacy-csv":
        raise ValueError("old-tracker trades keep the screenshot column as written")
    if phase not in PHASES:
        raise ValueError("phase is entry, exit or other")
    ext = kind(data)
    if ext is None:
        raise NotAnImage("only PNG or JPEG screenshots")
    if len(data) > MAX_BYTES:
        raise NotAnImage("that image is over 25 MB")
    sha = hashlib.sha256(data).hexdigest()
    if conn.execute(
        "SELECT 1 FROM screenshots WHERE trade_id = ? AND sha256 = ?", (trade_id, sha)
    ).fetchone():
        return None
    rel = f"screenshots/{trade_id}/{_name(conn, trade_id, phase, ext)}"
    paths.ensure_private_dir(paths.screenshots_dir())
    paths.write_private(paths.data_dir() / rel, data)
    cur = conn.execute(
        "INSERT INTO screenshots (trade_id, phase, path, sha256, added_at) VALUES (?, ?, ?, ?, ?)",
        (trade_id, phase, rel, sha, added_at or db.now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def candidates(trade: sqlite3.Row, downloads: Path | None = None) -> list[Path]:
    """Images in ~/Downloads saved between an hour before the entry and a day
    after the exit, nearest the exit first. Only names and times are read."""
    downloads = downloads or Path.home() / "Downloads"
    if not trade["entry_time"] or not downloads.is_dir():
        return []
    start = datetime.fromisoformat(trade["entry_time"]) - BEFORE
    anchor = datetime.fromisoformat(trade["exit_time"] or trade["entry_time"])
    end = anchor + AFTER
    found = []
    for p in downloads.iterdir():
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg") or not p.is_file():
            continue
        saved = datetime.fromtimestamp(p.stat().st_mtime, tz=anchor.tzinfo)
        if start <= saved <= end:
            found.append((abs((saved - anchor).total_seconds()), p))
    return [p for _, p in sorted(found)]
