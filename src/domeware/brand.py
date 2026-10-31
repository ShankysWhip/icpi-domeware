"""ICPI's logo for domeware's screens: the letters in dark ink for the light screens (the original sits on black) and
the orange spark. web/icpi.svg is the logo itself (the HUD and the report use it); draw_tk() draws the same strokes on
a Tk canvas for dw app."""

from __future__ import annotations

import math
from pathlib import Path

INK = "#121b26"
ORANGE = "#f39200"
SVG = Path(__file__).parent / "web" / "icpi.svg"
VIEW_W, VIEW_H = 80, 38  # the SVG's viewBox


def svg() -> str:
    return SVG.read_text(encoding="utf-8").strip()


def draw_tk(canvas, height: float = 26, x0: float = 0, y0: float = 0) -> float:
    """Draw the logo on a Tk canvas, `height` pixels tall; returns its width. Same strokes as web/icpi.svg."""
    k = height / VIEW_H

    def pts(*xy):
        return [v * k + (x0 if i % 2 == 0 else y0) for i, v in enumerate(xy)]

    def line(xy, width, fill):
        canvas.create_line(*pts(*xy), width=max(1.0, width * k), fill=fill, capstyle="round", joinstyle="round",
                           smooth=False)

    def arc(cx, cy, r, a0, a1, step):
        out = []
        for a in range(a0, a1 + (1 if step > 0 else -1), step):
            out += [cx + r * math.cos(math.radians(a)), cy - r * math.sin(math.radians(a))]
        return out

    line((10, 17, 10, 35), 4.4, INK)
    line(arc(29, 26, 9, 50, 310, 10), 4.4, INK)  # C: from the upper right round the left to the lower right
    line((46, 35, 46, 17, 52, 17, *arc(52, 22, 5, 90, -90, -15), 46, 27), 4.4, INK)  # P
    line((67, 17, 67, 35), 4.4, INK)
    for seg in ((41, 1.5, 41, 6.5), (41, 10.5, 41, 15.5), (34, 8.5, 39, 8.5), (43, 8.5, 76, 8.5), (36.5, 4, 39.4, 6.9),
                (42.6, 10.1, 45.5, 13), (36.5, 13, 39.4, 10.1), (42.6, 6.9, 45.5, 4)):
        line(seg, 2, ORANGE)
    return VIEW_W * k
