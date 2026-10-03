"""The TradingView importer: knows the file by its header, rebuilds each round
trip from the orders, and works out R without retyping a price.

Every expected number here is worked out by hand from the made-up orders.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from conftest import rows
from tradejournal import db, rulebook, settings, tvimport
from tradejournal.synth import (
    ACCOUNT_HEADER,
    HISTORY_HEADER,
    Spec,
    account_rows,
    history_rows,
    to_csv,
)

T0 = datetime(
    2026, 10, 5, 14, 5, 0
)  # a Monday, in the export's timezone (SAST by default)


def history_file(tmp_path, specs, name="history.csv", first_id=1000):
    path = tmp_path / name
    path.write_bytes(to_csv(HISTORY_HEADER, history_rows(specs, first_id=first_id)))
    return path


def do_import(conn, path, **setting_lines):
    text = "\n".join(f"{k} = {v}" for k, v in setting_lines.items())
    return tvimport.import_file(conn, path, settings.parse(text))


def long_target(**over):
    base = dict(
        symbol="TEST:ABCUSD",
        direction="long",
        qty=2,
        entry=100,
        stop=99,
        target=102,
        placed=T0,
        exit_kind="target",
    )
    base.update(over)
    return Spec(**base)


def trade(conn, ref_like="%"):
    return conn.execute(
        "SELECT * FROM trades WHERE trade_ref LIKE ? ORDER BY entry_time", (ref_like,)
    ).fetchall()


# --- knowing the file --------------------------------------------------------


def test_the_history_tab_is_recognised_whatever_the_case_or_bom():
    header = ["﻿symbol", " SIDE ", *[h.lower() for h in HISTORY_HEADER[2:]]]
    assert tvimport.recognise(header) == "history"


def test_the_account_history_tab_is_recognised():
    assert tvimport.recognise(ACCOUNT_HEADER) == "account-history"


def test_a_history_file_missing_a_needed_column_says_which_to_switch_on():
    header = [h for h in HISTORY_HEADER if h != "Stop Price"]
    with pytest.raises(tvimport.LayoutError) as e:
        tvimport.recognise(header)
    assert "Stop Price" in str(e.value)
    assert "switch" in str(e.value).lower()


def test_a_file_that_is_not_from_tradingview_is_refused():
    with pytest.raises(tvimport.LayoutError, match="not a TradingView"):
        tvimport.recognise(["Date", "Description", "Amount"])


def test_a_refusal_never_quotes_the_rows(conn, tmp_path):
    path = tmp_path / "odd.csv"
    path.write_text("Symbol,Side,Qty\nSECRETSYM,Buy,123.456\n")
    with pytest.raises(tvimport.LayoutError) as e:
        do_import(conn, path)
    assert "SECRETSYM" not in str(e.value) and "123.456" not in str(e.value)
    assert rows(conn, "select count(*) from imports") == [(0,)]


# --- rebuilding round trips --------------------------------------------------


def test_a_long_that_hits_its_target(conn, tmp_path):
    do_import(conn, history_file(tmp_path, [long_target()]))
    (t,) = trade(conn)
    assert (
        t["direction"],
        t["entry"],
        t["stop"],
        t["target"],
        t["exit"],
        t["size"],
    ) == ("long", 100, 99, 102, 102, 2)
    assert t["result_r"] == pytest.approx(2.0)
    assert t["planned_rr"] == pytest.approx(2.0)
    assert t["risk_currency"] == pytest.approx(2.0)
    assert t["result_currency"] == pytest.approx(4.0)
    assert (t["exit_reason"], t["stop_placed"]) == ("target", "with-order")
    assert t["entry_time"] == "2026-10-05T14:05:00+02:00"
    assert t["exit_time"] == "2026-10-05T14:35:00+02:00"
    assert (t["weekday"], t["source"], t["asset"]) == (
        "Mon",
        "tradingview-csv",
        "TEST:ABCUSD",
    )
    assert t["trade_ref"] == "2026-10-05-ABCUSD-01"


def test_a_short_that_hits_its_stop(conn, tmp_path):
    spec = Spec(
        symbol="XYZ",
        direction="short",
        qty=1,
        entry=50,
        stop=50.5,
        target=49,
        placed=T0,
        exit_kind="stop",
    )
    do_import(conn, history_file(tmp_path, [spec]))
    (t,) = trade(conn)
    assert (t["direction"], t["exit"], t["exit_reason"]) == ("short", 50.5, "stop")
    assert t["result_r"] == pytest.approx(-1.0)
    assert t["planned_rr"] == pytest.approx(2.0)


def test_a_manual_exit(conn, tmp_path):
    spec = long_target(stop=98, exit_kind="manual", exit_price=101)
    do_import(conn, history_file(tmp_path, [spec]))
    (t,) = trade(conn)
    assert t["exit_reason"] == "manual"
    assert t["result_r"] == pytest.approx(0.5)


def test_fees_come_off_the_result(conn, tmp_path):
    do_import(conn, history_file(tmp_path, [long_target(commission=0.5)]))
    (t,) = trade(conn)
    assert t["fees"] == pytest.approx(1.0)
    assert t["result_currency"] == pytest.approx(3.0)
    assert t["result_r"] == pytest.approx(1.5)


def test_a_futures_point_value_scales_the_money_but_not_the_r(conn, tmp_path):
    spec = long_target(symbol="CME_MINI:MES1!", commission=2.5)
    do_import(conn, history_file(tmp_path, [spec]), **{"[point_values]\nMES": "5"})
    (t,) = trade(conn)
    assert t["risk_currency"] == pytest.approx(10.0)  # 1 point x 2 contracts x $5
    assert t["result_currency"] == pytest.approx(15.0)  # 2 points x 2 x $5 - $5 fees
    assert t["result_r"] == pytest.approx(1.5)


def test_the_export_timezone_comes_from_settings(conn, tmp_path):
    spec = long_target(placed=datetime(2026, 10, 5, 12, 5, 0))
    do_import(conn, history_file(tmp_path, [spec]), tradingview_timezone='"UTC"')
    (t,) = trade(conn)
    assert t["entry_time"] == "2026-10-05T14:05:00+02:00"


def test_scaling_in_averages_the_entry(conn, tmp_path):
    rows_ = history_rows([long_target(qty=1, stop=99.5, target=None, exit_kind="open")])
    add = dict(rows_[-1])  # the entry order
    add.update(
        {
            "Order ID": "2001",
            "Fill Price": "101",
            "Placing Time": "2026-10-05 14:10:00",
            "Closing Time": "2026-10-05 14:10:00",
        }
    )
    stop_row = next(r for r in rows_ if r["Type"] == "Stop")
    stop_row.update(
        {"Qty": "2", "Status": "Cancelled", "Closing Time": "2026-10-05 15:00:00"}
    )
    out = {h: "" for h in HISTORY_HEADER}
    out.update(
        {
            "Symbol": "TEST:ABCUSD",
            "Side": "Sell",
            "Type": "Market",
            "Qty": "2",
            "Fill Price": "102.5",
            "Status": "Filled",
            "Placing Time": "2026-10-05 15:00:00",
            "Closing Time": "2026-10-05 15:00:00",
            "Order ID": "2002",
        }
    )
    path = tmp_path / "h.csv"
    path.write_bytes(to_csv(HISTORY_HEADER, [out, add, *rows_]))
    do_import(conn, path)
    (t,) = trade(conn)
    assert (t["entry"], t["size"], t["exit"]) == (100.5, 2, 102.5)
    assert t["result_r"] == pytest.approx(2.0)  # (102.5 - 100.5) / (100.5 - 99.5)


def test_partial_exits_average_the_exit(conn, tmp_path):
    rows_ = history_rows(
        [long_target(qty=2, target=None, exit_kind="manual", exit_price=102)]
    )
    manual = next(r for r in rows_ if r["Type"] == "Market" and r["Side"] == "Sell")
    manual["Qty"] = "1"
    second = dict(manual)
    second.update(
        {
            "Order ID": "3001",
            "Fill Price": "101",
            "Placing Time": "2026-10-05 14:50:00",
            "Closing Time": "2026-10-05 14:50:00",
        }
    )
    path = tmp_path / "h.csv"
    path.write_bytes(to_csv(HISTORY_HEADER, [second, *rows_]))
    do_import(conn, path)
    (t,) = trade(conn)
    assert t["exit"] == pytest.approx(101.5)
    assert t["result_r"] == pytest.approx(1.5)
    assert t["exit_time"] == "2026-10-05T14:50:00+02:00"


def test_a_position_still_open_is_not_a_trade_yet(conn, tmp_path):
    result = do_import(conn, history_file(tmp_path, [long_target(exit_kind="open")]))
    assert rows(conn, "select count(*) from trades") == [(0,)]
    assert result.open_positions == 1


def test_a_stop_placed_after_the_entry_fill_is_marked(conn, tmp_path):
    do_import(
        conn,
        history_file(tmp_path, [long_target(stop_placed=T0 + timedelta(minutes=2))]),
    )
    (t,) = trade(conn)
    assert t["stop_placed"] == "after-entry"


def test_no_stop_order_means_no_r_and_a_flag(conn, tmp_path):
    do_import(conn, history_file(tmp_path, [long_target(stop=None)]))
    (t,) = trade(conn)
    assert (t["stop"], t["stop_placed"], t["result_r"]) == (None, "none", None)
    assert "no stop order in the export" in json.loads(t["flags_json"])


def test_a_stop_at_the_entry_price_leaves_r_unknown(conn, tmp_path):
    do_import(conn, history_file(tmp_path, [long_target(stop=100)]))
    (t,) = trade(conn)
    assert t["result_r"] is None
    assert any("initial stop" in f for f in json.loads(t["flags_json"]))


def test_a_later_stop_further_away_counts_as_moved(conn, tmp_path):
    spec = long_target(extra_stops=[(T0 + timedelta(minutes=10), 98.5)])
    do_import(conn, history_file(tmp_path, [spec]))
    (t,) = trade(conn)
    assert (t["stop"], t["stop_moved_further"]) == (99, 1)
    assert t["result_r"] == pytest.approx(2.0)  # 1R stays the first stop


def test_a_later_stop_closer_in_is_not_moved_further(conn, tmp_path):
    spec = long_target(extra_stops=[(T0 + timedelta(minutes=10), 99.5)])
    do_import(conn, history_file(tmp_path, [spec]))
    (t,) = trade(conn)
    assert t["stop_moved_further"] == 0


def test_with_one_stop_order_a_move_is_not_visible(conn, tmp_path):
    do_import(conn, history_file(tmp_path, [long_target()]))
    (t,) = trade(conn)
    assert t["stop_moved_further"] is None


# --- importing safely --------------------------------------------------------


def test_the_same_file_twice_changes_nothing(conn, tmp_path):
    path = history_file(tmp_path, [long_target()])
    do_import(conn, path)
    again = do_import(conn, path)
    assert again.already is True
    assert rows(conn, "select count(*) from trades") == [(1,)]
    assert rows(conn, "select count(*) from imports") == [(1,)]


def test_a_later_export_that_overlaps_adds_only_the_new_trade(conn, tmp_path):
    first = long_target()
    second = long_target(placed=T0 + timedelta(hours=2), exit_kind="stop")
    do_import(conn, history_file(tmp_path, [first], name="a.csv"))
    result = do_import(conn, history_file(tmp_path, [first, second], name="b.csv"))
    assert result.new_trades == 1
    assert [t["trade_ref"] for t in trade(conn)] == [
        "2026-10-05-ABCUSD-01",
        "2026-10-05-ABCUSD-02",
    ]


def test_a_copy_of_the_file_is_kept_privately(conn, tmp_path, private_dirs):
    data, _ = private_dirs
    do_import(conn, history_file(tmp_path, [long_target()]))
    (kept,) = list((data / "imports").iterdir())
    assert kept.suffix == ".csv"
    assert kept.stat().st_mode & 0o777 == 0o600


def test_the_session_window_and_rulebook_follow_the_entry_day(conn, tmp_path):
    from test_rulebook import BASE, with_line

    rb = rulebook.parse(
        with_line(
            "session_windows_sast", 'session_windows_sast = ["14:00-18:00"]', BASE
        )
    )
    rulebook.record(conn, rb)
    early = long_target(placed=datetime(2026, 10, 2, 15, 0, 0))  # before v0 starts
    inside = long_target()
    outside = long_target(placed=datetime(2026, 10, 5, 12, 0, 0))
    do_import(conn, history_file(tmp_path, [early, inside, outside]))
    got = {
        t["entry_time"][:16]: (t["session_window"], t["rulebook_version"])
        for t in trade(conn)
    }
    assert got == {
        "2026-10-02T15:00": (None, None),
        "2026-10-05T12:00": ("outside", "v0"),
        "2026-10-05T14:05": ("14:00-18:00", "v0"),
    }


def test_a_trade_already_in_the_old_tracker_is_not_doubled(conn, tmp_path):
    db.insert_trade(
        conn,
        {
            "trade_ref": "2026-10-05-ABC-01",
            "asset": "ABC",
            "direction": "long",
            "source": "legacy-csv",
            "entry_time": "2026-10-05T14:07:00+02:00",
            "entry": 100.1,
            "legacy_json": "{}",
        },
    )
    result = do_import(conn, history_file(tmp_path, [long_target()]))
    assert result.matched_old == 1
    assert rows(conn, "select count(*), max(source) from trades") == [(1, "legacy-csv")]
    assert "1001" in trade(conn)[0]["source_ref"]


# --- account history: the balance at entry ------------------------------------


def test_account_history_gives_the_balance_and_the_risk_percent(conn, tmp_path):
    before = datetime(2026, 10, 5, 10, 0, 0)
    acct = tmp_path / "account.csv"
    acct.write_bytes(
        to_csv(
            ACCOUNT_HEADER,
            account_rows([(before, 400.0, 0.0, "TEST:ABCUSD", 100, 1, 100)]),
        )
    )
    do_import(conn, acct)
    do_import(conn, history_file(tmp_path, [long_target()]))
    (t,) = trade(conn)
    assert t["balance_at_entry"] == pytest.approx(400.0)
    assert t["risk_pct"] == pytest.approx(0.5)  # 1R of $2 on $400


def test_a_realised_pnl_that_disagrees_is_flagged(conn, tmp_path):
    do_import(conn, history_file(tmp_path, [long_target()]))
    acct = tmp_path / "account.csv"
    close = datetime(2026, 10, 5, 14, 35, 0)
    acct.write_bytes(
        to_csv(
            ACCOUNT_HEADER,
            account_rows([(close, 400.0, 40.0, "TEST:ABCUSD", 102, 2, 100)]),
        )
    )
    do_import(conn, acct)
    (t,) = trade(conn)
    assert any("realised" in f for f in json.loads(t["flags_json"]))


def test_an_averaged_price_carries_no_floating_point_noise(conn, tmp_path):
    do_import(
        conn,
        history_file(
            tmp_path, [long_target(qty=5, entry=2023.51, stop=2021.44, target=2027.65)]
        ),
    )
    (t,) = trade(conn)
    assert t["entry"] == 2023.51
    from tradejournal import export

    with open(export.write(conn), encoding="utf-8") as fh:
        assert "2023.5099" not in fh.read()
