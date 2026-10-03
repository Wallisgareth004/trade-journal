"""Uploads over HTTP: a TradingView export on the import page, a screenshot on
a trade, or a screenshot attached from ~/Downloads."""

from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime

import pytest

from test_screenshots import png
from tradejournal import db, web
from tradejournal.synth import HISTORY_HEADER, Spec, history_rows, to_csv
from webtools import Client, NOW, start, stop


def multipart(
    fields: dict, files: list[tuple[str, str, bytes, str]]
) -> tuple[str, bytes]:
    boundary = uuid.uuid4().hex
    out = b""
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for name, filename, data, ctype in files:
        out += (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
    out += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", out


def upload(client: Client, path: str, files, token=True, fields=None):
    f = dict(fields or {})
    if token:
        f["csrf"] = client.token()
    ctype, body = multipart(f, files)
    return client.request(
        "POST",
        path,
        body=body,
        headers={"Content-Type": ctype, "Origin": f"http://127.0.0.1:{client.port}"},
    )


def history_csv(placed=datetime(2026, 10, 5, 14, 5)) -> bytes:
    spec = Spec(
        symbol="TEST:UPUSD",
        direction="long",
        qty=1,
        entry=100,
        stop=99,
        target=102,
        placed=placed,
        exit_kind="target",
    )
    return to_csv(HISTORY_HEADER, history_rows([spec]))


@pytest.fixture
def client():
    server, c = start()
    yield c
    stop(server)


def one(sql, *args):
    conn = db.connect()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


# --- the import page --------------------------------------------------------------


def test_the_import_page_takes_a_tradingview_export(client):
    status, _, page = client.get("/import")
    assert status == 200 and 'enctype="multipart/form-data"' in page
    status, _, page = upload(
        client, "/import", [("files", "history.csv", history_csv(), "text/csv")]
    )
    assert status == 200
    assert "1 new trade" in page
    assert one("SELECT count(*) FROM trades")[0] == 1


def test_the_same_export_twice_changes_nothing(client):
    upload(client, "/import", [("files", "history.csv", history_csv(), "text/csv")])
    _, _, page = upload(
        client, "/import", [("files", "history.csv", history_csv(), "text/csv")]
    )
    assert "already imported" in page
    assert one("SELECT count(*) FROM trades")[0] == 1


def test_a_file_that_is_not_an_export_is_refused_without_echoing_it(client):
    status, _, page = upload(
        client,
        "/import",
        [("files", "bank.csv", b"Date,Amount\n2026-10-01,SECRET123\n", "text/csv")],
    )
    assert status == 400
    assert "not a TradingView" in page and "SECRET123" not in page
    assert one("SELECT count(*) FROM trades")[0] == 0


def test_an_upload_without_the_page_token_is_refused(client):
    status, _, _ = upload(
        client,
        "/import",
        [("files", "history.csv", history_csv(), "text/csv")],
        token=False,
    )
    assert status == 403
    assert one("SELECT count(*) FROM imports")[0] == 0


# --- screenshots on a trade ---------------------------------------------------------


@pytest.fixture
def trade_id(client):
    upload(client, "/import", [("files", "history.csv", history_csv(), "text/csv")])
    return one("SELECT id FROM trades")[0]


def test_a_screenshot_dropped_on_a_trade_is_kept_and_shown(client, trade_id):
    status, headers, _ = upload(
        client,
        f"/trade/{trade_id}/shot",
        [("shot", "chart.png", png(), "image/png")],
        fields={"phase": "entry"},
    )
    assert status == 303 and headers["Location"] == f"/trade/{trade_id}"
    sid = one("SELECT id FROM screenshots")[0]
    status, headers, _ = client.request("GET", f"/shot/{sid}")
    assert status == 200 and headers["Content-Type"] == "image/png"
    _, _, page = client.get(f"/trade/{trade_id}")
    assert f'src="/shot/{sid}"' in page


def test_a_screenshot_brings_the_grade_and_export_up_to_date(client, trade_id):
    upload(
        client,
        f"/trade/{trade_id}/shot",
        [("shot", "chart.png", png(), "image/png")],
        fields={"phase": "entry"},
    )
    from tradejournal import paths

    export = (paths.exports_dir() / "trades.csv").read_text()
    assert f"screenshots/{trade_id}/entry.png" in export


def test_a_file_that_is_not_an_image_is_refused(client, trade_id):
    status, _, page = upload(
        client,
        f"/trade/{trade_id}/shot",
        [("shot", "x.svg", b"<svg onload=alert(1)>", "image/svg+xml")],
        fields={"phase": "entry"},
    )
    assert status == 400 and "PNG or JPEG" in page
    assert one("SELECT count(*) FROM screenshots")[0] == 0


def test_an_upload_over_the_limit_is_refused():
    server, c = start()
    server.app.upload_limit = 4096
    try:
        status, _, _ = upload(
            c, "/import", [("files", "big.csv", b"x" * 20_000, "text/csv")]
        )
        assert status == 413
    finally:
        stop(server)


# --- attaching from ~/Downloads --------------------------------------------------------


def test_a_screenshot_in_downloads_near_the_trade_can_be_attached(
    client, trade_id, tmp_path
):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    shot = downloads / "UPUSD_2026-10-05_14-40.png"
    shot.write_bytes(png(seed=4))
    at = datetime.fromisoformat("2026-10-05T14:40:00+02:00").timestamp()
    os.utime(shot, (at, at))
    client_app = client  # the server reads ~/Downloads unless told otherwise
    from webtools import SERVERS

    SERVERS[-1].app.downloads = downloads
    _, _, page = client_app.get(f"/trade/{trade_id}")
    assert "UPUSD_2026-10-05_14-40.png" in page
    status, _, _ = client_app.post(
        f"/trade/{trade_id}/attach", {"name": shot.name, "phase": "entry"}
    )
    assert status == 303
    assert one("SELECT count(*) FROM screenshots")[0] == 1


@pytest.mark.parametrize("name", ["../../etc/passwd", "not-listed.png", ""])
def test_only_a_listed_download_can_be_attached(client, trade_id, tmp_path, name):
    from webtools import SERVERS

    SERVERS[-1].app.downloads = tmp_path
    status, _, _ = client.post(
        f"/trade/{trade_id}/attach", {"name": name, "phase": "entry"}
    )
    assert status == 400
    assert one("SELECT count(*) FROM screenshots")[0] == 0


def test_a_web_import_leaves_a_backup_first(client, private_dirs):
    _, backups = private_dirs
    upload(client, "/import", [("files", "history.csv", history_csv(), "text/csv")])
    assert len(list(backups.glob("journal-*-before-import.sqlite"))) == 1
