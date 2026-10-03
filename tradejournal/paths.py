"""Where the journal keeps things.

The code lives in its repo. Your trades, checklists, screenshots and rulebook
live in ~/Library/Application Support/trade-journal (and its backups in
~/Backups/trade-journal): places that never sync to iCloud and are never
inside a git repo. Every path the journal writes passes `check_private`.

Overrides (for tests and the synthetic demo): TRADE_JOURNAL_HOME,
TRADE_JOURNAL_BACKUPS.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class UnsafeLocation(Exception):
    """A place that syncs or gets pushed: the journal's data may not live there."""


def _synced_roots() -> list[Path]:
    home = Path.home()
    return [
        home / "Documents",
        home / "Desktop",
        home / "Library" / "Mobile Documents",
        home / "Library" / "CloudStorage",
    ]


def check_private(path: Path | str) -> Path:
    """The absolute path, or UnsafeLocation if it syncs or sits in a git repo."""
    p = Path(path).expanduser().resolve()
    for root in _synced_roots():
        root = root.resolve()
        if p == root or root in p.parents:
            raise UnsafeLocation(
                f"{p} is under {root}, which syncs to iCloud or the cloud"
            )
    for parent in (p, *p.parents):
        if (parent / ".git").exists():
            raise UnsafeLocation(
                f"{p} is inside a git repo ({parent}); trades never go into git"
            )
    return p


def data_dir() -> Path:
    default = Path.home() / "Library" / "Application Support" / "trade-journal"
    return check_private(os.environ.get("TRADE_JOURNAL_HOME") or default)


def backups_dir() -> Path:
    default = Path.home() / "Backups" / "trade-journal"
    return check_private(os.environ.get("TRADE_JOURNAL_BACKUPS") or default)


def ensure_private_dir(path: Path | str) -> Path:
    """Create the folder (and any missing parents) owner-only: 700."""
    p = check_private(path)
    missing = [d for d in (p, *p.parents) if not d.exists()]
    p.mkdir(parents=True, exist_ok=True)
    for d in missing:
        os.chmod(d, 0o700)
    os.chmod(p, 0o700)
    return p


def db_path() -> Path:
    return data_dir() / "journal.sqlite"


def rulebook_path() -> Path:
    return data_dir() / "rulebook.toml"


def exports_dir() -> Path:
    return data_dir() / "exports"


def screenshots_dir() -> Path:
    return data_dir() / "screenshots"


def imports_dir() -> Path:
    return data_dir() / "imports"


def inbox_dir() -> Path:
    return data_dir() / "inbox"


def write_private(path: Path, data: bytes) -> Path:
    """Write a file owner-only (600) and atomically: a temp file, then rename."""
    path = check_private(path)
    ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if tmp.exists():
            discard(tmp)
        raise
    os.chmod(path, 0o600)
    return path


def discard(path: Path) -> None:
    """Unlink a temp file under $TMPDIR; send anything else to the Trash."""
    path = Path(path)
    tmp_root = Path(tempfile.gettempdir()).resolve()
    if tmp_root in path.resolve().parents:
        path.unlink()
    else:
        subprocess.run(["/usr/bin/trash", str(path)], check=True, capture_output=True)
