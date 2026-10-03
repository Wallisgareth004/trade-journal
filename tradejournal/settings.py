"""Journal settings: how to read TradingView's files and where to serve.

These are not trading rules (those live in rulebook.toml). The file is
~/Library/Application Support/trade-journal/settings.toml; with no file the
defaults apply.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import paths


TAILNET_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


class SettingsError(ValueError):
    """A setting can't be used; the message names it."""


@dataclass(frozen=True)
class Settings:
    tradingview_timezone: str = "Africa/Johannesburg"
    checklist_window_minutes: int = 30
    port: int = 8765
    point_values: dict = field(default_factory=dict)
    tailnet: str = "off"
    allowed_peers: tuple = ()
    old_tracker: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tradingview_timezone)

    def point_value(self, symbol: str) -> float:
        """Dollars per point per contract for a futures root (MES, MNQ ...); 1 otherwise."""
        base = symbol.split(":")[-1].upper()
        best = ""
        for root in self.point_values:
            if base.startswith(root.upper()) and len(root) > len(best):
                best = root
        return float(self.point_values[best]) if best else 1.0


KNOWN = {
    "tradingview_timezone",
    "checklist_window_minutes",
    "port",
    "point_values",
    "tailnet",
    "allowed_peers",
    "old_tracker",
}
TAILNET_MODES = ("off", "serve", "bind")


def from_dict(raw: dict) -> Settings:
    unknown = sorted(set(raw) - KNOWN)
    if unknown:
        raise SettingsError(f"unknown setting(s): {', '.join(unknown)}")
    out = {}
    if "tradingview_timezone" in raw:
        tz = raw["tradingview_timezone"]
        try:
            ZoneInfo(str(tz))
        except (ZoneInfoNotFoundError, ValueError):
            raise SettingsError(
                f"tradingview_timezone: {tz!r} isn't a timezone like Africa/Johannesburg or UTC"
            ) from None
        out["tradingview_timezone"] = str(tz)
    if "checklist_window_minutes" in raw:
        v = raw["checklist_window_minutes"]
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 240:
            raise SettingsError(
                "checklist_window_minutes must be a whole number from 1 to 240"
            )
        out["checklist_window_minutes"] = v
    if "port" in raw:
        v = raw["port"]
        if isinstance(v, bool) or not isinstance(v, int) or not 1024 < v < 65536:
            raise SettingsError("port must be a whole number from 1025 to 65535")
        out["port"] = v
    if "point_values" in raw:
        pv = raw["point_values"]
        if not isinstance(pv, dict):
            raise SettingsError(
                "point_values must be a table like [point_values] MES = 5"
            )
        for root, v in pv.items():
            if not re.fullmatch(r"[A-Za-z0-9_.!]{1,20}", root):
                raise SettingsError(f"point_values: {root!r} isn't a symbol root")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise SettingsError(f"point_values: {root} must be a positive number")
        out["point_values"] = dict(pv)
    if "tailnet" in raw:
        if raw["tailnet"] not in TAILNET_MODES:
            raise SettingsError(
                f"tailnet must be off, serve or bind (got {raw['tailnet']!r})"
            )
        out["tailnet"] = raw["tailnet"]
    if "allowed_peers" in raw:
        peers = raw["allowed_peers"]
        if not isinstance(peers, list) or not all(isinstance(x, str) for x in peers):
            raise SettingsError("allowed_peers must be a list of tailnet addresses")
        for peer in peers:
            try:
                ip = ipaddress.ip_address(peer)
            except ValueError:
                raise SettingsError(
                    f"allowed_peers: {peer!r} isn't an address"
                ) from None
            if not any(ip in net for net in TAILNET_NETS):
                raise SettingsError(
                    f"allowed_peers: {peer} isn't a tailnet address (100.64.0.0/10)"
                )
        out["allowed_peers"] = tuple(peers)
    if "old_tracker" in raw:
        if not isinstance(raw["old_tracker"], str):
            raise SettingsError("old_tracker must be a path in quotes")
        out["old_tracker"] = raw["old_tracker"]
    return Settings(**out)


def parse(text: str) -> Settings:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"settings.toml isn't valid TOML: {e}") from None
    return from_dict(raw)


def path():
    return paths.data_dir() / "settings.toml"


def load() -> Settings:
    p = path()
    return parse(p.read_text(encoding="utf-8")) if p.exists() else Settings()
