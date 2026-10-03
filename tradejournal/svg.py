"""Charts as plain SVG, drawn here: nothing fetched from the internet and no
scripts, so the page's content-security policy can stay strict. Colours come
from classes in app.css, so light and dark mode both work."""

from __future__ import annotations

import html

PAD_L, PAD_R, PAD_T, PAD_B = 44, 10, 12, 24


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _open(width: int, height: int, title: str) -> str:
    t = _e(title)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="chart"'
        f' role="img" aria-label="{t}"><title>{t}</title>'
    )


def _r(v: float) -> str:
    return f"{v:+.1f}R" if v else "0R"


def equity(
    curve: list[float], width: int = 480, height: int = 220, label: str | None = None
) -> str:
    """Cumulative R by trade, with each fall from a peak shaded."""
    if len(curve) < 2:
        return (
            _open(width, height, label or "Equity curve in R")
            + f'<text x="{width / 2:.0f}" y="{height / 2:.0f}" class="empty" text-anchor="middle">'
            "No closed trades yet</text></svg>"
        )
    lo, hi = min(min(curve), 0.0), max(max(curve), 0.0)
    if hi == lo:
        hi = lo + 1
    span_x = (width - PAD_L - PAD_R) / (len(curve) - 1)
    span_y = height - PAD_T - PAD_B

    def x(i: int) -> float:
        return PAD_L + i * span_x

    def y(v: float) -> float:
        return PAD_T + (hi - v) / (hi - lo) * span_y

    peaks, peak = [], 0.0
    for v in curve:
        peak = max(peak, v)
        peaks.append(peak)
    drawdown = max(p - v for p, v in zip(peaks, curve))
    title = (
        label
        or f"Equity curve in R over {len(curve) - 1} trades; deepest drawdown {drawdown:.1f}R"
    )
    parts = [_open(width, height, title)]
    parts.append(
        f'<line class="zero" x1="{PAD_L}" x2="{width - PAD_R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>'
    )
    if drawdown > 1e-12:
        top = " ".join(f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(peaks))
        bottom = " ".join(
            f"{x(i):.1f},{y(v):.1f}" for i, v in reversed(list(enumerate(curve)))
        )
        parts.append(f'<polygon class="dd" points="{top} {bottom}"/>')
    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(curve))
    parts.append(f'<polyline class="eq" points="{line}"/>')
    for v in sorted({hi, 0.0, lo}):
        parts.append(
            f'<text class="axis" x="{PAD_L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{_r(v)}</text>'
        )
    parts.append(f'<text class="axis" x="{PAD_L}" y="{height - 6}">trade 1</text>')
    parts.append(
        f'<text class="axis" x="{width - PAD_R}" y="{height - 6}" text-anchor="end">trade {len(curve) - 1}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def histogram(
    bins: list[tuple[float, int]],
    width: int = 480,
    height: int = 170,
    step: float = 0.5,
) -> str:
    """How many trades ended in each half-R band; losses left of zero, wins right."""
    if not bins:
        return (
            _open(width, height, "R distribution")
            + f'<text x="{width / 2:.0f}" y="{height / 2:.0f}" class="empty" text-anchor="middle">'
            "No closed trades yet</text></svg>"
        )
    counts = dict(bins)
    first, last = min(counts), max(counts)
    lefts = []
    v = first
    while v <= last + 1e-9:
        lefts.append(round(v, 6) + 0.0)
        v += step
    top = max(counts.values())
    slot = (width - PAD_L - PAD_R) / len(lefts)
    span_y = height - PAD_T - PAD_B
    title = f"R distribution in {step:g}R bands, {sum(counts.values())} trades"
    parts = [_open(width, height, title)]
    for i, left in enumerate(lefts):
        n = counts.get(left, 0)
        h = span_y * n / top
        cls = "bar win" if left >= 0 else "bar loss"
        bx = PAD_L + i * slot + 1
        parts.append(
            f'<rect class="{cls}" x="{bx:.1f}" y="{PAD_T + span_y - h:.1f}" width="{max(slot - 2, 1):.1f}"'
            f' height="{h:.1f}"><title>{_e(f"{left:+g}R to {left + step:+g}R: {n}")}</title></rect>'
        )
        if abs(left - round(left)) < 1e-9:
            parts.append(
                f'<text class="axis" x="{PAD_L + i * slot:.1f}" y="{height - 6}" text-anchor="middle">{_r(left)}</text>'
            )
    parts.append(
        f'<text class="axis" x="{PAD_L - 6}" y="{PAD_T + 10}" text-anchor="end">{top}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)
