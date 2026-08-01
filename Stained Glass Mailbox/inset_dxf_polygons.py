#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

import ezdxf
from ezdxf.math import Vec2, bulge_to_arc
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import polygonize, polygonize_full, snap, unary_union
from shapely.validation import make_valid


COMMON_DXF_UNITS_TO_INCHES = {
    0: 1.0,  # Unitless: assume the drawing was authored in inches.
    1: 1.0,  # Inches
    2: 12.0,  # Feet
    4: 1.0 / 25.4,  # Millimeters
    5: 1.0 / 2.54,  # Centimeters
    6: 39.37007874015748,  # Meters
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find closed regions in DXF linework, inset each region, and export the "
            "result as a physically sized SVG."
        )
    )
    parser.add_argument("input", type=Path, help="Input DXF file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output SVG path. Defaults to '<input stem> inset.svg'.",
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=0.01,
        help="Inward offset in inches. Default: 0.01.",
    )
    parser.add_argument(
        "--snap-tolerance",
        type=float,
        default=0.0,
        help="Round input coordinates to this grid size in inches. Default: 0.",
    )
    parser.add_argument(
        "--topology-snap-tolerance",
        type=float,
        default=1e-6,
        help=(
            "Join near-touching endpoint-to-line geometry before polygonizing, "
            "in inches. Default: 1e-6."
        ),
    )
    parser.add_argument(
        "--min-area",
        type=float,
        default=1e-8,
        help="Discard source polygons smaller than this many square inches. Default: 1e-8.",
    )
    parser.add_argument(
        "--min-inset-area",
        type=float,
        default=1e-8,
        help="Discard inset polygons smaller than this many square inches. Default: 1e-8.",
    )
    parser.add_argument(
        "--arc-angle",
        type=float,
        default=5.0,
        help="Maximum degrees per generated segment when flattening arcs. Default: 5.",
    )
    parser.add_argument(
        "--stroke-width",
        type=float,
        default=0.003,
        help="SVG stroke width in inches. Default: 0.003.",
    )
    return parser.parse_args()


def fmt(value: float) -> str:
    if abs(value) < 5e-10:
        value = 0.0
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def dxf_unit_scale_to_inches(doc: ezdxf.EzDxfDocument) -> tuple[float, int]:
    unit_code = int(doc.header.get("$INSUNITS", 0) or 0)
    return COMMON_DXF_UNITS_TO_INCHES.get(unit_code, 1.0), unit_code


def snap_coord(value: float, tolerance: float) -> float:
    if tolerance <= 0:
        return value
    return round(value / tolerance) * tolerance


def snap_point(point: tuple[float, float], tolerance: float) -> tuple[float, float]:
    return snap_coord(point[0], tolerance), snap_coord(point[1], tolerance)


def scaled_point(point: Iterable[float], scale: float) -> tuple[float, float]:
    x, y = point[0], point[1]  # type: ignore[index]
    return float(x) * scale, float(y) * scale


def segment_count_for_angle(angle_radians: float, max_angle_degrees: float) -> int:
    max_angle = math.radians(max(max_angle_degrees, 0.25))
    return max(2, math.ceil(abs(angle_radians) / max_angle))


def arc_points(
    center: Vec2,
    radius: float,
    start_angle: float,
    end_angle: float,
    max_angle_degrees: float,
) -> list[tuple[float, float]]:
    sweep = end_angle - start_angle
    while sweep <= 0:
        sweep += math.tau

    steps = segment_count_for_angle(sweep, max_angle_degrees)
    return [
        (
            center.x + math.cos(start_angle + sweep * i / steps) * radius,
            center.y + math.sin(start_angle + sweep * i / steps) * radius,
        )
        for i in range(steps + 1)
    ]


def bulge_segment_points(
    start: tuple[float, float],
    end: tuple[float, float],
    bulge: float,
    max_angle_degrees: float,
) -> list[tuple[float, float]]:
    if abs(bulge) < 1e-12:
        return [start, end]

    center, start_angle, end_angle, radius = bulge_to_arc(start, end, bulge)
    points = arc_points(center, radius, start_angle, end_angle, max_angle_degrees)
    points[0] = start
    points[-1] = end
    return points


def pairs_to_lines(
    points: list[tuple[float, float]],
    snap_tolerance: float,
) -> list[LineString]:
    lines: list[LineString] = []
    for start, end in zip(points, points[1:]):
        start = snap_point(start, snap_tolerance)
        end = snap_point(end, snap_tolerance)
        if start != end:
            lines.append(LineString([start, end]))
    return lines


def lwpolyline_lines(entity, scale: float, snap_tolerance: float, arc_angle: float) -> list[LineString]:
    vertices = list(entity.get_points("xyb"))
    if len(vertices) < 2:
        return []

    if entity.closed:
        vertices.append(vertices[0])

    lines: list[LineString] = []
    for current, nxt in zip(vertices, vertices[1:]):
        start = (current[0] * scale, current[1] * scale)
        end = (nxt[0] * scale, nxt[1] * scale)
        points = bulge_segment_points(start, end, current[2], arc_angle)
        lines.extend(pairs_to_lines(points, snap_tolerance))
    return lines


def circle_lines(entity, scale: float, snap_tolerance: float, arc_angle: float) -> list[LineString]:
    center = Vec2(entity.dxf.center.x * scale, entity.dxf.center.y * scale)
    radius = float(entity.dxf.radius) * scale
    points = arc_points(center, radius, 0.0, math.tau, arc_angle)
    points[-1] = points[0]
    return pairs_to_lines(points, snap_tolerance)


def arc_lines(entity, scale: float, snap_tolerance: float, arc_angle: float) -> list[LineString]:
    center = Vec2(entity.dxf.center.x * scale, entity.dxf.center.y * scale)
    radius = float(entity.dxf.radius) * scale
    start_angle = math.radians(float(entity.dxf.start_angle))
    end_angle = math.radians(float(entity.dxf.end_angle))
    points = arc_points(center, radius, start_angle, end_angle, arc_angle)
    return pairs_to_lines(points, snap_tolerance)


def extract_lines(
    doc: ezdxf.EzDxfDocument,
    scale: float,
    snap_tolerance: float,
    arc_angle: float,
) -> tuple[list[LineString], Counter[str]]:
    lines: list[LineString] = []
    unsupported: Counter[str] = Counter()

    for entity in doc.modelspace():
        entity_type = entity.dxftype()
        if entity_type == "LINE":
            start = scaled_point(entity.dxf.start, scale)
            end = scaled_point(entity.dxf.end, scale)
            lines.extend(pairs_to_lines([start, end], snap_tolerance))
        elif entity_type == "LWPOLYLINE":
            lines.extend(lwpolyline_lines(entity, scale, snap_tolerance, arc_angle))
        elif entity_type == "CIRCLE":
            lines.extend(circle_lines(entity, scale, snap_tolerance, arc_angle))
        elif entity_type == "ARC":
            lines.extend(arc_lines(entity, scale, snap_tolerance, arc_angle))
        elif entity_type == "POINT":
            continue
        else:
            unsupported[entity_type] += 1

    return lines, unsupported


def iter_polygons(geometry) -> Iterable[Polygon]:
    if geometry.is_empty:
        return
    geometry = make_valid(geometry)
    if isinstance(geometry, Polygon):
        yield geometry
    elif isinstance(geometry, MultiPolygon):
        yield from geometry.geoms
    elif isinstance(geometry, GeometryCollection):
        for part in geometry.geoms:
            yield from iter_polygons(part)


def polygonize_lines(
    lines: list[LineString],
    min_area: float,
    topology_snap_tolerance: float,
) -> tuple[list[Polygon], tuple]:
    linework = MultiLineString(lines)
    if topology_snap_tolerance > 0:
        linework = snap(linework, linework, topology_snap_tolerance)
    network = unary_union(linework)
    polygons, cut_edges, dangles, invalid_rings = polygonize_full(network)
    found = [
        polygon
        for polygon in polygonize(network)
        if polygon.area >= min_area and polygon.is_valid and not polygon.is_empty
    ]
    found.sort(key=lambda polygon: (-polygon.centroid.y, polygon.centroid.x, -polygon.area))
    return found, (polygons, cut_edges, dangles, invalid_rings)


def inset_polygons(
    polygons: list[Polygon],
    offset: float,
    min_inset_area: float,
) -> tuple[list[Polygon], int]:
    inset: list[Polygon] = []
    collapsed_count = 0
    for polygon in polygons:
        buffered = polygon.buffer(-offset, join_style=2, mitre_limit=10.0)
        pieces = [
            piece
            for piece in iter_polygons(buffered)
            if not piece.is_empty and piece.area >= min_inset_area
        ]
        if not pieces:
            collapsed_count += 1
        inset.extend(pieces)
    inset.sort(key=lambda polygon: (-polygon.centroid.y, polygon.centroid.x, -polygon.area))
    return inset, collapsed_count


def drawing_bounds(lines: list[LineString]) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = MultiLineString(lines).bounds
    if minx == maxx or miny == maxy:
        raise ValueError("The input linework has zero width or height.")
    return minx, miny, maxx, maxy


def svg_point(point: tuple[float, float], bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    minx, _miny, _maxx, maxy = bounds
    x, y = point
    return x - minx, maxy - y


def ring_path(coords: Iterable[tuple[float, float]], bounds: tuple[float, float, float, float]) -> str:
    points = list(coords)
    if len(points) < 2:
        return ""
    if points[0] == points[-1]:
        points = points[:-1]

    first_x, first_y = svg_point(points[0], bounds)
    parts = [f"M {fmt(first_x)} {fmt(first_y)}"]
    for point in points[1:]:
        x, y = svg_point(point, bounds)
        parts.append(f"L {fmt(x)} {fmt(y)}")
    parts.append("Z")
    return " ".join(parts)


def polygon_path(polygon: Polygon, bounds: tuple[float, float, float, float]) -> str:
    parts = [ring_path(polygon.exterior.coords, bounds)]
    parts.extend(ring_path(interior.coords, bounds) for interior in polygon.interiors)
    return " ".join(part for part in parts if part)


def write_svg(
    output: Path,
    polygons: list[Polygon],
    bounds: tuple[float, float, float, float],
    stroke_width: float,
    source_name: str,
    offset: float,
) -> None:
    minx, miny, maxx, maxy = bounds
    width = maxx - minx
    height = maxy - miny

    paths = []
    for index, polygon in enumerate(polygons, start=1):
        paths.append(
            f'    <path id="piece-{index:03d}" d="{polygon_path(polygon, bounds)}" />'
        )

    svg = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{fmt(width)}in" height="{fmt(height)}in" '
                f'viewBox="0 0 {fmt(width)} {fmt(height)}">'
            ),
            f"  <title>{source_name} inset polygons</title>",
            (
                f"  <metadata>source={source_name}; offset_inches={fmt(offset)}; "
                f"piece_count={len(polygons)}; width_inches={fmt(width)}; "
                f"height_inches={fmt(height)}</metadata>"
            ),
            (
                f'  <g id="inset-polygons" fill="none" stroke="#000000" '
                f'stroke-width="{fmt(stroke_width)}" stroke-linecap="butt" '
                f'stroke-linejoin="miter" vector-effect="non-scaling-stroke">'
            ),
            *paths,
            "  </g>",
            "</svg>",
            "",
        ]
    )
    output.write_text(svg, encoding="utf-8")


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else input_path.with_name(f"{input_path.stem} inset.svg")
    )

    if args.offset <= 0:
        raise SystemExit("--offset must be positive.")
    if args.snap_tolerance < 0:
        raise SystemExit("--snap-tolerance cannot be negative.")
    if args.topology_snap_tolerance < 0:
        raise SystemExit("--topology-snap-tolerance cannot be negative.")

    doc = ezdxf.readfile(input_path)
    unit_scale, unit_code = dxf_unit_scale_to_inches(doc)
    if unit_code not in COMMON_DXF_UNITS_TO_INCHES:
        print(
            f"Warning: unsupported $INSUNITS={unit_code}; treating drawing units as inches.",
            file=sys.stderr,
        )

    lines, unsupported = extract_lines(doc, unit_scale, args.snap_tolerance, args.arc_angle)
    if not lines:
        raise SystemExit("No usable linework found in DXF.")

    source_polygons, polygonize_report = polygonize_lines(
        lines,
        args.min_area,
        args.topology_snap_tolerance,
    )
    if not source_polygons:
        raise SystemExit("No closed polygons found in DXF linework.")

    inset, collapsed_count = inset_polygons(source_polygons, args.offset, args.min_inset_area)
    if not inset:
        raise SystemExit("All polygons disappeared after applying the inward offset.")

    bounds = drawing_bounds(lines)
    write_svg(output_path, inset, bounds, args.stroke_width, input_path.name, args.offset)

    _polygons, cut_edges, dangles, invalid_rings = polygonize_report
    print(f"Read: {input_path}")
    print(f"Output: {output_path}")
    print(f"DXF $INSUNITS: {unit_code} (scale to inches: {fmt(unit_scale)})")
    print(f"Line segments used: {len(lines)}")
    print(f"Topology snap tolerance: {fmt(args.topology_snap_tolerance)}in")
    print(f"Source polygons found: {len(source_polygons)}")
    print(f"Inset polygons written: {len(inset)}")
    print(f"Source polygons collapsed by offset: {collapsed_count}")
    print(
        "Original SVG size: "
        f"{fmt(bounds[2] - bounds[0])}in x {fmt(bounds[3] - bounds[1])}in"
    )
    print(
        "Polygonize leftovers: "
        f"cut_edges={len(cut_edges.geoms)}, "
        f"dangles={len(dangles.geoms)}, "
        f"invalid_rings={len(invalid_rings.geoms)}"
    )
    if unsupported:
        print(f"Unsupported ignored entities: {dict(unsupported)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
