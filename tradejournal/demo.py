"""A made-up journal to show every page: for trying things out, and for the
public case study. It lives in a folder of its own (a temp folder by
default) and never touches a real one.

Trades come from the synthetic generator with its deliberate rule breaks,
are imported a day at a time as a trader would, and most get a drawn chart as
their screenshot.
"""

from __future__ import annotations

import math
import os
import random
import struct
import zlib
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import db, derive, paths, rulebook, screenshots, settings, synth, tvimport
from .legacy import SAST

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _png(width: int, height: int, pixels: bytearray) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + bytes(pixels[y * width * 3 : (y + 1) * width * 3])
        for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def chart_png(seed: int, width: int = 320, height: int = 180) -> bytes:
    """A small made-up price chart: a random walk with entry, stop and target lines."""
    rng = random.Random(seed)
    px = bytearray([246, 247, 249] * width * height)

    def dot(x: int, y: int, colour: tuple) -> None:
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 3
            px[i : i + 3] = bytes(colour)

    def line(x0, y0, x1, y1, colour, thick=2):
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for k in range(steps + 1):
            x = round(x0 + (x1 - x0) * k / steps)
            y = round(y0 + (y1 - y0) * k / steps)
            for dx in range(thick):
                for dy in range(thick):
                    dot(x + dx, y + dy, colour)

    walk = [0.0]
    for _ in range(59):
        walk.append(walk[-1] + rng.gauss(0, 1))
    lo, hi = min(walk) - 2, max(walk) + 2
    ys = [round((hi - v) / (hi - lo) * (height - 20)) + 10 for v in walk]
    xs = [round(i * (width - 20) / (len(walk) - 1)) + 10 for i in range(len(walk))]
    entry_i = 35
    entry_y = ys[entry_i]
    stop_y = min(height - 6, entry_y + rng.randint(12, 24))
    target_y = max(5, entry_y - 2 * (stop_y - entry_y))
    for x in range(xs[entry_i], width - 6, 6):
        line(x, entry_y, x + 3, entry_y, (110, 118, 129), 1)
        line(x, stop_y, x + 3, stop_y, (196, 48, 43), 1)
        line(x, target_y, x + 3, target_y, (26, 127, 55), 1)
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        line(x0, y0, x1, y1, (36, 87, 197))
    return _png(width, height, px)


def build(
    folder: Path | str, trades: int = 60, seed: int = 7, today: date | None = None
) -> dict:
    """Fill a made-up journal in `folder`. Points the journal's paths there for
    the rest of this process. Returns the counts it made."""
    folder = Path(folder)
    os.environ["TRADE_JOURNAL_HOME"] = str(folder / "data")
    os.environ["TRADE_JOURNAL_BACKUPS"] = str(folder / "backups")
    paths.ensure_private_dir(paths.data_dir())

    today = today or date.today()
    weeks = math.ceil(trades / 15) + 1
    start = today - timedelta(days=today.weekday() + 7 * weeks)
    text = (
        (EXAMPLES / "rulebook.toml")
        .read_text()
        .replace("placeholder = true", "placeholder = false")
        .replace(
            "session_windows_sast = []",
            'session_windows_sast = ["09:00-13:00", "14:00-18:00"]',
        )
        .replace("min_planned_rr = 2.0", "min_planned_rr = 1.5")
        .replace(
            'effective_from = "2026-10-03"', f'effective_from = "{start.isoformat()}"'
        )
    )
    paths.write_private(paths.rulebook_path(), text.encode())
    paths.write_private(settings.path(), (EXAMPLES / "settings.toml").read_bytes())
    s = settings.load()
    conn = db.connect()
    rulebook.record(conn, rulebook.parse(text))

    journal = synth.journal(
        trades, seed=seed, start=datetime.combine(start, time()), min_rr=1.5
    )
    synth.insert_checklists(conn, journal.checklists)
    by_day: dict[str, list[dict]] = {}
    for row in synth.history_rows(journal.specs):
        by_day.setdefault(row["Placing Time"][:10], []).append(row)
    for day in sorted(by_day):
        when = datetime.combine(date.fromisoformat(day), time(18, 30), SAST)
        raw = synth.to_csv(synth.HISTORY_HEADER, by_day[day])
        tvimport.import_bytes(conn, raw, f"history-{day}.csv", s, when)

    rng = random.Random(seed + 2)
    shots = 0
    for t in conn.execute(
        "SELECT id, exit_time FROM trades ORDER BY entry_time"
    ).fetchall():
        if rng.random() < 0.75 and t["exit_time"]:
            added = (
                datetime.fromisoformat(t["exit_time"]) + timedelta(minutes=10)
            ).isoformat(timespec="seconds")
            if screenshots.add(
                conn, t["id"], chart_png(seed * 1000 + t["id"]), "entry", added_at=added
            ):
                shots += 1
    derive.refresh(conn, s)
    grades = Counter(r[0] for r in conn.execute("SELECT grade FROM trades") if r[0])
    counts = {
        "trades": conn.execute("SELECT count(*) FROM trades").fetchone()[0],
        "grades": {g: grades.get(g, 0) for g in "ABC"},
        "screenshots": shots,
        "checklists": conn.execute("SELECT count(*) FROM checklists").fetchone()[0],
    }
    conn.close()
    return counts
