"""The charts are SVG the journal draws itself: nothing fetched, no scripts."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from tradejournal import svg

NS = "{http://www.w3.org/2000/svg}"


def parse(text):
    return ET.fromstring(text)


def points(el) -> list[tuple[float, float]]:
    return [tuple(map(float, p.split(","))) for p in el.get("points").split()]


def test_the_equity_curve_draws_every_point_inside_its_box():
    root = parse(svg.equity([0.0, 1.0, 0.5, 2.5], width=400, height=200))
    (line,) = root.iter(f"{NS}polyline")
    pts = points(line)
    assert len(pts) == 4
    assert all(0 <= x <= 400 and 0 <= y <= 200 for x, y in pts)
    xs = [x for x, _ in pts]
    assert xs == sorted(xs) and xs[0] < xs[-1]
    ys = [y for _, y in pts]
    assert ys[3] < ys[1] < ys[2] < ys[0]  # higher R is higher up (smaller y)


def test_a_drawdown_is_shaded_and_none_when_the_curve_only_rises():
    falling = parse(svg.equity([0.0, 2.0, 0.5, 1.0], width=400, height=200))
    assert [p.get("class") for p in falling.iter(f"{NS}polygon")] == ["dd"]
    rising = parse(svg.equity([0.0, 1.0, 2.0], width=400, height=200))
    assert list(rising.iter(f"{NS}polygon")) == []


def test_the_chart_says_what_it_shows_for_screen_readers():
    root = parse(svg.equity([0.0, 1.0, -1.0], width=400, height=200))
    assert root.get("role") == "img"
    assert "R" in root.find(f"{NS}title").text


def test_with_no_trades_the_chart_says_so():
    root = parse(svg.equity([0.0], width=400, height=200))
    assert "No closed trades yet" in "".join(root.itertext())


def test_the_histogram_has_a_bar_per_half_r_bin_including_empty_ones():
    root = parse(svg.histogram([(-1.0, 2), (-0.5, 1), (0.5, 3)], width=400, height=160))
    bars = [
        r for r in root.iter(f"{NS}rect") if r.get("class") in ("bar win", "bar loss")
    ]
    assert len(bars) == 4  # -1.0, -0.5, 0.0 (empty), 0.5
    heights = [float(b.get("height")) for b in bars]
    assert heights[2] == 0 and heights[3] > heights[0] > heights[1]
    assert [b.get("class") for b in bars] == [
        "bar loss",
        "bar loss",
        "bar win",
        "bar win",
    ]


def test_no_markup_can_be_injected_through_a_label():
    text = svg.equity([0.0, 1.0], width=400, height=200, label="<script>x</script>")
    assert "<script>" not in text
    assert not re.search(r"\son\w+=", text)
