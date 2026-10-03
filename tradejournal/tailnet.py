"""This Mac's tailnet names and addresses, read from `tailscale status --json`.

Read only: nothing here changes a Tailscale setting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class TailnetInfo:
    ips: list
    names: list

    @property
    def ipv4(self) -> str | None:
        return next((ip for ip in self.ips if "." in ip), None)


def parse(text: str) -> TailnetInfo | None:
    try:
        status = json.loads(text)
    except (ValueError, TypeError):
        return None
    me = status.get("Self") or {}
    ips = list(me.get("TailscaleIPs") or [])
    if status.get("BackendState") != "Running" or not ips:
        return None
    fqdn = (me.get("DNSName") or "").rstrip(".").lower()
    names = [fqdn, fqdn.split(".")[0]] if fqdn else []
    return TailnetInfo(ips=ips, names=names)


def info() -> TailnetInfo | None:
    exe = (
        shutil.which("tailscale")
        or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    )
    try:
        run = subprocess.run(
            [exe, "status", "--json"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse(run.stdout) if run.returncode == 0 else None
