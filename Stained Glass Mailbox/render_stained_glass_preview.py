#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path

import ezdxf
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import MultiLineString, Polygon
from shapely.ops import polygonize_full, unary_union

from inset_dxf_polygons import (
    dxf_unit_scale_to_inches,
    extract_lines,
    polygonize_lines,
)


Image.MAX_IMAGE_PIXELS = None


PALETTES = {
    "sampled": None,
    "flw-5": [
        ("cream", (240, 236, 197)),
        ("yellow", (219, 190, 48)),
        ("amber", (224, 142, 20)),
        ("green", (128, 154, 38)),
        ("brown", (104, 45, 22)),
    ],
    "flw-4": [
        ("cream", (240, 236, 197)),
        ("yellow", (219, 190, 48)),
        ("green", (128, 154, 38)),
        ("brown", (104, 45, 22)),
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a stained-glass color preview from DXF polygons and a reference image."
    )
    parser.add_argument("dxf", type=Path, help="Input DXF file.")
    parser.add_argument("reference", type=Path, help="Reference PNG/JPG image.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output PNG path. Defaults to '<dxf stem> stained glass.png'.",
    )
    parser.add_argument(
        "--ppi",
        type=int,
        default=220,
        help="Output pixels per inch. Default: 220.",
    )
    parser.add_argument(
        "--sample-width",
        type=int,
        default=2400,
        help="Width of the internal reference sampling image. Default: 2400.",
    )
    parser.add_argument(
        "--lead-width",
        type=float,
        default=0.045,
        help="Lead/came line width in inches. Default: 0.045.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=31337,
        help="Texture random seed. Default: 31337.",
    )
    parser.add_argument(
        "--palette",
        choices=sorted(PALETTES),
        default="sampled",
        help="Limit glass fills to a named palette. Default: sampled.",
    )
    parser.add_argument(
        "--flat-fill",
        action="store_true",
        help="Use exact flat fill colors instead of generated glass texture.",
    )
    return parser.parse_args()


def fmt(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def load_reference(path: Path, sample_width: int) -> Image.Image:
    image = Image.open(path).convert("RGB")
    if image.width > sample_width:
        sample_height = max(1, round(image.height * sample_width / image.width))
        image = image.resize((sample_width, sample_height), Image.Resampling.LANCZOS)
    return image


def art_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    arr = np.asarray(image)
    # The reference has a white page/background; this finds the outer artwork.
    non_white = np.any(arr < 245, axis=2)
    ys, xs = np.where(non_white)
    if len(xs) == 0:
        return (0, 0, image.width, image.height)
    return (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1))


def world_to_unit(
    point: tuple[float, float],
    bounds: tuple[float, float, float, float],
) -> tuple[float, float]:
    minx, miny, maxx, maxy = bounds
    x, y = point
    return (x - minx) / (maxx - minx), (maxy - y) / (maxy - miny)


def world_to_ref(
    point: tuple[float, float],
    bounds: tuple[float, float, float, float],
    bbox: tuple[int, int, int, int],
) -> tuple[float, float]:
    ux, uy = world_to_unit(point, bounds)
    x0, y0, x1, y1 = bbox
    return x0 + ux * (x1 - x0), y0 + uy * (y1 - y0)


def world_to_out(
    point: tuple[float, float],
    bounds: tuple[float, float, float, float],
    ppi: float,
    supersample: int,
) -> tuple[float, float]:
    ux, uy = world_to_unit(point, bounds)
    minx, miny, maxx, maxy = bounds
    return ux * (maxx - minx) * ppi * supersample, uy * (maxy - miny) * ppi * supersample


def polygon_rings(
    polygon: Polygon,
    mapper,
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    exterior = [mapper((x, y)) for x, y in polygon.exterior.coords]
    interiors = [[mapper((x, y)) for x, y in ring.coords] for ring in polygon.interiors]
    return exterior, interiors


def polygon_mask_for_ref(
    polygon: Polygon,
    bounds: tuple[float, float, float, float],
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    exterior, interiors = polygon_rings(
        polygon,
        lambda point: world_to_ref(point, bounds, bbox),
    )
    xs = [x for x, _ in exterior]
    ys = [y for _, y in exterior]
    pad = 3
    crop = (
        max(0, math.floor(min(xs)) - pad),
        max(0, math.floor(min(ys)) - pad),
        min(image_size[0], math.ceil(max(xs)) + pad),
        min(image_size[1], math.ceil(max(ys)) + pad),
    )
    mask = Image.new("L", (crop[2] - crop[0], crop[3] - crop[1]), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(x - crop[0], y - crop[1]) for x, y in exterior], fill=255)
    for ring in interiors:
        draw.polygon([(x - crop[0], y - crop[1]) for x, y in ring], fill=0)
    return mask, crop


def sample_polygon_color(
    polygon: Polygon,
    reference: Image.Image,
    bounds: tuple[float, float, float, float],
    bbox: tuple[int, int, int, int],
) -> tuple[int, int, int]:
    sampler = polygon.buffer(-0.025, join_style=2, mitre_limit=10.0)
    if sampler.is_empty or not isinstance(sampler, Polygon):
        sampler = polygon

    mask, crop = polygon_mask_for_ref(sampler, bounds, bbox, reference.size)
    if mask.width <= 0 or mask.height <= 0:
        return (238, 231, 174)

    crop_arr = np.asarray(reference.crop(crop)).astype(np.uint8)
    mask_arr = np.asarray(mask) > 0
    pixels = crop_arr[mask_arr]
    if len(pixels) == 0:
        return (238, 231, 174)

    pixels_f = pixels.astype(np.float32)
    luma = pixels_f[:, 0] * 0.2126 + pixels_f[:, 1] * 0.7152 + pixels_f[:, 2] * 0.0722
    maxc = pixels_f.max(axis=1)
    minc = pixels_f.min(axis=1)
    saturation = (maxc - minc) / np.maximum(maxc, 1)

    # Ignore black lead lines and white page hits, but keep dark reddish-brown glass.
    not_lead = (luma > 52) | ((pixels_f[:, 0] > 60) & (saturation > 0.35))
    not_page = ~((pixels_f[:, 0] > 248) & (pixels_f[:, 1] > 248) & (pixels_f[:, 2] > 248))
    filtered = pixels[not_lead & not_page]
    if len(filtered) < 8:
        filtered = pixels[not_page] if np.any(not_page) else pixels

    color = np.median(filtered.astype(np.float32), axis=0)

    # Nudge very pale pieces toward warm opalescent glass instead of paper white.
    color_f = color.astype(np.float32)
    luma_color = color_f[0] * 0.2126 + color_f[1] * 0.7152 + color_f[2] * 0.0722
    if luma_color > 225 and (color_f.max() - color_f.min()) < 26:
        color_f = color_f * 0.72 + np.array([250, 246, 203], dtype=np.float32) * 0.28

    return tuple(int(round(max(0, min(255, channel)))) for channel in color_f)


def nearest_palette_color(
    color: tuple[int, int, int],
    palette_name: str,
) -> tuple[int, int, int]:
    palette = PALETTES[palette_name]
    if palette is None:
        return color

    color_arr = np.array(color, dtype=np.float32)

    def distance(entry) -> float:
        _name, palette_color = entry
        palette_arr = np.array(palette_color, dtype=np.float32)
        # Weighted distance keeps pale cream pieces from drifting into yellow too often.
        diff = color_arr - palette_arr
        return float(diff[0] * diff[0] * 0.7 + diff[1] * diff[1] * 1.0 + diff[2] * diff[2] * 0.6)

    return min(palette, key=distance)[1]


def polygon_mask_for_output(
    polygon: Polygon,
    bounds: tuple[float, float, float, float],
    ppi: float,
    supersample: int,
    canvas_size: tuple[int, int],
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    exterior, interiors = polygon_rings(
        polygon,
        lambda point: world_to_out(point, bounds, ppi, supersample),
    )
    xs = [x for x, _ in exterior]
    ys = [y for _, y in exterior]
    pad = max(4, round(ppi * supersample * 0.04))
    crop = (
        max(0, math.floor(min(xs)) - pad),
        max(0, math.floor(min(ys)) - pad),
        min(canvas_size[0], math.ceil(max(xs)) + pad),
        min(canvas_size[1], math.ceil(max(ys)) + pad),
    )
    mask = Image.new("L", (crop[2] - crop[0], crop[3] - crop[1]), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(x - crop[0], y - crop[1]) for x, y in exterior], fill=255)
    for ring in interiors:
        draw.polygon([(x - crop[0], y - crop[1]) for x, y in ring], fill=0)
    return mask, crop


def textured_piece(
    color: tuple[int, int, int],
    size: tuple[int, int],
    seed: int,
) -> Image.Image:
    width, height = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]

    angle = rng.uniform(-0.55, 0.55)
    directional = xx * math.cos(angle) + yy * math.sin(angle)
    streak_1 = np.sin(directional * rng.uniform(0.035, 0.085) + rng.uniform(0, math.tau))
    streak_2 = np.sin((xx * -math.sin(angle) + yy * math.cos(angle)) * rng.uniform(0.012, 0.04))
    noise = rng.normal(0, 1, (height, width))

    variation = 1.0 + 0.055 * streak_1 + 0.035 * streak_2 + 0.038 * noise
    base = np.array(color, dtype=np.float32)
    arr = np.clip(base[None, None, :] * variation[:, :, None], 0, 255)

    # A warm diagonal glint gives the flat vector fill a stained-glass feel.
    glint = np.clip((xx / max(width, 1) * 0.7 + yy / max(height, 1) * 0.3), 0, 1)
    arr = arr * (0.94 + 0.08 * glint[:, :, None]) + np.array([255, 244, 190]) * 0.025

    image = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    return image.filter(ImageFilter.GaussianBlur(radius=0.35))


def draw_line_geom(draw: ImageDraw.ImageDraw, geom, mapper, color, width: int) -> None:
    if geom.geom_type == "LineString":
        points = [mapper((x, y)) for x, y in geom.coords]
        if len(points) >= 2:
            draw.line(points, fill=color, width=width, joint="curve")
    elif hasattr(geom, "geoms"):
        for part in geom.geoms:
            draw_line_geom(draw, part, mapper, color, width)


def render(
    dxf_path: Path,
    reference_path: Path,
    output_path: Path,
    ppi: int,
    sample_width: int,
    lead_width_in: float,
    seed: int,
    palette: str,
    flat_fill: bool,
) -> None:
    doc = ezdxf.readfile(dxf_path)
    unit_scale, unit_code = dxf_unit_scale_to_inches(doc)
    lines, unsupported = extract_lines(doc, unit_scale, 0, 5)
    if unsupported:
        print(f"Ignored unsupported entities: {dict(unsupported)}")

    bounds = MultiLineString(lines).bounds
    width_in = bounds[2] - bounds[0]
    height_in = bounds[3] - bounds[1]
    polygons, report = polygonize_lines(lines, 1e-8)
    _polys, _cut_edges, dangles, _invalid_rings = report

    reference = load_reference(reference_path, sample_width)
    bbox = art_bbox(reference)

    supersample = 2
    canvas_size = (round(width_in * ppi * supersample), round(height_in * ppi * supersample))
    canvas = Image.new("RGB", canvas_size, (246, 243, 218))

    for index, polygon in enumerate(polygons, start=1):
        color = sample_polygon_color(polygon, reference, bounds, bbox)
        color = nearest_palette_color(color, palette)
        mask, crop = polygon_mask_for_output(polygon, bounds, ppi, supersample, canvas_size)
        texture = Image.new("RGB", mask.size, color) if flat_fill else textured_piece(color, mask.size, seed + index * 7919)
        canvas.paste(texture, crop[:2], mask)

    line_layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    line_draw = ImageDraw.Draw(line_layer)
    line_width = max(3, round(lead_width_in * ppi * supersample))
    shadow_width = max(line_width + 2, round(line_width * 1.22))
    mapper = lambda point: world_to_out(point, bounds, ppi, supersample)

    for line in lines:
        draw_line_geom(line_draw, line, mapper, (24, 22, 15, 245), shadow_width)
    for line in lines:
        draw_line_geom(line_draw, line, mapper, (8, 8, 6, 245), line_width)
    # Subtle inner highlight on the leading keeps the black lines from looking flat.
    highlight_width = max(1, round(line_width * 0.18))
    for line in lines:
        draw_line_geom(line_draw, line, mapper, (60, 46, 24, 80), highlight_width)

    canvas = Image.alpha_composite(canvas.convert("RGBA"), line_layer).convert("RGB")
    final_size = (round(width_in * ppi), round(height_in * ppi))
    canvas = canvas.resize(final_size, Image.Resampling.LANCZOS)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(
        output_path,
        "PNG",
        dpi=(ppi, ppi),
        optimize=True,
    )

    print(f"Read DXF: {dxf_path}")
    print(f"Reference: {reference_path}")
    print(f"Output: {output_path}")
    print(f"DXF $INSUNITS: {unit_code} (scale to inches: {fmt(unit_scale)})")
    print(f"Original polygons rendered: {len(polygons)}")
    print(f"Reference sample image: {reference.width}px x {reference.height}px")
    print(f"Reference artwork bbox: {bbox}")
    print(f"Palette: {palette}")
    print(f"Flat fill: {flat_fill}")
    print(f"Dangles still drawn as linework: {len(dangles.geoms)}")
    print(f"PNG size: {final_size[0]}px x {final_size[1]}px at {ppi}ppi")
    print(f"Physical size: {fmt(width_in)}in x {fmt(height_in)}in")


def main() -> int:
    args = parse_args()
    dxf_path = args.dxf.expanduser().resolve()
    reference_path = args.reference.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else dxf_path.with_name(f"{dxf_path.stem} stained glass.png")
    )
    render(
        dxf_path=dxf_path,
        reference_path=reference_path,
        output_path=output_path,
        ppi=args.ppi,
        sample_width=args.sample_width,
        lead_width_in=args.lead_width,
        seed=args.seed,
        palette=args.palette,
        flat_fill=args.flat_fill,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
