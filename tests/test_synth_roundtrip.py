"""Sixty made-up bracket trades go through the importer and come out with the
R the generator knows they made (target = +planned R:R, stop = -1R, a manual
exit = its distance over the stop's)."""

from __future__ import annotations

import pytest

from tradejournal import settings, synth, tvimport


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_generated_trades_import_with_their_known_r(conn, tmp_path, seed):
    specs, truth = synth.generate(60, seed=seed)
    path = tmp_path / "h.csv"
    path.write_bytes(synth.to_csv(synth.HISTORY_HEADER, synth.history_rows(specs)))
    result = tvimport.import_file(conn, path, settings.parse(""))
    assert result.new_trades == 60 and result.open_positions == 0
    got = conn.execute(
        "SELECT direction, result_r, planned_rr, exit_reason FROM trades ORDER BY entry_time"
    ).fetchall()
    for row, want in zip(got, truth, strict=True):
        assert row["direction"] == want["direction"]
        assert row["exit_reason"] == want["exit_kind"]
        assert row["result_r"] == pytest.approx(want["r"], abs=1e-9)
        assert row["planned_rr"] == pytest.approx(want["planned_rr"], abs=1e-9)


def test_the_generator_makes_wins_losses_and_manual_exits():
    _, truth = synth.generate(60, seed=1)
    kinds = {t["exit_kind"] for t in truth}
    assert kinds == {"target", "stop", "manual"}
    assert any(t["r"] > 0 for t in truth) and any(t["r"] < 0 for t in truth)


def test_the_same_seed_gives_the_same_trades():
    assert synth.generate(10, seed=7)[1] == synth.generate(10, seed=7)[1]
