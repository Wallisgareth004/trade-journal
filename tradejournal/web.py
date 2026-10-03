"""The journal's pages, served from this Mac only.

* Bound to localhost and the Mac's Tailscale address, never to every address,
  so nothing on the home Wi-Fi or the internet can reach it.
* Each request must come from this Mac or a tailnet device, and name one of
  the journal's own host names (so a web page can't reach it by DNS tricks).
* Every change is a POST carrying the page's token from a same-origin page,
  so another website can't post to it.
* No accounts, keys or cookies. Pages are never cached.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import secrets
import socketserver
import sys
import threading
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import (
    backup,
    checklist,
    dashboard,
    db,
    derive,
    export,
    multipart,
    pages,
    paths,
    rulebook,
    screenshots,
    settings,
    tvimport,
)
from .legacy import LayoutError
from .legacy import SAST
from .rulebook import TAPPED_ITEMS

STATIC = Path(__file__).resolve().parent / "static"
STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
FORM_LIMIT = 64 * 1024
UPLOAD_LIMIT = 30 * 1024 * 1024  # a few exports or one big screenshot
DRAIN_LIMIT = 2 * 1024 * 1024
TAILNET = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:;"
        " form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",  # no-referrer makes browsers post Origin: null
    "Cache-Control": "no-store",
}

TEXT_LIMIT = 4000
NOTE_FIELDS = ("lesson", "mistake", "note")
IMAGE_TYPES = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg"}


class UnsafeBind(Exception):
    """An address that would expose the journal beyond this Mac and the tailnet."""


def _ip(addr: str):
    return ipaddress.ip_address(addr.strip("[]").split("%")[0])


def bind_allowed(addr: str) -> bool:
    ip = _ip(addr)
    return ip.is_loopback or any(ip in net for net in TAILNET)


def peer_allowed(addr: str, allowed: set[str]) -> bool:
    ip = _ip(addr)
    if ip.is_loopback:
        return True
    if not any(ip in net for net in TAILNET):
        return False
    return not allowed or str(ip) in allowed


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    headers: dict = field(default_factory=dict)


def redirect(location: str) -> Response:
    return Response(303, headers={"Location": location})


def text(status: int, message: str) -> Response:
    return Response(status, message.encode(), "text/plain; charset=utf-8")


@dataclass
class Request:
    method: str
    path: str
    query: dict
    form: dict
    groups: tuple = ()
    files: list = field(default_factory=list)


def _log_edit(conn, tid: int, field_name: str, old, new) -> None:
    conn.execute(
        "INSERT INTO edits (trade_id, field, old, new, edited_at) VALUES (?, ?, ?, ?, ?)",
        (
            tid,
            field_name,
            None if old is None else str(old),
            None if new is None else str(new),
            db.now_iso(),
        ),
    )


class App:
    def __init__(self, clock=None, hostnames=(), allowed_peers=(), downloads=None):
        self.clock = clock or (lambda: datetime.now(SAST))
        self.upload_limit = UPLOAD_LIMIT
        self.downloads = downloads or Path.home() / "Downloads"
        self.token = secrets.token_urlsafe(32)
        self.hostnames = {h.lower() for h in hostnames}
        self.allowed_peers = set(allowed_peers)
        self.write_lock = threading.Lock()
        self.routes = [
            ("GET", r"/", self.home),
            ("GET", r"/healthz", self.healthz),
            ("GET", r"/checklist", self.checklist_form),
            ("POST", r"/checklist", self.checklist_save),
            ("GET", r"/checklist/(\d+)", self.checklist_view),
            ("POST", r"/checklist/(\d+)/anyway", self.checklist_anyway),
            ("POST", r"/checklist/(\d+)/skip", self.checklist_skip),
            ("GET", r"/dashboard", self.dashboard),
            ("GET", r"/trades", self.trades),
            ("GET", r"/trade/(\d+)", self.trade),
            ("POST", r"/trade/(\d+)/notes", self.trade_notes),
            ("POST", r"/trade/(\d+)/fix", self.trade_fix),
            ("GET", r"/shot/(\d+)", self.shot),
            ("GET", r"/review", self.review),
            ("POST", r"/review", self.review_save),
            ("GET", r"/import", self.import_page),
            ("POST", r"/import", self.import_upload),
            ("POST", r"/trade/(\d+)/shot", self.trade_shot),
            ("POST", r"/trade/(\d+)/attach", self.trade_attach),
        ]

    # --- plumbing ------------------------------------------------------------

    def host_ok(self, host: str, port: int) -> bool:
        m = re.fullmatch(r"\[?([^\]]+?)\]?:(\d+)", host.strip())
        return (
            bool(m) and m.group(1).lower() in self.hostnames and int(m.group(2)) == port
        )

    def origin_ok(self, origin: str, port: int) -> bool:
        """A post's Origin must be one of the journal's own names on its own
        port. (Behind Tailscale Serve the Host can read 127.0.0.1 while the
        phone's browser names the Mac; both are the journal.)"""
        url = urlsplit(origin)
        try:
            origin_port = url.port
        except ValueError:
            return False
        return (
            url.scheme == "http"
            and (url.hostname or "") in self.hostnames
            and origin_port == port
        )

    def handle(self, req: Request) -> Response:
        if req.method == "GET" and req.path.startswith("/static/"):
            return self.static(req.path[len("/static/") :])
        for method, pattern, fn in self.routes:
            m = re.fullmatch(pattern, req.path)
            if m and method == req.method:
                req.groups = m.groups()
                return fn(req)
        return text(404, "not found")

    def static(self, name: str) -> Response:
        allowed = {p.name: p for p in STATIC.iterdir() if p.suffix in STATIC_TYPES}
        if name not in allowed:
            return text(404, "not found")
        p = allowed[name]
        return Response(200, p.read_bytes(), STATIC_TYPES[p.suffix])

    def html(self, status: int, page: str) -> Response:
        return Response(status, page.encode("utf-8"))

    def rulebook_now(self, conn, now: datetime) -> rulebook.Rulebook:
        """The version in force today; before the first one starts, the current file (practice)."""
        return rulebook.version_for(conn, now.astimezone(SAST).date()) or rulebook.load(
            paths.rulebook_path()
        )

    # --- pages ---------------------------------------------------------------

    def home(self, req: Request) -> Response:
        return redirect("/dashboard")

    def healthz(self, req: Request) -> Response:
        return text(200, "ok")

    def _form_page(self, conn, now, status=200, error=None, values=None) -> Response:
        rb = self.rulebook_now(conn, now)
        auto = checklist.auto_checks(conn, rb, now)
        recent = [
            r[0]
            for r in conn.execute(
                "SELECT asset FROM trades WHERE asset IS NOT NULL GROUP BY asset ORDER BY max(entry_time) DESC LIMIT 8"
            )
        ]
        return self.html(
            status,
            pages.checklist_form(
                rb, auto, self.token, recent, error=error, values=values or {}
            ),
        )

    def checklist_form(self, req: Request) -> Response:
        with closing(db.connect()) as conn:
            return self._form_page(conn, self.clock())

    def checklist_save(self, req: Request) -> Response:
        f = req.form
        now = self.clock()
        with self.write_lock, closing(db.connect()) as conn:
            rb = self.rulebook_now(conn, now)
            answers = {
                k: f.get(k) == "yes" for k in TAPPED_ITEMS if f.get(k) in ("yes", "no")
            }
            complete = (
                len(answers) == len(TAPPED_ITEMS)
                and f.get("direction") in ("long", "short")
                and f.get("setup") in rb.setups
            )
            if not complete:
                return self._form_page(
                    conn,
                    now,
                    400,
                    error="Answer every question: long or short, the setup, and the three checks.",
                    values=f,
                )
            emotion = (
                f.get("emotion_other")
                if f.get("emotion_word") == "other"
                else f.get("emotion_word")
            )
            conf = f.get("confidence")
            cid = checklist.save(
                conn,
                rb,
                now,
                direction=f["direction"],
                setup=f["setup"],
                answers=answers,
                emotion_word=emotion,
                confidence=int(conf) if conf in {"1", "2", "3", "4", "5"} else None,
                asset=f.get("asset"),
            )
        return redirect(f"/checklist/{cid}")

    def checklist_view(self, req: Request) -> Response:
        cid = int(req.groups[0])
        with closing(db.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM checklists WHERE id = ?", (cid,)
            ).fetchone()
            if row is None:
                return text(404, "no such checklist")
            filled = datetime.fromisoformat(row["filled_at"])
            rb = self.rulebook_now(conn, filled)
            auto = checklist.AutoChecks(**json.loads(row["auto_json"]))
            answers = json.loads(row["answers_json"])
            return self.html(
                200, pages.checklist_view(rb, row, auto, answers, self.token)
            )

    def _change(self, req: Request, fn) -> Response:
        cid = int(req.groups[0])
        with self.write_lock, closing(db.connect()) as conn:
            try:
                fn(conn, cid)
            except ValueError as e:
                return text(409, str(e))
        return redirect(f"/checklist/{cid}")

    def checklist_anyway(self, req: Request) -> Response:
        return self._change(req, checklist.take_anyway)

    def checklist_skip(self, req: Request) -> Response:
        return self._change(req, checklist.skip)

    # --- dashboard, trades and the weekly review ------------------------------------

    def dashboard(self, req: Request) -> Response:
        now = self.clock()
        with closing(db.connect()) as conn:
            rb = self.rulebook_now(conn, now)
            return self.html(200, pages.dashboard(dashboard.build(conn, rb, now), rb))

    def trades(self, req: Request) -> Response:
        with closing(db.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY entry_time DESC LIMIT 500"
            ).fetchall()
            return self.html(200, pages.trades_list(rows))

    def _trade_page(
        self, conn, tid: int, status: int = 200, error: str | None = None
    ) -> Response:
        t = conn.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
        if t is None:
            return text(404, "no such trade")
        c = (
            conn.execute(
                "SELECT * FROM checklists WHERE id = ?", (t["checklist_id"],)
            ).fetchone()
            if t["checklist_id"]
            else None
        )
        shots = conn.execute(
            "SELECT * FROM screenshots WHERE trade_id = ? ORDER BY added_at", (tid,)
        ).fetchall()
        edits = conn.execute(
            "SELECT * FROM edits WHERE trade_id = ? ORDER BY id", (tid,)
        ).fetchall()
        when = (
            datetime.fromisoformat(t["entry_time"]) if t["entry_time"] else self.clock()
        )
        rb = (
            rulebook.get(conn, t["rulebook_version"]) if t["rulebook_version"] else None
        ) or self.rulebook_now(conn, when)
        downloads = (
            []
            if t["source"] == "legacy-csv"
            else [p.name for p in screenshots.candidates(t, self.downloads)]
        )
        return self.html(
            status,
            pages.trade_detail(t, c, shots, edits, rb, self.token, error, downloads),
        )

    def trade(self, req: Request) -> Response:
        with closing(db.connect()) as conn:
            return self._trade_page(conn, int(req.groups[0]))

    def trade_notes(self, req: Request) -> Response:
        tid = int(req.groups[0])
        with self.write_lock, closing(db.connect()) as conn:
            t = conn.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
            if t is None:
                return text(404, "no such trade")
            changes = {}
            for k in NOTE_FIELDS:
                new = (req.form.get(k) or "").strip()[:TEXT_LIMIT] or None
                if new != t[k]:
                    changes[k] = new
                    _log_edit(conn, tid, k, t[k], new)
            if changes:
                db.update_trade(conn, tid, changes)
                export.write(conn)
        return redirect(f"/trade/{tid}")

    def trade_fix(self, req: Request) -> Response:
        tid = int(req.groups[0])
        f = req.form
        with self.write_lock, closing(db.connect()) as conn:
            t = conn.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
            if t is None:
                return text(404, "no such trade")
            if t["source"] == "legacy-csv":
                return text(409, "old-tracker trades keep the values as written")
            overrides = json.loads(t["overrides_json"] or "{}")
            raw_stop = (f.get("stop") or "").strip().replace(",", ".")
            stop = None
            if raw_stop:
                try:
                    stop = float(raw_stop)
                except ValueError:
                    stop = -1.0
                if not stop > 0:
                    return self._trade_page(
                        conn, tid, 400, "The initial stop must be a price, like 2412.5."
                    )
            changes = {}
            setup = f.get("setup") or None
            when = (
                datetime.fromisoformat(t["entry_time"])
                if t["entry_time"]
                else self.clock()
            )
            rb = self.rulebook_now(conn, when)
            if setup is not None and setup not in rb.setups:
                return self._trade_page(
                    conn, tid, 400, "Pick a setup from the rulebook."
                )
            if setup != t["setup"]:
                changes["setup"] = setup
                _log_edit(conn, tid, "setup", t["setup"], setup)
            emotion = (f.get("emotion_word") or "").strip()[:30] or None
            if emotion != t["emotion_word"]:
                changes["emotion_word"] = emotion
                _log_edit(conn, tid, "emotion_word", t["emotion_word"], emotion)
            current = overrides.get("stop", t["stop"])
            if stop is not None and stop != current:
                _log_edit(
                    conn, tid, "stop", None if current is None else float(current), stop
                )
                overrides["stop"] = stop
            elif stop is None and "stop" in overrides:
                _log_edit(conn, tid, "stop", overrides["stop"], t["stop"])
                del overrides["stop"]
            changes["overrides_json"] = json.dumps(overrides) if overrides else None
            db.update_trade(conn, tid, changes)
            derive.refresh(conn, settings.load())
        return redirect(f"/trade/{tid}")

    def shot(self, req: Request) -> Response:
        with closing(db.connect()) as conn:
            row = conn.execute(
                "SELECT path FROM screenshots WHERE id = ?", (int(req.groups[0]),)
            ).fetchone()
        if row is None:
            return text(404, "no such screenshot")
        root = paths.screenshots_dir().resolve()
        target = (paths.data_dir() / row["path"]).resolve()
        if root not in target.parents or not target.is_file():
            return text(404, "no such screenshot")
        data = target.read_bytes()
        kind = next((v for k, v in IMAGE_TYPES.items() if data.startswith(k)), None)
        if kind is None:
            return text(415, "not an image the journal shows")
        return Response(200, data, kind)

    def review(self, req: Request) -> Response:
        now = self.clock()
        week_id = req.query.get("week") or dashboard.week_of(
            now.astimezone(SAST).date()
        )
        try:
            dashboard.week_bounds(week_id)
        except ValueError:
            return text(400, "a week looks like 2026-W41")
        with closing(db.connect()) as conn:
            return self.html(
                200, pages.review(dashboard.week(conn, week_id, now), self.token)
            )

    def review_save(self, req: Request) -> Response:
        f = req.form
        week_id = f.get("week") or ""
        try:
            dashboard.week_bounds(week_id)
        except ValueError:
            return text(400, "a week looks like 2026-W41")
        answers = {
            k: (f.get(k) or "").strip()[:TEXT_LIMIT] for k, _, _ in pages.PROMPTS
        }
        with self.write_lock, closing(db.connect()) as conn:
            conn.execute(
                "INSERT INTO reviews (week, answers_json, saved_at) VALUES (?, ?, ?)"
                " ON CONFLICT(week) DO UPDATE SET answers_json = excluded.answers_json, saved_at = excluded.saved_at",
                (week_id, json.dumps(answers), db.now_iso()),
            )
            conn.commit()
        return redirect(f"/review?week={week_id}")

    # --- uploads ---------------------------------------------------------------------

    def import_page(self, req: Request) -> Response:
        return self.html(200, pages.import_page(self.token))

    def import_upload(self, req: Request) -> Response:
        uploads = [u for u in req.files if u.field == "files"]
        if not uploads:
            return self.html(
                400,
                pages.import_page(
                    self.token, error="Pick a TradingView export (a CSV file) first."
                ),
            )
        results, status = [], 200
        with self.write_lock, closing(db.connect()) as conn:
            s = settings.load()
            backup.snapshot(conn, self.clock(), label="before-import")
            for u in uploads:
                try:
                    results.append(
                        (
                            u.filename,
                            tvimport.import_bytes(
                                conn, u.data, u.filename, s, self.clock()
                            ),
                            None,
                        )
                    )
                except LayoutError as err:
                    results.append((u.filename, None, str(err)))
                    status = 400
        return self.html(status, pages.import_page(self.token, results=results))

    def _add_shot(self, conn, tid: int, data: bytes, phase: str) -> Response | None:
        try:
            screenshots.add(conn, tid, data, phase)
        except screenshots.NotAnImage as err:
            return self._trade_page(
                conn, tid, 400, f"Not kept: {err}. Save the screenshot as PNG or JPEG."
            )
        except ValueError as err:
            return self._trade_page(conn, tid, 409, str(err))
        derive.refresh(conn, settings.load())
        return None

    def trade_shot(self, req: Request) -> Response:
        tid = int(req.groups[0])
        phase = req.form.get("phase") or "entry"
        uploads = [u for u in req.files if u.field == "shot"]
        with self.write_lock, closing(db.connect()) as conn:
            if (
                conn.execute("SELECT 1 FROM trades WHERE id = ?", (tid,)).fetchone()
                is None
            ):
                return text(404, "no such trade")
            if not uploads:
                return self._trade_page(conn, tid, 400, "Pick a screenshot first.")
            for u in uploads:
                refused = self._add_shot(conn, tid, u.data, phase)
                if refused is not None:
                    return refused
        return redirect(f"/trade/{tid}")

    def trade_attach(self, req: Request) -> Response:
        tid = int(req.groups[0])
        name = req.form.get("name") or ""
        phase = req.form.get("phase") or "entry"
        with self.write_lock, closing(db.connect()) as conn:
            t = conn.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
            if t is None:
                return text(404, "no such trade")
            listed = {p.name: p for p in screenshots.candidates(t, self.downloads)}
            if name not in listed:
                return self._trade_page(
                    conn,
                    tid,
                    400,
                    "That file isn't one of the screenshots listed for this trade.",
                )
            refused = self._add_shot(conn, tid, listed[name].read_bytes(), phase)
            if refused is not None:
                return refused
        return redirect(f"/trade/{tid}")


class Handler(BaseHTTPRequestHandler):
    server_version = "journal"
    sys_version = ""

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def _serve(self, method: str) -> None:
        app: App = self.server.app
        try:
            if not peer_allowed(self.client_address[0], app.allowed_peers):
                return self._send(
                    text(403, "this journal answers this Mac and its tailnet only")
                )
            host = self.headers.get("Host", "")
            if not app.host_ok(host, self.server.server_address[1]):
                return self._send(text(421, "unknown host name"))
            url = urlsplit(self.path)
            form: dict = {}
            files: list = []
            if method == "POST":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return self._send(text(400, "bad length"))
                ctype = self.headers.get("Content-Type", "")
                is_upload = ctype.lower().startswith("multipart/form-data")
                limit = app.upload_limit if is_upload else FORM_LIMIT
                if length > limit:
                    if length <= DRAIN_LIMIT:
                        self.rfile.read(length)
                    self.close_connection = True
                    return self._send(text(413, "too large"))
                origin = self.headers.get("Origin")
                if origin is not None and not app.origin_ok(
                    origin, self.server.server_address[1]
                ):
                    return self._send(text(403, "cross-site request refused"))
                raw = self.rfile.read(length)
                if is_upload:
                    try:
                        form, files = multipart.parse(ctype, raw)
                    except ValueError:
                        return self._send(text(400, "an upload the journal can't read"))
                else:
                    form = {
                        k: v[-1]
                        for k, v in parse_qs(
                            raw.decode("utf-8", "replace"), keep_blank_values=True
                        ).items()
                    }
                if not secrets.compare_digest(form.get("csrf", ""), app.token):
                    return self._send(
                        text(403, "the page token is missing or old: reload the page")
                    )
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            self._send(app.handle(Request(method, url.path, query, form, files=files)))
        except Exception as e:  # never a traceback to the browser
            print(
                f"journal: error on {method} {urlsplit(self.path).path}: {e!r}",
                file=sys.stderr,
            )
            self._send(
                text(
                    500,
                    "something went wrong; the details are in the journal's terminal",
                )
            )

    def _send(self, r: Response) -> None:
        self.send_response(r.status)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", r.content_type)
        self.send_header("Content-Length", str(len(r.body)))
        for k, v in r.headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(r.body)

    def log_request(self, code="-", size="-") -> None:
        path = urlsplit(self.path).path
        print(
            f"{self.log_date_time_string()} {self.client_address[0]} {self.command} {path} {code}",
            file=sys.stderr,
        )


class Server(ThreadingHTTPServer):
    daemon_threads = True
    app: App

    def server_bind(self) -> None:
        # HTTPServer.server_bind looks the address up in DNS (getfqdn), which
        # can stall for half a minute on macOS. The name is never used here.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class Server6(Server):
    address_family = __import__("socket").AF_INET6


def make_servers(binds: list[str], port: int, app: App) -> list[Server]:
    """One server per address. Refuses any address beyond localhost and the tailnet."""
    for addr in binds:
        if not bind_allowed(addr):
            raise UnsafeBind(
                f"{addr} would expose the journal beyond this Mac and the tailnet"
            )
    servers = []
    for addr in binds:
        cls = Server6 if ":" in addr else Server
        s = cls((addr.strip("[]"), port), Handler)
        s.app = app
        servers.append(s)
        port = port or s.server_address[1]
    return servers


def escape(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)
