"""Every test runs on made-up data in temp folders.

Nothing here reads or writes a real journal: the data and backup folders point
into pytest's tmp_path for every test, and a guard fails the run if they don't.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def private_dirs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    monkeypatch.setenv("TRADE_JOURNAL_HOME", str(data))
    monkeypatch.setenv("TRADE_JOURNAL_BACKUPS", str(backups))
    monkeypatch.delenv("TRADE_JOURNAL_NOW", raising=False)
    real = Path.home() / "Library" / "Application Support" / "trade-journal"
    assert not str(data).startswith(str(real))
    return data, backups


@pytest.fixture
def conn():
    from tradejournal import db

    c = db.connect()
    yield c
    c.close()


def env_for_cli() -> dict:
    """The environment a CLI subprocess gets: the same temp folders."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def rows(c: sqlite3.Connection, sql: str, *args) -> list:
    return [tuple(r) for r in c.execute(sql, args).fetchall()]
