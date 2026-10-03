"""Journal settings (not trading rules): TradingView's timezone, point values, port."""

from __future__ import annotations

import pytest

from conftest import ROOT
from tradejournal import settings


def test_with_no_file_the_defaults_apply():
    s = settings.load()
    assert s.tradingview_timezone == "Africa/Johannesburg"
    assert s.checklist_window_minutes == 30
    assert s.point_value("OANDA:XAUUSD") == 1.0


def test_the_shipped_example_loads():
    s = settings.parse((ROOT / "examples" / "settings.toml").read_text())
    assert s.tradingview_timezone == "Africa/Johannesburg"


def test_a_point_value_is_found_by_root_symbol():
    s = settings.parse('[point_values]\nMES = 5\n"MNQ" = 2\n')
    assert s.point_value("CME_MINI:MES1!") == 5.0
    assert s.point_value("MESZ2026") == 5.0
    assert s.point_value("MNQ1!") == 2.0
    assert s.point_value("BTCUSD") == 1.0


@pytest.mark.parametrize(
    "text, key",
    [
        ('tradingview_timezone = "Mars/Olympus"', "tradingview_timezone"),
        ("checklist_window_minutes = 0", "checklist_window_minutes"),
        ("[point_values]\nMES = -5", "MES"),
        ("port = 80", "port"),
        ("colour = 1", "colour"),
    ],
)
def test_a_bad_setting_is_refused_by_name(text, key):
    with pytest.raises(settings.SettingsError, match=key):
        settings.parse(text)
