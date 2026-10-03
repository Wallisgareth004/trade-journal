"""`journal serve --local-only` answers on this Mac and nowhere else."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request

from conftest import ROOT, env_for_cli


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_local_only_answers_on_localhost(private_dirs):
    subprocess.run(
        [sys.executable, "-m", "tradejournal", "init"],
        cwd=ROOT,
        env=env_for_cli(),
        check=True,
        capture_output=True,
    )
    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tradejournal",
            "serve",
            "--local-only",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env_for_cli(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        body = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz", timeout=1
                ) as r:
                    body = r.read().decode()
                break
            except OSError:
                time.sleep(0.05)
        assert body == "ok"
        listening = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
        )
        addresses = {line.split()[8] for line in listening.stdout.splitlines()[1:]}
        assert addresses == {f"127.0.0.1:{port}"}
    finally:
        proc.terminate()
        proc.wait(timeout=5)
