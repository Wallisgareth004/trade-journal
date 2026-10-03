"""Test utilities: a journal server on 127.0.0.1 with a fixed clock, and a raw
HTTP client that can set any Host or Origin header."""

from __future__ import annotations

import http.client
import re
import threading
from datetime import datetime
from urllib.parse import urlencode

from conftest import ROOT
from tradejournal import db, paths, rulebook, web
from tradejournal.legacy import SAST

NOW = datetime(2026, 10, 5, 14, 30, tzinfo=SAST)
SERVERS: list = []  # the servers started by tests, newest last


class Client:
    def __init__(self, port: int):
        self.port = port

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), data.decode("utf-8", "replace")

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def token(self):
        _, _, page = self.get("/checklist")
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def post(self, path, fields: dict, token=True, origin=True, headers=None):
        fields = dict(fields)
        if token is True:
            fields["csrf"] = self.token()
        elif token:
            fields["csrf"] = token
        h = {"Content-Type": "application/x-www-form-urlencoded"}
        if origin is True:
            h["Origin"] = f"http://127.0.0.1:{self.port}"
        elif origin:
            h["Origin"] = origin
        h.update(headers or {})
        return self.request("POST", path, body=urlencode(fields), headers=h)


def start(clock=lambda: NOW, rulebook_text: str | None = None):
    paths.ensure_private_dir(paths.data_dir())
    text = rulebook_text or (ROOT / "examples" / "rulebook.toml").read_text()
    paths.write_private(paths.rulebook_path(), text.encode())
    conn = db.connect()
    rulebook.record(conn, rulebook.parse(text))
    conn.close()
    app = web.App(clock=clock, hostnames={"127.0.0.1", "localhost"})
    (server,) = web.make_servers(["127.0.0.1"], 0, app)
    SERVERS.append(server)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    return server, Client(server.server_address[1])


def stop(server):
    server.shutdown()
    server.server_close()
