#!/usr/bin/env python3
"""Render the home-screen app icons (assets/app-icons/) from the carrot favicon.

- icon-192.png, icon-512.png: the rounded tile, as in the browser tab
- maskable-512.png: full-bleed aubergine with the carrot inside Android's safe zone
- apple-touch-icon.png (180): full-bleed square; iOS rounds the corners itself

cairosvg needs Homebrew's libcairo, so run it like the og-cover target:
  DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib" uv run --with cairosvg python tools/make_app_icons.py
"""
import re
from pathlib import Path

import cairosvg

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'assets/favicons/2-carrot-on-aubergine.svg'
OUT = ROOT / 'assets/app-icons'


def full_bleed(svg, scale):
    """Square background with the carrot scaled about the center."""
    svg = re.sub(r'<rect [^>]*rx="7"[^>]*/>', '<rect width="32" height="32" fill="#3E2A52"/>', svg)
    return re.sub(r'(<g transform="rotate\(42 16 16\)">.*?</g>)',
        lambda m: f'<g transform="translate(16 16) scale({scale}) translate(-16 -16)">{m.group(1)}</g>', svg, flags=re.S)


def main():
    svg = SOURCE.read_text()
    OUT.mkdir(exist_ok=True)
    for name, size, source in (('icon-192.png', 192, svg), ('icon-512.png', 512, svg),
                               ('maskable-512.png', 512, full_bleed(svg, .72)),
                               ('apple-touch-icon.png', 180, full_bleed(svg, .9))):
        cairosvg.svg2png(bytestring=source.encode(), write_to=str(OUT / name), output_width=size, output_height=size)
        print('wrote', (OUT / name).relative_to(ROOT))


if __name__ == '__main__': main()
