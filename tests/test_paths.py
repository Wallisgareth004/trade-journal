"""The journal's data may only live in a place that never syncs or gets pushed."""

from __future__ import annotations

from pathlib import Path

import pytest

from tradejournal import paths

HOME = Path.home()


@pytest.mark.parametrize(
    "where",
    [
        HOME / "Documents" / "trade-journal",
        HOME / "Desktop" / "journal",
        HOME / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "journal",
        HOME / "Library" / "CloudStorage" / "GoogleDrive-x" / "journal",
        HOME / "Documents" / "vault" / "Notes" / "Trading",
    ],
)
def test_a_folder_that_syncs_is_refused(where):
    with pytest.raises(paths.UnsafeLocation):
        paths.check_private(where)


def test_a_folder_inside_a_git_repo_is_refused(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(paths.UnsafeLocation, match="git"):
        paths.check_private(tmp_path / "repo" / "data" / "journal.sqlite")


def test_a_temp_folder_is_accepted(tmp_path):
    assert paths.check_private(tmp_path / "x") == (tmp_path / "x").resolve()


def test_with_no_override_the_data_lives_in_application_support(monkeypatch):
    monkeypatch.delenv("TRADE_JOURNAL_HOME")
    monkeypatch.delenv("TRADE_JOURNAL_BACKUPS")
    assert (
        paths.data_dir() == HOME / "Library" / "Application Support" / "trade-journal"
    )
    assert paths.backups_dir() == HOME / "Backups" / "trade-journal"


def test_an_override_into_documents_is_refused(monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL_HOME", str(HOME / "Documents" / "tj"))
    with pytest.raises(paths.UnsafeLocation):
        paths.data_dir()


def test_a_private_folder_is_created_owner_only(private_dirs):
    data, _ = private_dirs
    made = paths.ensure_private_dir(data / "screenshots")
    assert made.is_dir()
    assert made.stat().st_mode & 0o777 == 0o700
    assert data.stat().st_mode & 0o777 == 0o700
