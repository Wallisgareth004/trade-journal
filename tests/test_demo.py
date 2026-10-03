"""The made-up demo journal: complete enough to show every page, and never a real one."""

from __future__ import annotations

import os
from datetime import date

from tradejournal import demo, screenshots


def test_the_demo_fills_a_journal_of_its_own(tmp_path, private_dirs):
    real_home, _ = private_dirs
    counts = demo.build(tmp_path / "demo", trades=40, seed=7, today=date(2026, 11, 2))
    assert counts["trades"] == 40
    assert (
        counts["grades"]["A"] > 0
        and counts["grades"]["B"] > 0
        and counts["grades"]["C"] > 0
    )
    assert counts["screenshots"] > 0
    assert os.environ["TRADE_JOURNAL_HOME"] == str(tmp_path / "demo" / "data")
    assert not real_home.exists()  # the test's own journal folder was never touched


def test_the_same_seed_gives_the_same_demo(tmp_path):
    a = demo.build(tmp_path / "a", trades=30, seed=3, today=date(2026, 11, 2))
    b = demo.build(tmp_path / "b", trades=30, seed=3, today=date(2026, 11, 2))
    assert a == b


def test_the_demo_charts_are_real_pngs():
    data = demo.chart_png(seed=1)
    assert screenshots.kind(data) == "png"
    assert len(data) > 200


def test_the_demo_command_builds_in_a_temp_folder_and_says_where(
    tmp_path, private_dirs
):
    import subprocess
    import sys

    from conftest import ROOT, env_for_cli

    env = env_for_cli()
    env["TMPDIR"] = str(tmp_path)
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "tradejournal",
            "demo",
            "--build-only",
            "--trades",
            "20",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert "made-up journal in" in run.stdout and "20 trades" in run.stdout
    made = [p for p in tmp_path.iterdir() if p.name.startswith("trade-journal-demo-")]
    assert len(made) == 1 and (made[0] / "data" / "journal.sqlite").exists()
    real_home, _ = private_dirs
    assert not real_home.exists()
