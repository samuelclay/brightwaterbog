#!/usr/bin/env python3
"""Generates public/bog-motifs.svg — the sprite of bog residents.

The scatter itself is not baked here: any fixed tile eventually repeats, and the
repeat is visible. src/islands/bog-scatter.ts places these symbols across the
whole page at runtime with blue-noise spacing, so no two areas match.

Animal silhouettes come from phylopic.org (see sources/CREDITS.md); mountain
laurel and blueberry are drawn here because PhyloPic has no Kalmia and its
Vaccinium loses the berries at this size. Run from website/:

    python3 scripts/bog-pattern/gen.py
"""

import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
OUT = HERE.parent.parent / "public" / "bog-motifs.svg"

GREEN, BROWN, SLATE, RUST, ROSE, BLUE = (
    "#6b7a64", "#907e5e", "#576057", "#a05540", "#c25682", "#2f5bd0",
)


def phylopic(name, color, op=None):
    """Load a potrace silhouette, recolor it, and report its viewBox size.

    Pass op=None when the caller supplies opacity at group level (as the beaver
    does) — setting it in both places multiplies, and the motif vanishes.
    """
    raw = (SOURCES / f"{name}.svg").read_text()
    vb = re.search(r'viewBox="([\d. ]+)"', raw).group(1).split()
    group = re.search(r"(<g[^>]*>.*</g>)", raw, re.S).group(1)
    fill = f"fill='{color}'" if op is None else f"fill='{color}' fill-opacity='{op}'"
    return group.replace('fill="#000000"', fill), float(vb[2]), float(vb[3])


def beaver(color=BROWN, op=0.17):
    """The tail is the whole point, and the traced one is thin — widen it.

    A paddle ellipse matched to the traced tail's axis (measured: 1130,415 →
    1535,640 in a 1536x684 viewBox) doubles its width without lengthening it.
    Group-level opacity keeps the overlap from darkening.
    """
    group, w, h = phylopic("beaver", color)  # opacity comes from the wrapper
    paddle = "<ellipse cx='1332' cy='527' rx='240' ry='85' transform='rotate(29 1332 527)'/>"
    return f"<g opacity='{op}' fill='{color}'>{group}{paddle}</g>", w, h


def laurel_flower(cx, cy, r, color, op):
    """Kalmia's signature: a shallow five-lobed cup with ten radiating stamens."""
    lobes = [-90, -18, 54, 126, 198]
    notch = r * 0.66
    pts = []
    for a in lobes:
        na = a - 36
        pts.append(("N", cx + notch * math.cos(math.radians(na)), cy + notch * math.sin(math.radians(na))))
        pts.append(("L", cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))))
    d = f"M {pts[0][1]:.2f} {pts[0][2]:.2f} "
    for i in range(len(pts)):
        kind, x, y = pts[(i + 1) % len(pts)]
        d += f"Q {x:.2f} {y:.2f} " if kind == "L" else f"{x:.2f} {y:.2f} "
    out = [f"<path d='{d}Z' fill='{color}' fill-opacity='{op}'/>"]
    for k in range(10):
        a = math.radians(k * 36 - 90)
        x1, y1 = cx + r * 0.12 * math.cos(a), cy + r * 0.12 * math.sin(a)
        x2, y2 = cx + r * 0.74 * math.cos(a), cy + r * 0.74 * math.sin(a)
        out.append(
            f"<path d='M {x1:.2f} {y1:.2f} L {x2:.2f} {y2:.2f}' stroke='{color}' "
            f"stroke-opacity='{op * 1.5:.3f}' stroke-width='{r * 0.07:.2f}' fill='none'/>"
        )
        out.append(f"<circle cx='{x2:.2f}' cy='{y2:.2f}' r='{r * 0.09:.2f}' fill='{color}' fill-opacity='{op * 2:.3f}'/>")
    return "".join(out)


def laurel_bud(cx, cy, r, color, op):
    out = [f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='{color}' fill-opacity='{op}'/>"]
    for k in range(5):
        a = math.radians(k * 72 - 90)
        out.append(
            f"<path d='M {cx:.2f} {cy:.2f} L {cx + r * math.cos(a):.2f} {cy + r * math.sin(a):.2f}' "
            f"stroke='{color}' stroke-opacity='{op * 1.4:.3f}' stroke-width='{r * 0.16:.2f}'/>"
        )
    return "".join(out)


def laurel(color=ROSE, green=GREEN, op=0.16):
    """A six-flower corymb with two fluted buds."""
    flowers = [(11, 13, 7.0), (26, 11, 6.4), (38, 16, 6.0), (18, 24, 6.6), (32, 26, 5.8), (7, 27, 5.2)]
    stems = "M11 20 q1 9 2 14 M26 18 q0 9 -1 14 M38 22 q-2 8 -5 12 M18 31 q0 4 0 6 M32 32 q-1 3 -2 5"
    parts = [
        f"<path d='{stems}' stroke='{green}' stroke-opacity='{op * 0.85:.3f}' "
        f"stroke-width='0.9' fill='none' stroke-linecap='round'/>"
    ]
    parts += [laurel_flower(cx, cy, r, color, op) for cx, cy, r in flowers]
    parts.append(laurel_bud(43, 27, 2.6, color, op))
    parts.append(laurel_bud(2.5, 18, 2.2, color, op))
    return "".join(parts), 46, 40


def blueberry(blue=BLUE, green=GREEN, op=0.16):
    """Berries carry nearly double the leaves' alpha so the blue still reads small."""
    parts = [
        f"<path d='M3 3 q10 3 15 11' stroke='{green}' stroke-opacity='{op:.3f}' "
        f"stroke-width='1.3' fill='none' stroke-linecap='round'/>",
        f"<path d='M6 4 q6 -6 11 -3 q-4 6 -11 3z' fill='{green}' fill-opacity='{op:.3f}'/>",
        f"<path d='M13 9 q7 -3 10 2 q-6 3 -10 -2z' fill='{green}' fill-opacity='{op * 0.9:.3f}'/>",
        f"<path d='M12 9 q0 3 0 5 M17 12 q1 3 1 5 M14 16 q-1 3 -1 5' stroke='{green}' "
        f"stroke-opacity='{op * 0.8:.3f}' stroke-width='0.8' fill='none'/>",
    ]
    for cx, cy, r in [(11.5, 18.5, 4.0), (19, 20, 3.5), (14, 25.5, 3.2)]:
        parts.append(f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='{blue}' fill-opacity='{op * 1.9:.3f}'/>")
        for k in range(5):  # calyx crown
            a = math.radians(54 + k * 18)
            parts.append(
                f"<path d='M {cx + r * 0.45 * math.cos(a):.2f} {cy + r * 0.45 * math.sin(a):.2f} "
                f"L {cx + r * 0.95 * math.cos(a):.2f} {cy + r * 0.95 * math.sin(a):.2f}' "
                f"stroke='{blue}' stroke-opacity='{op * 1.1:.3f}' stroke-width='0.5'/>"
            )
    return "".join(parts), 30, 30


# name -> (renderer, height in tile px)
CAST = {
    "heron": (lambda: phylopic("heron", GREEN, 0.17), 52),
    "beaver": (beaver, 40),
    "squirrel": (lambda: phylopic("squirrel", BROWN, 0.16), 44),
    "chipmunk": (lambda: phylopic("chipmunk", BROWN, 0.16), 28),
    "finch": (lambda: phylopic("finch", GREEN, 0.18), 30),
    "eagle": (lambda: phylopic("eagle", SLATE, 0.16), 44),
    "sundew": (lambda: phylopic("sundew", RUST, 0.15), 34),
    "pitcher": (lambda: phylopic("pitcher-plant", RUST, 0.16), 40),
    "laurel": (laurel, 38),
    "blueberry": (blueberry, 32),
}

def main():
    """Emit one <symbol> per motif; the island scales them via width/height."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- Generated by scripts/bog-pattern/gen.py - do not edit by hand. -->",
        "<svg xmlns='http://www.w3.org/2000/svg' style='display:none'>",
    ]
    for name, (render, target) in CAST.items():
        inner, width, height = render()
        parts.append(
            f"<symbol id='bog-{name}' viewBox='0 0 {width:g} {height:g}' "
            f"data-height='{target}'>{inner}</symbol>"
        )
    parts.append("</svg>")
    OUT.write_text("\n".join(parts))
    print(f"bog-motifs: {len(CAST)} symbols -> {OUT.relative_to(OUT.parents[2])}")


if __name__ == "__main__":
    main()
