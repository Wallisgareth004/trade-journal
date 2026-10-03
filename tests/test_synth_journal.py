"""A made-up journal with deliberate rule breaks and skipped checklists. The
generator decides each break itself and says what grade it expects; the
journal must reach the same grade from the data alone."""

from __future__ import annotations

from collections import Counter

import pytest

from test_rulebook import BASE, with_line
from tradejournal import rulebook, settings, synth, tvimport

V0 = with_line("placeholder", "placeholder = false", BASE)


def build(conn, tmp_path, n=80, seed=3):
    rulebook.record(conn, rulebook.parse(V0))
    data = synth.journal(n, seed=seed)
    synth.insert_checklists(conn, data.checklists)
    path = tmp_path / "h.csv"
    path.write_bytes(synth.to_csv(synth.HISTORY_HEADER, synth.history_rows(data.specs)))
    tvimport.import_file(conn, path, settings.parse(""))
    return data


@pytest.mark.parametrize("seed", [3, 8, 21, 34])
def test_every_trade_gets_the_grade_the_generator_expects(conn, tmp_path, seed):
    data = build(conn, tmp_path, n=120, seed=seed)
    got = [
        r["grade"] for r in conn.execute("SELECT grade FROM trades ORDER BY entry_time")
    ]
    want = [t["expected_grade"] for t in data.truth]
    assert got == want


def test_the_generator_really_breaks_each_rule(tmp_path):
    data = synth.journal(80, seed=3)
    breaks = Counter(b for t in data.truth for b in t["breaks"])
    for kind in (
        "no checklist",
        "taken anyway",
        "planned R:R",
        "stop after entry",
        "max a day",
        "after losses",
    ):
        assert breaks[kind] > 0, kind


def test_skipped_checklists_never_attach_to_a_trade(conn, tmp_path):
    data = build(conn, tmp_path)
    skipped = [c for c in data.checklists if c["skipped"]]
    assert skipped
    unmatched = conn.execute(
        "SELECT count(*) FROM checklists WHERE matched_trade_id IS NULL"
    ).fetchone()[0]
    assert unmatched == len(skipped)
