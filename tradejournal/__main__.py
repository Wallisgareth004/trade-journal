"""journal: the trade journal's command line.

It prints counts, never trades: an AI helper that runs it sees no prices,
symbols or notes. The trades themselves are shown only in the browser.

    journal init            private folder, journal file, rulebook (placeholders), settings
    journal migrate [CSV]   the old tracker's rows, once (re-running adds nothing)
    journal inspect FILE    a TradingView export's layout, in names and counts; writes nothing
    journal import FILE...  TradingView exports in (the same file twice changes nothing)
    journal export          rewrite exports/trades.csv
    journal rulebook        check rulebook.toml and record its version
    journal status          counts only
    journal serve           the pages (this Mac; the tailnet only as settings.toml says)
    journal start | stop    the same, as a service in the tmux session 'journal'
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import (
    backup,
    db,
    demo,
    derive,
    export,
    legacy,
    paths,
    rulebook,
    service,
    settings,
    tailnet,
    tvimport,
    web,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _open():
    """The journal, with the current rulebook version recorded."""
    conn = db.connect()
    if paths.rulebook_path().exists():
        rulebook.record(conn, rulebook.load(paths.rulebook_path()))
    return conn


def _describe(rb: rulebook.Rulebook) -> str:
    windows = (
        ", ".join(rb.session_windows) if rb.windows_set else "unset (session check off)"
    )
    tag = " (placeholder numbers)" if rb.placeholder else ""
    return (
        f"rulebook {rb.version}{tag} from {rb.effective_from}: risk {rb.risk_pct}% per trade,"
        f" planned R:R at least {rb.min_planned_rr}, max {rb.max_trades_per_day} trades a day,"
        f" stop after {rb.stop_after_losses} losses, windows {windows}"
    )


def cmd_init(args) -> int:
    data = paths.ensure_private_dir(paths.data_dir())
    for name, target in (
        ("rulebook.toml", paths.rulebook_path()),
        ("settings.toml", settings.path()),
    ):
        if not target.exists():
            paths.write_private(target, (EXAMPLES / name).read_bytes())
    settings.load()
    conn = _open()
    derive.refresh(conn, settings.load())
    conn.close()
    print(f"journal ready in {data}")
    print(_describe(rulebook.load(paths.rulebook_path())))
    return 0


def cmd_migrate(args) -> int:
    conn = _open()
    csv_path = args.csv or settings.load().old_tracker
    if not csv_path:
        raise legacy.LayoutError(
            'which file? Give the old tracker\'s CSV, or set old_tracker = "/path/to/it.csv" in settings.toml'
        )
    backup.snapshot(conn, label="before-migration")
    result = legacy.migrate(conn, Path(csv_path).expanduser())
    derive.refresh(conn, settings.load())
    conn.close()
    plural = "row" if result.examples == 1 else "rows"
    print(
        f"{result.new} trades in, {result.already} already in, {result.matched} matched a TradingView trade, "
        f"{result.examples} example {plural} skipped ({result.read} rows read)"
    )
    return 0


def cmd_inspect(args) -> int:
    f = tvimport.inspect(args.file, settings.load())
    if f["kind"] == "account-history":
        print("File: the Account history tab (TradingView paper trading)")
        print(f"columns ({len(f['columns'])}): {', '.join(f['columns'])}")
        print(
            f"rows: {f['rows']}; closing actions understood: {f['actions_understood']} of {f['rows']}"
        )
        return 0
    sp = f["stop_placed"]
    print("File: the History tab (TradingView paper trading)")
    print(f"columns ({len(f['columns'])}): {', '.join(f['columns'])}")
    print(f"optional columns missing: {', '.join(f['missing_optional']) or 'none'}")
    print(f"orders: {f['orders']}")
    print(f"statuses: {', '.join(f['statuses'])}")
    print(f"sides: {', '.join(f['sides'])}")
    print(f"types: {', '.join(f['types'])}")
    print(f"orders with commission: {f['with_commission']}")
    print(f"round trips: {f['closed_trips']} closed, {f['open_trips']} open")
    print(
        f"stops: {sp['with-order']} placed with the order, {sp['after-entry']} after the entry,"
        f" {sp['none']} with none; {f['trips_with_several_stops']} trips with more than one stop order;"
        f" {f['trips_stop_at_or_past_entry']} with the stop at or past the entry"
    )
    print(f"trips without a target order: {f['trips_without_target']}")
    return 0


def cmd_import(args) -> int:
    conn = _open()
    s = settings.load()
    backup.snapshot(conn, label="before-import")
    for name in args.files:
        r = tvimport.import_file(conn, name, s)
        label = Path(name).name
        if r.already:
            print(f"{label}: already imported (nothing changed)")
        elif r.kind == "account-history":
            print(
                f"{label}: Account history, {r.new_balances} new balance rows of {r.rows}"
            )
        else:
            still = (
                "position still open"
                if r.open_positions == 1
                else "positions still open"
            )
            print(
                f"{label}: {r.new_trades} new trades, {r.updated_trades} updated,"
                f" {r.matched_old} matched to the old tracker, {r.open_positions} {still}"
                f" ({r.rows} orders: {r.new_orders} new, {r.updated_orders} changed)"
            )
    conn.close()
    return 0


def cmd_export(args) -> int:
    conn = _open()
    out = export.write(conn)
    n = conn.execute("SELECT count(*) FROM trades").fetchone()[0]
    conn.close()
    print(f"{n} trades written to {out}")
    return 0


def cmd_rulebook(args) -> int:
    conn = _open()
    derive.refresh(conn, settings.load())
    conn.close()
    print(_describe(rulebook.load(paths.rulebook_path())))
    return 0


def _answering(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
            return r.read() == b"ok"
    except OSError:
        return False


def cmd_status(args) -> int:
    conn = _open()

    def one(sql: str, *a) -> int:
        return conn.execute(sql, a).fetchone()[0]

    s = settings.load()
    up = service.running(service.session_name())
    answering = _answering(s.port)
    state = "running" if up else "stopped"
    print(
        f"service: {state} (tmux '{service.session_name()}'), {'answering' if answering else 'not answering'} on 127.0.0.1:{s.port}"
    )
    print(f"tailnet: {s.tailnet}")
    by_source = "SELECT count(*) FROM trades WHERE source = ?"
    print(f"data folder: {paths.data_dir()}")
    print(f"trades: {one('SELECT count(*) FROM trades')}")
    print(f"  old tracker: {one(by_source, 'legacy-csv')}")
    print(f"  from TradingView: {one(by_source, 'tradingview-csv')}")
    print(f"  graded: {one('SELECT count(*) FROM trades WHERE grade IS NOT NULL')}")
    print(
        f"  with something to look at: {one('SELECT count(*) FROM trades WHERE flags_json IS NOT NULL')}"
    )
    print(f"checklists: {one('SELECT count(*) FROM checklists')}")
    print(f"screenshots: {one('SELECT count(*) FROM screenshots')}")
    last = conn.execute("SELECT max(imported_at) FROM imports").fetchone()[0]
    print(f"imports: {one('SELECT count(*) FROM imports')} (last {last or 'never'})")
    versions = [
        r[0]
        for r in conn.execute(
            "SELECT version FROM rulebook_versions ORDER BY effective_from"
        )
    ]
    print(f"rulebook versions: {', '.join(versions) or 'none yet'}")
    print(f"grading starts: {_grading_text(conn)}")
    copies = (
        sorted(paths.backups_dir().glob("journal-*.sqlite"))
        if paths.backups_dir().exists()
        else []
    )
    print(
        f"backups: {len(copies)} in {paths.backups_dir()} (latest {copies[-1].name if copies else 'none'})"
    )
    conn.close()
    return 0


def _grading_text(conn) -> str:
    if not paths.rulebook_path().exists():
        return "no rulebook yet"
    rb = rulebook.load(paths.rulebook_path())
    if rb.grading_starts is None:
        return f"with rulebook {rb.version} ({rb.effective_from})"
    if rb.grading_starts != "first-checklist":
        return rb.grading_starts.replace("T", " ")
    first = conn.execute(
        "SELECT min(entry_time) FROM trades WHERE checklist_id IS NOT NULL AND source != 'legacy-csv'"
    ).fetchone()[0]
    when = first[:16].replace("T", " ") if first else "none yet"
    return f"with the first trade that has a checklist ({when})"


def cmd_serve(args) -> int:
    import threading

    s = settings.load()
    port = args.port or s.port
    conn = _open()
    derive.refresh(conn, s)
    backup.daily(conn)
    conn.close()
    info = None if (args.local_only or s.tailnet == "off") else tailnet.info()
    binds, hostnames = service.addresses(
        s if not args.local_only else settings.Settings(), info
    )
    app = web.App(hostnames=hostnames, allowed_peers=s.allowed_peers)
    servers = web.make_servers(binds, port, app)
    for srv in servers:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    stop = threading.Event()

    def backups() -> None:  # one copy a day, checked hourly
        while not stop.wait(3600):
            try:
                c = db.connect()
                backup.daily(c)
                c.close()
            except Exception as e:  # keep serving; say so in the terminal
                print(f"journal: backup failed: {e!r}", file=sys.stderr, flush=True)

    threading.Thread(target=backups, daemon=True).start()
    print(f"journal serving on http://127.0.0.1:{port}/ (this Mac)", flush=True)
    if s.tailnet != "off" and not args.local_only:
        if info is None:
            print(
                "tailnet: Tailscale isn't running, so this Mac only for now", flush=True
            )
        else:
            how = "Tailscale Serve" if s.tailnet == "serve" else "its Tailscale address"
            print(
                f"tailnet ({how}): http://{info.names[1] if info.names else info.ipv4}:{port}/",
                flush=True,
            )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for srv in servers:
            srv.shutdown()
            srv.server_close()
    return 0


def cmd_demo(args) -> int:
    import tempfile

    folder = Path(tempfile.mkdtemp(prefix="trade-journal-demo-"))
    counts = demo.build(folder, trades=args.trades, seed=args.seed)
    g = counts["grades"]
    print(
        f"made-up journal in {folder}: {counts['trades']} trades (A {g['A']} · B {g['B']} · C {g['C']}),"
        f" {counts['checklists']} checklists, {counts['screenshots']} screenshots"
    )
    if args.build_only:
        return 0
    args.local_only = True
    return cmd_serve(args)


def cmd_start(args) -> int:
    import time

    s = settings.load()  # a bad file is refused here, not inside tmux
    rulebook.load(paths.rulebook_path())
    print(service.start(ROOT))
    for _ in range(60):
        if _answering(s.port):
            print(f"answering on http://127.0.0.1:{s.port}/")
            return 0
        time.sleep(0.1)
    print(
        f"not answering yet: tmux attach -t {service.session_name()} to see why",
        file=sys.stderr,
    )
    return 1


def cmd_stop(args) -> int:
    print(service.stop())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="journal", description="The trade journal. Prints counts, never trades."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "init", help="create the private folder, journal, rulebook and settings"
    ).set_defaults(fn=cmd_init)
    m = sub.add_parser("migrate", help="bring the old tracker's rows in, once")
    m.add_argument("csv", nargs="?", default="")
    m.set_defaults(fn=cmd_migrate)
    i = sub.add_parser(
        "inspect",
        help="a TradingView export's layout in names and counts; writes nothing",
    )
    i.add_argument("file")
    i.set_defaults(fn=cmd_inspect)
    im = sub.add_parser("import", help="bring TradingView exports in")
    im.add_argument("files", nargs="+")
    im.set_defaults(fn=cmd_import)
    sub.add_parser("export", help="rewrite exports/trades.csv").set_defaults(
        fn=cmd_export
    )
    sub.add_parser(
        "rulebook", help="check rulebook.toml and record its version"
    ).set_defaults(fn=cmd_rulebook)
    sub.add_parser("status", help="counts only").set_defaults(fn=cmd_status)
    sv = sub.add_parser("serve", help="serve the pages")
    sv.add_argument(
        "--local-only",
        action="store_true",
        help="this Mac only, whatever settings.toml says",
    )
    sv.add_argument("--port", type=int, default=0)
    sv.set_defaults(fn=cmd_serve)
    dm = sub.add_parser(
        "demo", help="a made-up journal in a temp folder, served on this Mac"
    )
    dm.add_argument("--port", type=int, default=8799)
    dm.add_argument("--trades", type=int, default=60)
    dm.add_argument("--seed", type=int, default=7)
    dm.add_argument("--build-only", action="store_true", help="make it, don't serve it")
    dm.set_defaults(fn=cmd_demo)
    sub.add_parser("start", help="run the service in its tmux session").set_defaults(
        fn=cmd_start
    )
    sub.add_parser("stop", help="stop the service's tmux session").set_defaults(
        fn=cmd_stop
    )
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (
        legacy.LayoutError,
        rulebook.RulebookError,
        settings.SettingsError,
        paths.UnsafeLocation,
        web.UnsafeBind,
    ) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
