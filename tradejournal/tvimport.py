"""TradingView paper-trading exports into the journal.

The file is recognised by its header row: the History tab (every order, with
its fill, stop and limit prices) or the Account history tab (balances and
realised P&L). Anything else is refused with the list of columns to switch
on, never guessed at. Error messages name columns and line numbers, never a
value from the file.

History is the main source. Every order is kept (`orders`), and the round
trips are rebuilt from all of them, so a later export that overlaps an
earlier one adds only what's new. Each trip's first entry order is its key.
Importing the same file twice changes nothing.

TradingView publishes no column headers or timezone; the names below follow
other journals' documentation and are confirmed against a real export. The
timezone comes from settings.toml.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import db, derive, paths
from .legacy import SAST, LayoutError, same_trade
from .settings import Settings

HISTORY_REQUIRED = [
    "Symbol",
    "Side",
    "Type",
    "Qty",
    "Limit Price",
    "Stop Price",
    "Fill Price",
    "Status",
    "Placing Time",
    "Closing Time",
    "Order ID",
]
HISTORY_OPTIONAL = ["Commission", "Level ID", "Leverage", "Margin"]
ACCOUNT_REQUIRED = [
    "Time",
    "Balance Before",
    "Balance After",
    "Realized P&L (value)",
    "Action",
]
ALIASES = {"Realized P&L (value)": ["realized p&l (value)", "realized p&l"]}

TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)
ACTION = re.compile(
    r"close (long|short) position for symbol (\S+) at price ([\d.,]+) for ([\d.,]+) units",
    re.IGNORECASE,
)
STOP_TYPES = {"stop", "stop limit", "stop market", "stop loss"}
LIMIT_TYPES = {"limit", "take profit"}
WITH_ORDER_SLACK = timedelta(seconds=5)


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("﻿", "")).strip().casefold()


def _names(canonical: str) -> list[str]:
    return ALIASES.get(canonical, [_key(canonical)])


def _missing(keys: set[str], wanted: list[str]) -> list[str]:
    return [c for c in wanted if not any(a in keys for a in _names(c))]


def recognise(header: list[str]) -> str:
    """'history' or 'account-history'; LayoutError for anything else."""
    keys = {_key(h) for h in header}
    missing = _missing(keys, HISTORY_REQUIRED)
    if not missing:
        return "history"
    if not _missing(keys, ACCOUNT_REQUIRED):
        return "account-history"
    if {"symbol", "side"} <= keys or "order id" in keys:
        raise LayoutError(
            "a TradingView file, but not the History tab, or some of its columns are switched off."
            f" Missing: {', '.join(missing)}. In TradingView's History panel, switch those columns on"
            " (the column settings), then export again."
        )
    if {"time", "action"} <= keys:
        gap = _missing(keys, ACCOUNT_REQUIRED)
        raise LayoutError(f"looks like Account history, but it lacks: {', '.join(gap)}")
    raise LayoutError(
        "not a TradingView paper-trading export: the journal reads the History tab"
        " (and Account history for balances)"
    )


# --- reading cells -----------------------------------------------------------


def _number(cell: str, line: int, col: str) -> float | None:
    s = cell.strip().replace("−", "-")
    if not s:
        return None
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        raise LayoutError(f"line {line}: {col} isn't a number") from None


def _time(cell: str, settings: Settings, line: int, col: str) -> str | None:
    s = cell.strip()
    if not s:
        return None
    for fmt in TIME_FORMATS:
        try:
            naive = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return (
            naive.replace(tzinfo=settings.tz)
            .astimezone(SAST)
            .isoformat(timespec="seconds")
        )
    raise LayoutError(f"line {line}: {col} isn't a time like 2026-10-05 14:05:00")


def _read(path: Path) -> tuple[list[str], list[tuple[int, list[str]]], bytes]:
    return _parse(Path(path).read_bytes())


def _parse(raw: bytes) -> tuple[list[str], list[tuple[int, list[str]]], bytes]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LayoutError("not a text CSV file") from None
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, [])
    body = [(n, r) for n, r in enumerate(reader, start=2) if any(c.strip() for c in r)]
    return header, body, raw


def _columns(header: list[str], wanted: list[str]) -> dict[str, int]:
    keys = [_key(h) for h in header]
    out = {}
    for canonical in wanted:
        for alias in _names(canonical):
            if alias in keys:
                out[canonical] = keys.index(alias)
                break
    return out


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str
    type: str
    qty: float
    limit_price: float | None
    stop_price: float | None
    fill_price: float | None
    status: str
    commission: float
    placing_time: str | None
    closing_time: str | None
    level_id: str | None
    leverage: str | None
    raw: dict = field(default_factory=dict)


def _orders(header, body, settings: Settings) -> list[Order]:
    col = _columns(header, HISTORY_REQUIRED + HISTORY_OPTIONAL)
    out = []
    for line, cells in body:
        if len(cells) < len(header):
            cells = cells + [""] * (len(header) - len(cells))
        get = lambda name: cells[col[name]] if name in col else ""  # noqa: E731
        side = get("Side").strip().lower()
        if side not in ("buy", "sell"):
            raise LayoutError(f"line {line}: Side must be Buy or Sell")
        qty = _number(get("Qty"), line, "Qty")
        if qty is None or qty <= 0:
            raise LayoutError(f"line {line}: Qty must be a positive number")
        order_id = get("Order ID").strip()
        if not order_id:
            raise LayoutError(f"line {line}: Order ID is empty")
        status = get("Status").strip().lower().replace("canceled", "cancelled")
        out.append(
            Order(
                order_id=order_id,
                symbol=get("Symbol").strip(),
                side=side,
                type=get("Type").strip().lower(),
                qty=qty,
                limit_price=_number(get("Limit Price"), line, "Limit Price"),
                stop_price=_number(get("Stop Price"), line, "Stop Price"),
                fill_price=_number(get("Fill Price"), line, "Fill Price"),
                status=status,
                commission=abs(_number(get("Commission"), line, "Commission") or 0.0),
                placing_time=_time(get("Placing Time"), settings, line, "Placing Time"),
                closing_time=_time(get("Closing Time"), settings, line, "Closing Time"),
                level_id=get("Level ID").strip() or None,
                leverage=get("Leverage").strip() or None,
                raw=dict(zip(header, cells)),
            )
        )
        if out[-1].status == "filled" and (
            out[-1].fill_price is None or out[-1].closing_time is None
        ):
            raise LayoutError(
                f"line {line}: a filled order needs its Fill Price and Closing Time"
            )
    return out


# --- round trips -------------------------------------------------------------


@dataclass
class Trip:
    symbol: str
    direction: str
    entries: list = field(default_factory=list)  # (order, qty)
    exits: list = field(default_factory=list)
    closed: bool = False

    @property
    def key(self) -> str:
        return f"tv:{self.entries[0][0].order_id}"


def _sort_key(o: Order):
    digits = int(o.order_id) if o.order_id.isdigit() else 0
    return (o.closing_time or "", o.placing_time or "", digits, o.order_id)


def build_trips(orders: list[Order]) -> tuple[list[Trip], int]:
    """Closed round trips from the filled orders, and how many positions are still open."""
    trips: list[Trip] = []
    open_count = 0
    by_symbol: dict[str, list[Order]] = {}
    for o in orders:
        if o.status == "filled":
            by_symbol.setdefault(o.symbol, []).append(o)
    for symbol, fills in by_symbol.items():
        pos = 0.0
        trip: Trip | None = None
        for o in sorted(fills, key=_sort_key):
            remaining = o.qty if o.side == "buy" else -o.qty
            while abs(remaining) > 1e-12:
                if trip is None:
                    trip = Trip(symbol, "long" if remaining > 0 else "short")
                    trips.append(trip)
                if pos == 0 or (pos > 0) == (remaining > 0):
                    trip.entries.append((o, abs(remaining)))
                    pos += remaining
                    remaining = 0.0
                else:
                    close = min(abs(remaining), abs(pos))
                    trip.exits.append((o, close))
                    step = close if remaining > 0 else -close
                    pos += step
                    remaining -= step
                    if abs(pos) < 1e-9 * max(1.0, o.qty):
                        pos = 0.0
                        trip.closed = True
                        trip = None
        if trip is not None and not trip.closed:
            open_count += 1
    return [t for t in trips if t.closed], open_count


def _avg(parts: list) -> float:
    """The quantity-weighted fill price, rounded past any real tick size so
    2023.51 x 5 / 5 comes back as 2023.51, not 2023.5099999999998."""
    total = sum(q for _, q in parts)
    return round(sum(o.fill_price * q for o, q in parts) / total, 9)


def _bracket(trip: Trip, orders: list[Order]) -> tuple[list[Order], Order | None]:
    """The trip's stop orders (oldest first) and its target order, from all its orders."""
    exit_side = "sell" if trip.direction == "long" else "buy"
    first = trip.entries[0][0]
    start = (
        datetime.fromisoformat(first.placing_time or first.closing_time)
        - WITH_ORDER_SLACK
    )
    end = datetime.fromisoformat(trip.exits[-1][0].closing_time)
    entry = _avg(trip.entries)
    stops, targets = [], []
    for o in orders:
        if (
            o.symbol != trip.symbol
            or o.side != exit_side
            or o.status == "rejected"
            or not o.placing_time
        ):
            continue
        placed = datetime.fromisoformat(o.placing_time)
        if not start <= placed <= end:
            continue
        if o.type in STOP_TYPES and o.stop_price is not None:
            stops.append(o)
        elif o.type in LIMIT_TYPES and o.limit_price is not None:
            profit_side = (
                o.limit_price > entry
                if trip.direction == "long"
                else o.limit_price < entry
            )
            if profit_side:
                targets.append(o)
    stops.sort(key=lambda o: (o.placing_time, _sort_key(o)))
    targets.sort(key=lambda o: (o.placing_time, _sort_key(o)))
    return stops, (targets[0] if targets else None)


def trip_fields(trip: Trip, orders: list[Order], settings: Settings) -> dict:
    """The order facts of one round trip. R and everything rule-related come later (derive)."""
    entry = _avg(trip.entries)
    exit_ = _avg(trip.exits)
    size = sum(q for _, q in trip.entries)
    first = trip.entries[0][0]
    last = trip.exits[-1][0]
    stops, target = _bracket(trip, orders)
    fees = 0.0
    for o, q in trip.entries + trip.exits:
        fees += o.commission * (q / o.qty)

    stop_placed = "none"
    moved = None
    stop = None
    if stops:
        initial = stops[0]
        stop = initial.stop_price
        filled_at = datetime.fromisoformat(first.closing_time)
        placed_at = datetime.fromisoformat(initial.placing_time)
        stop_placed = (
            "with-order" if placed_at <= filled_at + WITH_ORDER_SLACK else "after-entry"
        )
        if len(stops) > 1:
            further = [
                s
                for s in stops[1:]
                if (
                    s.stop_price < stop
                    if trip.direction == "long"
                    else s.stop_price > stop
                )
            ]
            moved = 1 if further else 0

    kind = last.type
    exit_reason = (
        "stop"
        if kind in STOP_TYPES
        else "target"
        if kind in LIMIT_TYPES
        else "manual"
        if kind == "market"
        else kind
    )
    used = {o.order_id for o, _ in trip.entries + trip.exits} | {
        o.order_id for o in stops
    }
    if target is not None:
        used.add(target.order_id)
    return {
        "trip_key": trip.key,
        "source_ref": ",".join(sorted(used, key=lambda x: (len(x), x))),
        "asset": trip.symbol,
        "direction": trip.direction,
        "placed_time": first.placing_time or first.closing_time,
        "entry_time": first.closing_time,
        "exit_time": last.closing_time,
        "entry": entry,
        "stop": stop,
        "target": target.limit_price if target is not None else None,
        "exit": exit_,
        "size": size,
        "leverage": first.leverage,
        "point_value": settings.point_value(trip.symbol),
        "fees": round(fees, 10),
        "exit_reason": exit_reason,
        "stop_placed": stop_placed,
        "stop_moved_further": moved,
    }


# --- writing to the journal --------------------------------------------------


@dataclass
class ImportResult:
    kind: str
    already: bool = False
    rows: int = 0
    new_orders: int = 0
    updated_orders: int = 0
    new_trades: int = 0
    updated_trades: int = 0
    matched_old: int = 0
    open_positions: int = 0
    new_balances: int = 0


def short_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", symbol.split(":")[-1].upper()) or "X"


def _new_ref(conn: sqlite3.Connection, entry_time: str, symbol: str) -> str:
    day = datetime.fromisoformat(entry_time).astimezone(SAST).date().isoformat()
    stem = f"{day}-{short_symbol(symbol)}-"
    n = 1
    while conn.execute(
        "SELECT 1 FROM trades WHERE trade_ref = ?", (f"{stem}{n:02d}",)
    ).fetchone():
        n += 1
    return f"{stem}{n:02d}"


def _old_tracker_match(conn: sqlite3.Connection, f: dict) -> sqlite3.Row | None:
    """The one old-tracker trade that is this trip, if exactly one fits."""
    hits = [
        t
        for t in conn.execute(
            "SELECT * FROM trades WHERE source = 'legacy-csv' AND trip_key IS NULL AND direction = ?",
            (f["direction"],),
        )
        if same_trade(t, f)
    ]
    return hits[0] if len(hits) == 1 else None


def _upsert_orders(conn, orders: list[Order], import_id: int) -> tuple[int, int]:
    new = updated = 0
    for o in orders:
        values = (
            o.symbol,
            o.side,
            o.type,
            o.qty,
            o.limit_price,
            o.stop_price,
            o.fill_price,
            o.status,
            o.commission,
            o.placing_time,
            o.closing_time,
            o.level_id,
            o.leverage,
            json.dumps(o.raw, ensure_ascii=False),
        )
        old = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (o.order_id,)
        ).fetchone()
        if old is None:
            conn.execute(
                "INSERT INTO orders (symbol, side, type, qty, limit_price, stop_price, fill_price, status,"
                " commission, placing_time, closing_time, level_id, leverage, raw_json, order_id, import_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, o.order_id, import_id),
            )
            new += 1
        elif tuple(
            old[k]
            for k in (
                "status",
                "fill_price",
                "closing_time",
                "stop_price",
                "limit_price",
            )
        ) != (
            o.status,
            o.fill_price,
            o.closing_time,
            o.stop_price,
            o.limit_price,
        ):
            conn.execute(
                "UPDATE orders SET symbol = ?, side = ?, type = ?, qty = ?, limit_price = ?, stop_price = ?,"
                " fill_price = ?, status = ?, commission = ?, placing_time = ?, closing_time = ?, level_id = ?,"
                " leverage = ?, raw_json = ? WHERE order_id = ?",
                (*values, o.order_id),
            )
            updated += 1
    return new, updated


def _stored_orders(conn) -> list[Order]:
    out = []
    for r in conn.execute("SELECT * FROM orders"):
        out.append(
            Order(
                order_id=r["order_id"],
                symbol=r["symbol"],
                side=r["side"],
                type=r["type"],
                qty=r["qty"],
                limit_price=r["limit_price"],
                stop_price=r["stop_price"],
                fill_price=r["fill_price"],
                status=r["status"],
                commission=r["commission"] or 0.0,
                placing_time=r["placing_time"],
                closing_time=r["closing_time"],
                level_id=r["level_id"],
                leverage=r["leverage"],
            )
        )
    return out


def _rebuild(conn, settings: Settings, result: ImportResult, stamp: str) -> None:
    orders = _stored_orders(conn)
    trips, result.open_positions = build_trips(orders)
    for trip in sorted(trips, key=lambda t: t.entries[0][0].closing_time):
        f = trip_fields(trip, orders, settings)
        existing = conn.execute(
            "SELECT id FROM trades WHERE trip_key = ?", (f["trip_key"],)
        ).fetchone()
        if existing:
            db.update_trade(conn, existing["id"], f, commit=False)
            result.updated_trades += 1
            trade_id = existing["id"]
        else:
            old = _old_tracker_match(conn, f)
            if old is not None:
                db.update_trade(
                    conn,
                    old["id"],
                    {"trip_key": f["trip_key"], "source_ref": f["source_ref"]},
                    commit=False,
                )
                result.matched_old += 1
                trade_id = old["id"]
            else:
                trade_id = db.insert_trade(
                    conn,
                    {
                        **f,
                        "trade_ref": _new_ref(conn, f["entry_time"], f["asset"]),
                        "source": "tradingview-csv",
                        "journaled_at": stamp,
                    },
                    commit=False,
                )
                result.new_trades += 1
        ids = f["source_ref"].split(",")
        conn.execute(
            f"UPDATE orders SET trade_id = ? WHERE order_id IN ({','.join('?' * len(ids))})",
            (trade_id, *ids),
        )


def _balances(conn, header, body, settings: Settings, import_id: int) -> int:
    col = _columns(header, ACCOUNT_REQUIRED)
    new = 0
    for line, cells in body:
        get = lambda name: cells[col[name]] if col[name] < len(cells) else ""  # noqa: E731
        when = _time(get("Time"), settings, line, "Time")
        if when is None:
            raise LayoutError(f"line {line}: Time is empty")
        cur = conn.execute(
            "INSERT OR IGNORE INTO balances (time, balance_before, balance_after, realized_pnl, action, import_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                when,
                _number(get("Balance Before"), line, "Balance Before"),
                _number(get("Balance After"), line, "Balance After"),
                _number(get("Realized P&L (value)"), line, "Realized P&L"),
                get("Action").strip(),
                import_id,
            ),
        )
        new += cur.rowcount
    return new


def inspect(path: Path | str, settings: Settings) -> dict:
    """Facts about a file's layout, for confirming the format. Writes nothing and
    returns no prices, symbols or times: only names and counts."""
    header, body, _ = _read(Path(path))
    kind = recognise(header)
    facts = {"kind": kind, "columns": header, "rows": len(body), "missing_optional": []}
    if kind == "account-history":
        col = _columns(header, ACCOUNT_REQUIRED)
        actions = [
            cells[col["Action"]] for _, cells in body if col["Action"] < len(cells)
        ]
        facts["actions_understood"] = sum(1 for a in actions if ACTION.search(a))
        return facts
    keys = {_key(h) for h in header}
    facts["missing_optional"] = _missing(keys, HISTORY_OPTIONAL)
    orders = _orders(header, body, settings)
    trips, open_count = build_trips(orders)

    def values(canonical: str) -> list[str]:
        name = header[_columns(header, [canonical])[canonical]]
        return sorted({o.raw.get(name, "").strip() for o in orders})

    facts.update(
        orders=len(orders),
        statuses=values("Status"),
        sides=values("Side"),
        types=values("Type"),
        with_commission=sum(1 for o in orders if o.commission),
        closed_trips=len(trips),
        open_trips=open_count,
    )
    shapes = [trip_fields(t, orders, settings) for t in trips]

    def past_entry(f: dict) -> bool:
        sign = 1 if f["direction"] == "long" else -1
        return f["stop"] is not None and (f["entry"] - f["stop"]) * sign <= 0

    facts["trips_without_stop"] = sum(1 for f in shapes if f["stop"] is None)
    facts["trips_with_several_stops"] = sum(
        1 for f in shapes if f["stop_moved_further"] is not None
    )
    facts["trips_stop_at_or_past_entry"] = sum(1 for f in shapes if past_entry(f))
    facts["trips_without_target"] = sum(1 for f in shapes if f["target"] is None)
    facts["stop_placed"] = {
        k: sum(1 for f in shapes if f["stop_placed"] == k)
        for k in ("with-order", "after-entry", "none")
    }
    return facts


def import_file(
    conn: sqlite3.Connection,
    path: Path | str,
    settings: Settings,
    now: datetime | None = None,
) -> ImportResult:
    """Import one TradingView export. All or nothing; the same file twice changes nothing."""
    return import_bytes(conn, Path(path).read_bytes(), Path(path).name, settings, now)


def import_bytes(
    conn: sqlite3.Connection,
    raw: bytes,
    name: str,
    settings: Settings,
    now: datetime | None = None,
) -> ImportResult:
    """The same, for a file that arrived by upload."""
    header, body, raw = _parse(raw)
    kind = recognise(header)
    sha = hashlib.sha256(raw).hexdigest()
    result = ImportResult(kind=kind, rows=len(body))
    if conn.execute("SELECT 1 FROM imports WHERE sha256 = ?", (sha,)).fetchone():
        result.already = True
        return result
    orders = _orders(header, body, settings) if kind == "history" else []
    stamp = (now or datetime.now(SAST)).isoformat(timespec="seconds")
    with conn:
        cur = conn.execute(
            "INSERT INTO imports (sha256, kind, file_name, imported_at, rows) VALUES (?, ?, ?, ?, ?)",
            (sha, kind, Path(name).name, stamp, len(body)),
        )
        import_id = cur.lastrowid
        if kind == "history":
            result.new_orders, result.updated_orders = _upsert_orders(
                conn, orders, import_id
            )
            _rebuild(conn, settings, result, stamp)
        else:
            result.new_balances = _balances(conn, header, body, settings, import_id)
        conn.execute(
            "UPDATE imports SET new_orders = ?, new_trades = ?, notes_json = ? WHERE id = ?",
            (
                result.new_orders,
                result.new_trades,
                json.dumps(
                    {
                        "matched_old": result.matched_old,
                        "open_positions": result.open_positions,
                    }
                ),
                import_id,
            ),
        )
    paths.write_private(paths.imports_dir() / f"{sha[:16]}-{kind}.csv", raw)
    derive.refresh(conn, settings)
    return result
