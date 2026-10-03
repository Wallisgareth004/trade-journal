"""Running the journal: which addresses it answers on, and its tmux session.

The tailnet stays off until the user picks how a phone reaches it:
  serve  Tailscale Serve forwards the tailnet port to the journal on localhost
  bind   the journal listens on the Mac's Tailscale address itself
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request

import pytest

from conftest import ROOT, env_for_cli
from tradejournal import service, settings, tailnet

FAKE_STATUS = json.dumps(
    {
        "BackendState": "Running",
        "Self": {
            "DNSName": "mini.tail0000.ts.net.",
            "HostName": "Mini",
            "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
            "Online": True,
        },
        "Peer": {
            "x": {"DNSName": "phone.tail0000.ts.net.", "TailscaleIPs": ["100.64.0.2"]}
        },
    }
)


def test_the_tailnet_names_and_addresses_come_from_tailscale_status():
    info = tailnet.parse(FAKE_STATUS)
    assert info.ipv4 == "100.64.0.1"
    assert info.names == ["mini.tail0000.ts.net", "mini"]
    assert info.ips == ["100.64.0.1", "fd7a:115c:a1e0::1"]


def test_a_stopped_tailscale_gives_nothing():
    assert tailnet.parse(json.dumps({"BackendState": "Stopped", "Self": {}})) is None
    assert tailnet.parse("not json") is None


@pytest.mark.parametrize(
    "mode, binds, extra_names",
    [
        ("off", ["127.0.0.1"], set()),
        (
            "serve",
            ["127.0.0.1"],
            {"mini.tail0000.ts.net", "mini", "100.64.0.1", "fd7a:115c:a1e0::1"},
        ),
        (
            "bind",
            ["127.0.0.1", "100.64.0.1"],
            {"mini.tail0000.ts.net", "mini", "100.64.0.1", "fd7a:115c:a1e0::1"},
        ),
    ],
)
def test_each_mode_answers_on_its_own_addresses(mode, binds, extra_names):
    s = settings.parse(f'tailnet = "{mode}"')
    got_binds, names = service.addresses(s, tailnet.parse(FAKE_STATUS))
    assert got_binds == binds
    assert names == {"127.0.0.1", "localhost"} | extra_names


def test_a_tailnet_mode_without_tailscale_running_stays_on_this_mac():
    s = settings.parse('tailnet = "bind"')
    assert service.addresses(s, None) == (["127.0.0.1"], {"127.0.0.1", "localhost"})


@pytest.mark.parametrize(
    "text, key",
    [
        ('tailnet = "public"', "tailnet"),
        ('allowed_peers = ["192.168.1.4"]', "allowed_peers"),
    ],
)
def test_a_bad_network_setting_is_refused(text, key):
    with pytest.raises(settings.SettingsError, match=key):
        settings.parse(text)


def test_the_tmux_command_names_the_session_and_carries_the_private_folders():
    argv = service.tmux_start_argv(
        ROOT,
        "journal-test",
        {"TRADE_JOURNAL_HOME": "/x/data", "TRADE_JOURNAL_BACKUPS": "/x/b"},
    )
    assert argv[:5] == ["tmux", "new-session", "-d", "-s", "journal-test"]
    assert "TRADE_JOURNAL_HOME=/x/data" in argv and "TRADE_JOURNAL_BACKUPS=/x/b" in argv
    assert argv[-1] == f"{shlex.quote(str(ROOT / 'bin' / 'journal'))} serve"


@pytest.mark.skipif(
    not os.environ.get("TJ_TMUX_TEST"),
    reason="starts a real tmux session; run with TJ_TMUX_TEST=1",
)
def test_start_status_stop_with_a_real_tmux_session(private_dirs, tmp_path):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = env_for_cli()
    env["TRADE_JOURNAL_TMUX_SESSION"] = "journal-selftest"
    run = lambda *a: subprocess.run(
        [sys.executable, "-m", "tradejournal", *a],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )  # noqa: E731
    run("init")
    data, _ = private_dirs
    (data / "settings.toml").write_text(f"port = {port}\n")
    try:
        assert run("start").returncode == 0
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        out = run("status").stdout
        assert "service: running" in out and "answering" in out
    finally:
        run("stop")
    assert "service: stopped" in run("status").stdout
