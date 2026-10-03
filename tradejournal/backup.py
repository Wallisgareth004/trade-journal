"""Backups in ~/Backups/trade-journal (owner-only; not iCloud, not git).

One copy of the journal a day (SQLite's own online backup, so a copy taken
while the server runs is consistent), the screenshots mirrored by name, and
the rulebook and settings files. Nothing is ever removed from the backups.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import paths
from .legacy import SAST


def _copy_db(conn: sqlite3.Connection, target: Path) -> Path:
    target = paths.check_private(target)
    paths.ensure_private_dir(target.parent)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
    dest = sqlite3.connect(tmp)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    os.replace(tmp, target)
    os.chmod(target, 0o600)
    return target


def _mirror_files() -> None:
    root = paths.backups_dir()
    shots = paths.screenshots_dir()
    if shots.is_dir():
        for src in shots.rglob("*"):
            if not src.is_file():
                continue
            dest = root / "screenshots" / src.relative_to(shots)
            if dest.exists() and dest.stat().st_size == src.stat().st_size:
                continue
            paths.ensure_private_dir(dest.parent)
            shutil.copyfile(src, dest)
            os.chmod(dest, 0o600)
    for name in ("rulebook.toml", "settings.toml"):
        src = paths.data_dir() / name
        if src.is_file():
            paths.write_private(root / name, src.read_bytes())


def daily(conn: sqlite3.Connection, now: datetime | None = None) -> Path | None:
    """Today's copy, if there isn't one yet. Returns its path, or None."""
    now = (now or datetime.now(SAST)).astimezone(SAST)
    root = paths.ensure_private_dir(paths.backups_dir())
    target = root / f"journal-{now.date().isoformat()}.sqlite"
    if target.exists():
        return None
    made = _copy_db(conn, target)
    _mirror_files()
    return made


def snapshot(
    conn: sqlite3.Connection, now: datetime | None = None, label: str = "manual"
) -> Path:
    """A named copy before something big (a migration, a rulebook change)."""
    now = (now or datetime.now(SAST)).astimezone(SAST)
    root = paths.ensure_private_dir(paths.backups_dir())
    safe = "".join(ch for ch in label if ch.isalnum() or ch == "-")[:40] or "manual"
    made = _copy_db(conn, root / f"journal-{now:%Y-%m-%d-%H%M}-{safe}.sqlite")
    _mirror_files()
    return made
