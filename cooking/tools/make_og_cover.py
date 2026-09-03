#!/usr/bin/env python3
"""Build images/og-cover.jpg — the link-preview card for the site root.

A mosaic of six recipe photos behind an aubergine plaque carrying the wordmark,
sized 1200x630 for WhatsApp / iMessage / Slack / Twitter previews.

    python3 tools/make_og_cover.py

Fonts (Fraunces, Karla) are pulled from Google Fonts and cached in tools/.fonts.
"""

import io
import os
import re
import urllib.request

import cairosvg
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERO = os.path.join(ROOT, "images", "hero")
FONT_DIR = os.path.join(ROOT, "tools", ".fonts")
OUT = os.path.join(ROOT, "images", "og-cover.jpg")

W, H = 1200, 630

AUBERGINE = (62, 42, 82)
CARROT = (232, 117, 46)
CREAM = (243, 237, 228)
CREAM_SOFT = (212, 201, 224)

# Six photos that read well as small tiles: strong color, top-down framing,
# no recipe-card text baked into the crop.
TILES = ["030", "049", "023", "034", "048", "056"]

CARROT_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <g transform="rotate(42 16 16)">
    <path d="M16 9.5 C19.2 9.5 21 12.2 20.3 17.2 C19.7 21.7 17.9 26.2 16 27.6 C14.1 26.2 12.3 21.7 11.7 17.2 C11 12.2 12.8 9.5 16 9.5 Z" fill="#E8752E"/>
    <path d="M16 9.5 C19.2 9.5 21 12.2 20.3 17.2 C19.8 20.5 18.7 23.9 17.6 25.9 C16.7 23.4 16.2 17.6 16.4 12.4 C16.4 11.2 16.3 10.1 16 9.5 Z" fill="#C75A1A"/>
    <path d="M15.8 9 C14.9 6.3 13.2 4.7 10.9 4.2 C12.3 6.5 13.6 8 15 9 Z" fill="#7DA34A"/>
    <path d="M16.2 9 C16.4 5.9 17.8 3.9 20.2 3.2 C19.5 5.9 18.3 7.8 16.9 9 Z" fill="#9CC25E"/>
    <path d="M16.3 9.3 C18.8 8.2 21.2 8.3 23 9.6 C20.8 10.5 18.5 10.4 16.7 9.9 Z" fill="#7DA34A"/>
  </g>
</svg>"""


def google_fonts(spec):
    """Return {(family, weight, italic): ttf_path} for a css2 family spec."""
    os.makedirs(FONT_DIR, exist_ok=True)
    url = "https://fonts.googleapis.com/css2?" + spec
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    css = urllib.request.urlopen(req).read().decode()
    faces = {}
    for blk in re.findall(r"@font-face\s*\{[^}]+\}", css):
        fam = re.search(r"font-family:\s*'([^']+)'", blk).group(1)
        wt = int(re.search(r"font-weight:\s*(\d+)", blk).group(1))
        ital = "italic" in re.search(r"font-style:\s*(\w+)", blk).group(1)
        src = re.search(r"url\((https://[^)]+)\)", blk).group(1)
        path = os.path.join(FONT_DIR, f"{fam}-{wt}{'i' if ital else ''}.ttf")
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(urllib.request.urlopen(src).read())
        faces[(fam, wt, ital)] = path
    return faces


def mosaic(cell_w, cell_h, cols=3, rows=2):
    """Center-cropped grid of hero photos filling the full card."""
    out = Image.new("RGB", (cell_w * cols, cell_h * rows))
    for i, name in enumerate(TILES[: cols * rows]):
        im = Image.open(os.path.join(HERO, f"{name}.jpg")).convert("RGB")
        scale = max(cell_w / im.width, cell_h / im.height)
        im = im.resize((round(im.width * scale), round(im.height * scale)),
                       Image.LANCZOS)
        left = (im.width - cell_w) // 2
        top = (im.height - cell_h) // 2
        out.paste(im.crop((left, top, left + cell_w, top + cell_h)),
                  ((i % cols) * cell_w, (i // cols) * cell_h))
    return out


def svg_image(svg, px):
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=px,
                           output_height=px)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def run_width(draw, runs):
    return sum(draw.textlength(t, font=f) for t, f, _ in runs)


def draw_runs(draw, x, y, runs):
    for text, font, fill in runs:
        draw.text((x, y), text, font=font, fill=fill)
        x += draw.textlength(text, font=font)


def main():
    faces = google_fonts(
        "family=Fraunces:ital,opsz,wght@0,9..144,600;1,9..144,600"
        "&family=Karla:wght@500;600&display=swap")
    title_up = ImageFont.truetype(faces[("Fraunces", 600, False)], 70)
    title_it = ImageFont.truetype(faces[("Fraunces", 600, True)], 70)
    sub = ImageFont.truetype(faces[("Karla", 500, False)], 28)

    card = mosaic(W // 3, H // 2)

    # Keep the food appetizing under the wash, then knock the whole mosaic
    # back so the plaque reads first.
    card = ImageEnhance.Color(card).enhance(1.14)
    card = ImageEnhance.Contrast(card).enhance(1.06)
    card = Image.blend(card, Image.new("RGB", (W, H), (24, 14, 34)), 0.24)

    card = card.convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Plaque
    px0, px1 = 150, W - 150
    py0, py1 = 168, 462
    d.rounded_rectangle((px0 + 5, py0 + 9, px1 + 5, py1 + 9), radius=24,
                        fill=(20, 10, 30, 90))          # drop shadow
    d.rounded_rectangle((px0, py0, px1, py1), radius=24,
                        fill=AUBERGINE + (247,))
    d.rounded_rectangle((px0 + 9, py0 + 9, px1 - 9, py1 - 9), radius=17,
                        outline=CARROT + (90,), width=2)

    card.alpha_composite(layer)
    d = ImageDraw.Draw(card)

    # Carrot mark
    mark = svg_image(CARROT_SVG, 62)
    card.alpha_composite(mark, (W // 2 - mark.width // 2, py0 + 22))

    # Wordmark: "The Cooking Book", middle word in carrot italic
    runs = [("The ", title_up, CREAM), ("Cooking", title_it, CARROT),
            (" Book", title_up, CREAM)]
    ty = py0 + 100
    draw_runs(d, (W - run_width(d, runs)) / 2, ty, runs)

    # Hairline + subtitle
    ry = ty + 108
    d.line((W // 2 - 46, ry, W // 2 + 46, ry), fill=CARROT, width=2)
    caption = "131 Purple Carrot cards, digitized, plus the recipes saved from NYT Cooking"
    d.text((W // 2, ry + 22), caption, font=sub, fill=CREAM_SOFT, anchor="ma")

    card.convert("RGB").save(OUT, quality=86, optimize=True,
                             progressive=True, subsampling=1)
    print(f"{OUT}  {os.path.getsize(OUT) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
