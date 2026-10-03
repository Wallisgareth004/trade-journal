"""Screenshots: dropped on a trade (from the Mac or iPhone Photos), or found in
~/Downloads by time. Kept privately beside the journal, never in iCloud."""

from __future__ import annotations

import os
import struct
import time
import zlib
from datetime import datetime

import pytest

from conftest import rows
from tradejournal import db, screenshots


def png(width=2, height=2, seed=0) -> bytes:
    raw = b"".join(b"\x00" + bytes([seed % 256, 0, 0]) * width for _ in range(height))

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


@pytest.fixture
def trade(conn):
    return db.insert_trade(
        conn,
        {
            "trade_ref": "T1",
            "asset": "TEST",
            "direction": "long",
            "source": "tradingview-csv",
            "entry_time": "2026-10-05T14:05:00+02:00",
            "exit_time": "2026-10-05T15:00:00+02:00",
        },
    )


def test_a_png_is_kept_privately_under_the_trade(conn, trade, private_dirs):
    data, _ = private_dirs
    sid = screenshots.add(conn, trade, png(), phase="entry")
    (path,) = rows(conn, "select path from screenshots where id = ?", sid)[0]
    assert path == f"screenshots/{trade}/entry.png"
    stored = data / path
    assert stored.read_bytes() == png()
    assert stored.stat().st_mode & 0o777 == 0o600
    assert stored.parent.stat().st_mode & 0o777 == 0o700


def test_more_shots_get_numbered_and_the_same_image_twice_is_kept_once(conn, trade):
    screenshots.add(conn, trade, png(seed=1), phase="entry")
    screenshots.add(conn, trade, png(seed=2), phase="other")
    screenshots.add(conn, trade, png(seed=3), phase="other")
    again = screenshots.add(conn, trade, png(seed=2), phase="other")
    got = [r[0] for r in rows(conn, "select path from screenshots order by id")]
    assert got == [
        f"screenshots/{trade}/entry.png",
        f"screenshots/{trade}/other-1.png",
        f"screenshots/{trade}/other-2.png",
    ]
    assert again is None


def test_a_jpeg_keeps_its_kind(conn, trade):
    sid = screenshots.add(conn, trade, JPEG, phase="exit")
    assert rows(conn, "select path from screenshots where id = ?", sid)[0][0].endswith(
        "exit.jpg"
    )


@pytest.mark.parametrize(
    "blob", [b"GIF89a....", b"<svg onload=alert(1)>", b"%PDF-1.7", b""]
)
def test_anything_but_png_or_jpeg_is_refused(conn, trade, blob):
    with pytest.raises(screenshots.NotAnImage):
        screenshots.add(conn, trade, blob, phase="entry")
    assert rows(conn, "select count(*) from screenshots") == [(0,)]


def test_an_old_tracker_trade_takes_no_screenshot(conn):
    tid = db.insert_trade(
        conn,
        {
            "trade_ref": "OLD",
            "asset": "X",
            "direction": "long",
            "source": "legacy-csv",
            "legacy_json": "{}",
        },
    )
    with pytest.raises(ValueError):
        screenshots.add(conn, tid, png(), phase="entry")


def test_a_screenshot_moves_a_b_to_an_a(conn, tmp_path):
    from test_grade import a_trade, setup_book
    from tradejournal import grade

    setup_book(conn)
    tid = a_trade(conn, shot=False)
    grade.grade_all(conn)
    assert rows(conn, "select grade from trades where id = ?", tid) == [("B",)]
    screenshots.add(conn, tid, png(), phase="entry")
    grade.grade_all(conn)
    assert rows(conn, "select grade from trades where id = ?", tid) == [("A",)]


def test_downloads_are_matched_by_time_and_kind(tmp_path, conn, trade):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    near = downloads / "XYZUSD_2026-10-05_15-01-12.png"
    near.write_bytes(png())
    far = downloads / "holiday.png"
    far.write_bytes(png(seed=9))
    doc = downloads / "notes.txt"
    doc.write_text("x")
    at = datetime.fromisoformat("2026-10-05T15:05:00+02:00").timestamp()
    os.utime(near, (at, at))
    old = datetime.fromisoformat("2026-09-01T10:00:00+02:00").timestamp()
    os.utime(far, (old, old))
    os.utime(doc, (at, at))
    t = conn.execute("SELECT * FROM trades WHERE id = ?", (trade,)).fetchone()
    assert screenshots.candidates(t, downloads) == [near]
