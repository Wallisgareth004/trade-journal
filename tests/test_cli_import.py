"""`journal inspect` and `journal import`: counts and layout facts, never a trade."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta

from conftest import ROOT, env_for_cli
from tradejournal.synth import HISTORY_HEADER, Spec, history_rows, to_csv

T0 = datetime(2026, 10, 5, 14, 5, 0)
SECRETS = ("SECRETSYM", "4321.5", "4311.5", "4341.5", "4300.25")


def journal(*args):
    return subprocess.run(
        [sys.executable, "-m", "tradejournal", *args],
        cwd=ROOT,
        env=env_for_cli(),
        capture_output=True,
        text=True,
    )


def export_file(tmp_path, with_commission=False):
    specs = [
        Spec(
            symbol="EXCH:SECRETSYM",
            direction="long",
            qty=3,
            entry=4321.5,
            stop=4311.5,
            target=4341.5,
            placed=T0,
            exit_kind="target",
            commission=1.25 if with_commission else 0.0,
        ),
        Spec(
            symbol="EXCH:SECRETSYM",
            direction="short",
            qty=3,
            entry=4321.5,
            stop=4341.5,
            target=4300.25,
            placed=T0 + timedelta(hours=1),
            exit_kind="stop",
        ),
        Spec(
            symbol="EXCH:SECRETSYM",
            direction="long",
            qty=1,
            entry=4321.5,
            stop=4311.5,
            target=4341.5,
            placed=T0 + timedelta(hours=3),
            exit_kind="open",
        ),
    ]
    path = tmp_path / "paper-trading-history.csv"
    path.write_bytes(to_csv(HISTORY_HEADER, history_rows(specs)))
    return path


def test_inspect_reports_the_layout_and_counts_without_values(tmp_path):
    run = journal("inspect", str(export_file(tmp_path, with_commission=True)))
    assert run.returncode == 0, run.stderr
    out = run.stdout
    assert "History tab" in out
    assert "orders: 9" in out  # 3 brackets x (entry, stop, target)
    assert "round trips: 2 closed, 1 open" in out
    assert "statuses: Cancelled, Filled, Working" in out
    assert "orders with commission: 2" in out
    for secret in SECRETS:
        assert secret not in out + run.stderr


def test_inspect_changes_nothing(tmp_path, private_dirs):
    data, _ = private_dirs
    journal("inspect", str(export_file(tmp_path)))
    assert not (data / "journal.sqlite").exists()


def test_import_prints_counts_only(tmp_path):
    journal("init")
    run = journal("import", str(export_file(tmp_path)))
    assert run.returncode == 0, run.stderr
    assert "2 new trades" in run.stdout
    assert "1 position still open" in run.stdout
    for secret in SECRETS:
        assert secret not in run.stdout + run.stderr


def test_importing_the_same_file_again_says_so(tmp_path):
    journal("init")
    path = export_file(tmp_path)
    journal("import", str(path))
    run = journal("import", str(path))
    assert run.returncode == 0, run.stderr
    assert "already imported" in run.stdout


def test_an_unknown_layout_is_refused_with_a_clear_reason(tmp_path):
    journal("init")
    bad = tmp_path / "bank.csv"
    bad.write_text("Date,Description,Amount\n2026-10-01,SECRETSYM,4321.5\n")
    run = journal("import", str(bad))
    assert run.returncode == 2
    assert "not a TradingView" in run.stderr
    for secret in SECRETS:
        assert secret not in run.stdout + run.stderr


def test_an_import_and_a_migration_each_leave_a_backup_first(tmp_path, private_dirs):
    _, backups = private_dirs
    journal("init")
    journal("import", str(export_file(tmp_path)))
    assert len(list(backups.glob("journal-*-before-import.sqlite"))) == 1
    from conftest import FIXTURES

    journal("migrate", str(FIXTURES / "legacy_tracker.csv"))
    assert len(list(backups.glob("journal-*-before-migration.sqlite"))) == 1
