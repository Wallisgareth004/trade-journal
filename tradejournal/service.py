"""Running the journal as a service: a tmux session (`journal`), like other
repeat jobs. After a reboot: `bin/journal start`.

The addresses depend on settings.toml's `tailnet`:
  off    this Mac only (localhost)
  serve  localhost only; Tailscale Serve forwards the tailnet port here
  bind   localhost and the Mac's Tailscale address
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from .settings import Settings
from .tailnet import TailnetInfo

LOCAL = ["127.0.0.1"]
LOCAL_NAMES = {"127.0.0.1", "localhost"}


def session_name() -> str:
    return os.environ.get("TRADE_JOURNAL_TMUX_SESSION", "journal")


def addresses(s: Settings, info: TailnetInfo | None) -> tuple[list[str], set[str]]:
    """(the addresses to listen on, the host names to answer to)."""
    if s.tailnet == "off" or info is None:
        return list(LOCAL), set(LOCAL_NAMES)
    names = set(LOCAL_NAMES) | set(info.names) | set(info.ips)
    if s.tailnet == "bind" and info.ipv4:
        return [*LOCAL, info.ipv4], names
    return list(LOCAL), names


def tmux_start_argv(repo: Path, session: str, env: dict) -> list[str]:
    argv = ["tmux", "new-session", "-d", "-s", session]
    for key in ("TRADE_JOURNAL_HOME", "TRADE_JOURNAL_BACKUPS"):
        if env.get(key):
            argv += ["-e", f"{key}={env[key]}"]
    argv.append(f"{shlex.quote(str(repo / 'bin' / 'journal'))} serve")
    return argv


def running(session: str) -> bool:
    return (
        subprocess.run(
            ["tmux", "has-session", "-t", f"={session}"], capture_output=True
        ).returncode
        == 0
    )


def start(repo: Path) -> str:
    session = session_name()
    if running(session):
        return f"already running in tmux session '{session}'"
    subprocess.run(tmux_start_argv(repo, session, dict(os.environ)), check=True)
    return f"started in tmux session '{session}' (tmux attach -t {session} to watch it)"


def stop() -> str:
    session = session_name()
    if not running(session):
        return "not running"
    subprocess.run(["tmux", "kill-session", "-t", f"={session}"], check=True)
    return f"stopped tmux session '{session}'"
