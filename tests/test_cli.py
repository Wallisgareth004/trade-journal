"""The command line: sets the journal up, migrates, and prints counts, never trades."""

from __future__ import annotations

import subprocess
import sys

from conftest import FIXTURES, ROOT, env_for_cli
from tradejournal import rulebook


def journal(*args):
    return subprocess.run(
        [sys.executable, "-m", "tradejournal", *args],
        cwd=ROOT,
        env=env_for_cli(),
        capture_output=True,
        text=True,
    )


def test_init_writes_a_private_rulebook_with_the_placeholders(private_dirs):
    data, _ = private_dirs
    run = journal("init")
    assert run.returncode == 0, run.stderr
    book = data / "rulebook.toml"
    assert book.stat().st_mode & 0o777 == 0o600
    assert (data / "journal.sqlite").stat().st_mode & 0o777 == 0o600
    rb = rulebook.load(book)
    assert (
        rb.risk_pct,
        rb.min_planned_rr,
        rb.max_trades_per_day,
        rb.session_windows,
    ) == (0.5, 2.0, 3, ())


def test_init_again_keeps_his_rulebook(private_dirs):
    data, _ = private_dirs
    journal("init")
    book = data / "rulebook.toml"
    book.write_text(book.read_text().replace("risk_pct = 0.5", "risk_pct = 1.0"))
    run = journal("init")
    assert run.returncode == 0, run.stderr
    assert rulebook.load(book).risk_pct == 1.0


def test_migrate_prints_counts_and_never_a_trade(private_dirs):
    journal("init")
    run = journal("migrate", str(FIXTURES / "legacy_tracker.csv"))
    assert run.returncode == 0, run.stderr
    assert "2 trades in" in run.stdout
    assert "1 example row skipped" in run.stdout
    for secret in ("XYZ", "QRS", "2010.5", "2005.5", "wick", "sweep-BOS-FVG", "1.0850"):
        assert secret not in run.stdout + run.stderr


def test_a_refused_file_says_why_without_showing_its_rows(private_dirs, tmp_path):
    journal("init")
    bad = tmp_path / "bad.csv"
    bad.write_text("Symbol,Side,Qty\nXYZ,Buy,2010.5\n")
    run = journal("migrate", str(bad))
    assert run.returncode != 0
    assert "layout" in run.stderr.lower()
    assert (
        "XYZ" not in run.stdout + run.stderr and "2010.5" not in run.stdout + run.stderr
    )


def test_status_counts_without_values(private_dirs):
    journal("init")
    journal("migrate", str(FIXTURES / "legacy_tracker.csv"))
    run = journal("status")
    assert run.returncode == 0, run.stderr
    assert "trades: 2" in run.stdout
    assert "old tracker: 2" in run.stdout
    assert "XYZ" not in run.stdout and "2010.5" not in run.stdout


def test_migrate_reads_the_old_tracker_path_from_settings(private_dirs):
    data, _ = private_dirs
    journal("init")
    (data / "settings.toml").write_text(
        f'old_tracker = "{FIXTURES / "legacy_tracker.csv"}"\n'
    )
    run = journal("migrate")
    assert run.returncode == 0, run.stderr
    assert "2 trades in" in run.stdout


def test_migrate_without_a_path_says_where_to_put_one(private_dirs):
    journal("init")
    run = journal("migrate")
    assert run.returncode == 2
    assert "old_tracker" in run.stderr


def test_start_refuses_a_bad_settings_file_before_launching_anything(private_dirs):
    data, _ = private_dirs
    journal("init")
    (data / "settings.toml").write_text('[point_values]\nold_tracker = "x.csv"\n')
    env_session = "journal-never-started"
    import os

    os.environ["TRADE_JOURNAL_TMUX_SESSION"] = env_session
    try:
        run = journal("start")
    finally:
        del os.environ["TRADE_JOURNAL_TMUX_SESSION"]
    assert run.returncode == 2
    assert "refused" in run.stderr and "point_values" in run.stderr
    import subprocess

    assert (
        subprocess.run(
            ["tmux", "has-session", "-t", f"={env_session}"], capture_output=True
        ).returncode
        != 0
    )


def test_status_says_when_grading_starts(private_dirs):
    data, _ = private_dirs
    journal("init")
    assert (
        'grading_starts = "first-checklist"' in (data / "rulebook.toml").read_text()
    )  # the example's default
    run = journal("status")
    assert (
        "grading starts: with the first trade that has a checklist (none yet)"
        in run.stdout
    )
