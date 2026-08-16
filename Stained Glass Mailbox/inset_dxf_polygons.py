#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

import ezdxf
from ezdxf import units
from ezdxf.math import Vec2, bulge_to_arc
from shapely import affinity
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
    box,
)
from shapely.ops import linemerge, polygonize, polygonize_full, snap, unary_union
from shapely.validation import make_valid


COMMON_DXF_UNITS_TO_INCHES = {
    0: 1.0,  # Unitless: assume the drawing was authored in inches.
    1: 1.0,  # Inches
    2: 12.0,  # Feet
    4: 1.0 / 25.4,  # Millimeters
    5: 1.0 / 2.54,  # Centimeters
    6: 39.37007874015748,  # Meters
}

CONFIG_DEFAULTS = {
    "offset_inches": 0.01,
    "target_width_inches": None,
    "clip_x_bounds_inches": None,
    "clip_bounds_inches": None,
    "arc_angle_degrees": 1.0,
    "topology_snap_tolerance_inches": 0.0002,
    "simplify_tolerance_inches": 0.0002,
}

SUPPORTED_CONFIG_KEYS = set(CONFIG_DEFAULTS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clean Fusion DXF linework and create a direct SVG, an inset SVG, and a "
            "Silhouette-safe LINE-only DXF at physical size."
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
        "--svg-output",
        type=Path,
        help="Direct (non-inset) SVG path. Defaults to '<input stem>.svg'.",
    )
    parser.add_argument(
        "--dxf-output",
        type=Path,
        help="LINE-only DXF path. Defaults to '<input stem> Silhouette.dxf'.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "Conversion JSON path. By default, '<input stem>.conversion.json' is "
            "loaded when it exists."
        ),
    )
    parser.add_argument(
        "--offset",
        type=float,
        help="Inward offset in inches. Default: config value or 0.01.",
    )
    parser.add_argument(
        "--target-width",
        type=float,
        help="Uniformly scale all outputs to this width in inches.",
    )
    parser.add_argument(
        "--clip-x-bounds",
        type=float,
        nargs=2,
        metavar=("MIN_X", "MAX_X"),
        help=(
            "Keep the closed profile between these X coordinates (in source inches). "
            "Useful for trimming Fusion construction geometry."
        ),
    )
    parser.add_argument(
        "--clip-bounds",
        type=float,
        nargs=4,
        metavar=("MIN_X", "MIN_Y", "MAX_X", "MAX_Y"),
        help="Keep the closed profile inside these source-inch bounds.",
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
        help=(
            "Join near-touching endpoint-to-line geometry before polygonizing, "
            "in inches. Default: config value or 0.0002."
        ),
    )
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        help=(
            "Remove redundant micro-segments after topology repair, in inches. "
            "Default: config value or 0.0002."
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
        help=(
            "Maximum degrees per generated segment when flattening arcs. "
            "Default: config value or 1."
        ),
    )
    parser.add_argument(
        "--stroke-width",
        type=float,
        default=0.003,
        help="SVG stroke width in inches. Default: 0.003.",
    )
    return parser.parse_args()


def load_config(input_path: Path, requested_path: Path | None) -> tuple[dict[str, Any], Path | None]:
    if requested_path:
        config_path = requested_path.expanduser().resolve()
        if not config_path.exists():
            raise SystemExit(f"Conversion config does not exist: {config_path}")
    else:
        candidate = input_path.with_name(f"{input_path.stem}.conversion.json")
        config_path = candidate if candidate.exists() else None

    if config_path is None:
        return {}, None

    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Could not read conversion config {config_path}: {error}") from error
    if not isinstance(loaded, dict):
        raise SystemExit(f"Conversion config must contain a JSON object: {config_path}")

    unknown = sorted(set(loaded) - SUPPORTED_CONFIG_KEYS)
    if unknown:
        raise SystemExit(f"Unsupported conversion config keys: {', '.join(unknown)}")
    return loaded, config_path


def config_value(
    command_line_value: Any,
    config: dict[str, Any],
    config_key: str,
) -> Any:
    if command_line_value is not None:
        return command_line_value
    return config.get(config_key, CONFIG_DEFAULTS[config_key])


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


def iter_lines(geometry) -> Iterable[LineString]:
    if geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        yield geometry
    elif isinstance(geometry, MultiLineString):
        yield from geometry.geoms
    elif isinstance(geometry, GeometryCollection):
        for part in geometry.geoms:
            yield from iter_lines(part)


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


def profile_clip_box(
    line_bounds: tuple[float, float, float, float],
    clip_x_bounds: list[float] | tuple[float, float] | None,
    clip_bounds: list[float] | tuple[float, float, float, float] | None,
) -> Polygon | None:
    if clip_x_bounds is not None and clip_bounds is not None:
        raise SystemExit("Use only one of --clip-x-bounds and --clip-bounds.")

    if clip_bounds is not None:
        if len(clip_bounds) != 4:
            raise SystemExit("clip_bounds_inches must contain four numbers.")
        minx, miny, maxx, maxy = (float(value) for value in clip_bounds)
    elif clip_x_bounds is not None:
        if len(clip_x_bounds) != 2:
            raise SystemExit("clip_x_bounds_inches must contain two numbers.")
        minx, maxx = (float(value) for value in clip_x_bounds)
        _line_minx, line_miny, _line_maxx, line_maxy = line_bounds
        padding = max(line_maxy - line_miny, 1.0)
        miny = line_miny - padding
        maxy = line_maxy + padding
    else:
        return None

    if minx >= maxx or miny >= maxy:
        raise SystemExit("Conversion clip bounds must have positive width and height.")
    return box(minx, miny, maxx, maxy)


def clip_polygons(
    polygons: list[Polygon],
    clipping_box: Polygon | None,
    min_area: float,
) -> list[Polygon]:
    if clipping_box is None:
        return polygons

    clipped: list[Polygon] = []
    for polygon in polygons:
        clipped.extend(
            piece
            for piece in iter_polygons(polygon.intersection(clipping_box))
            if piece.area >= min_area and piece.is_valid and not piece.is_empty
        )
    return clipped


def canonical_profile(
    polygons: list[Polygon],
    min_area: float,
    topology_snap_tolerance: float,
    simplify_tolerance: float,
) -> tuple[object, list[Polygon], tuple]:
    """Return one deduplicated, closed line network shared by every output format."""
    network = unary_union([polygon.boundary for polygon in polygons])
    if topology_snap_tolerance > 0:
        network = snap(network, network, topology_snap_tolerance)
    network = unary_union(network)

    merged = linemerge(network) if isinstance(network, MultiLineString) else network
    if simplify_tolerance > 0:
        merged = merged.simplify(simplify_tolerance, preserve_topology=True)
    network = unary_union(merged)

    _all_polygons, cut_edges, dangles, invalid_rings = polygonize_full(network)
    found = [
        polygon
        for polygon in polygonize(network)
        if polygon.area >= min_area and polygon.is_valid and not polygon.is_empty
    ]
    if not found:
        raise SystemExit("No closed profile remained after clipping and topology repair.")

    # Rebuild from the accepted faces so DXF/SVG outputs contain no construction
    # dangles or duplicated shared edges.
    network = unary_union([polygon.boundary for polygon in found])
    found.sort(key=lambda polygon: (-polygon.centroid.y, polygon.centroid.x, -polygon.area))
    return network, found, (_all_polygons, cut_edges, dangles, invalid_rings)


def normalize_profile(
    network,
    target_width: float | None,
) -> tuple[object, tuple[float, float, float, float], float]:
    minx, miny, maxx, maxy = network.bounds
    width = maxx - minx
    height = maxy - miny
    if width <= 0 or height <= 0:
        raise SystemExit("The cleaned profile has zero width or height.")

    scale_factor = 1.0
    if target_width is not None:
        if target_width <= 0:
            raise SystemExit("--target-width must be positive.")
        scale_factor = target_width / width

    normalized = affinity.translate(network, xoff=-minx, yoff=-miny)
    if scale_factor != 1.0:
        normalized = affinity.scale(
            normalized,
            xfact=scale_factor,
            yfact=scale_factor,
            origin=(0.0, 0.0),
        )
    normalized = unary_union(normalized)
    bounds = normalized.bounds
    return normalized, bounds, scale_factor


def polygons_from_network(network, min_area: float) -> list[Polygon]:
    found = [
        polygon
        for polygon in polygonize(network)
        if polygon.area >= min_area and polygon.is_valid and not polygon.is_empty
    ]
    found.sort(key=lambda polygon: (-polygon.centroid.y, polygon.centroid.x, -polygon.area))
    return found


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


def line_path(line: LineString, bounds: tuple[float, float, float, float]) -> str:
    points = list(line.coords)
    if len(points) < 2:
        return ""
    first_x, first_y = svg_point(points[0], bounds)
    parts = [f"M {fmt(first_x)} {fmt(first_y)}"]
    for point in points[1:]:
        x, y = svg_point(point, bounds)
        parts.append(f"L {fmt(x)} {fmt(y)}")
    return " ".join(parts)


def write_linework_svg(
    output: Path,
    network,
    bounds: tuple[float, float, float, float],
    stroke_width: float,
    source_name: str,
    region_count: int,
) -> None:
    minx, miny, maxx, maxy = bounds
    width = maxx - minx
    height = maxy - miny
    merged = linemerge(network) if isinstance(network, MultiLineString) else network
    lines = sorted(
        iter_lines(merged),
        key=lambda line: (-line.bounds[3], line.bounds[0], line.bounds[1], line.length),
    )
    paths = [
        f'    <path id="cut-line-{index:03d}" d="{line_path(line, bounds)}" />'
        for index, line in enumerate(lines, start=1)
    ]
    safe_source_name = escape(source_name)
    svg = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{fmt(width)}in" height="{fmt(height)}in" '
                f'viewBox="0 0 {fmt(width)} {fmt(height)}">'
            ),
            f"  <title>{safe_source_name} direct cut lines</title>",
            (
                f"  <metadata>source={safe_source_name}; inset=false; "
                f"region_count={region_count}; width_inches={fmt(width)}; "
                f"height_inches={fmt(height)}</metadata>"
            ),
            (
                f'  <g id="direct-cut-lines" fill="none" stroke="#000000" '
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

    safe_source_name = escape(source_name)
    svg = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{fmt(width)}in" height="{fmt(height)}in" '
                f'viewBox="0 0 {fmt(width)} {fmt(height)}">'
            ),
            f"  <title>{safe_source_name} inset polygons</title>",
            (
                f"  <metadata>source={safe_source_name}; offset_inches={fmt(offset)}; "
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


def write_line_only_dxf(output: Path, network) -> int:
    output_doc = ezdxf.new("R2000")
    output_doc.units = units.IN
    output_doc.header["$INSUNITS"] = units.IN
    if "CUT" not in output_doc.layers:
        output_doc.layers.add("CUT", color=7)

    modelspace = output_doc.modelspace()
    segment_count = 0
    merged = linemerge(network) if isinstance(network, MultiLineString) else network
    for line in iter_lines(merged):
        coordinates = list(line.coords)
        for start, end in zip(coordinates, coordinates[1:]):
            if start == end:
                continue
            modelspace.add_line(start, end, dxfattribs={"layer": "CUT"})
            segment_count += 1

    output_doc.saveas(output)
    return segment_count


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    inset_output_path = (
        args.output.expanduser().resolve()
        if args.output
        else input_path.with_name(f"{input_path.stem} inset.svg")
    )
    direct_svg_output_path = (
        args.svg_output.expanduser().resolve()
        if args.svg_output
        else input_path.with_suffix(".svg")
    )
    dxf_output_path = (
        args.dxf_output.expanduser().resolve()
        if args.dxf_output
        else input_path.with_name(f"{input_path.stem} Silhouette.dxf")
    )

    config, config_path = load_config(input_path, args.config)
    offset = float(config_value(args.offset, config, "offset_inches"))
    target_width_value = config_value(args.target_width, config, "target_width_inches")
    target_width = float(target_width_value) if target_width_value is not None else None
    clip_x_bounds = config_value(args.clip_x_bounds, config, "clip_x_bounds_inches")
    clip_bounds = config_value(args.clip_bounds, config, "clip_bounds_inches")
    arc_angle = float(config_value(args.arc_angle, config, "arc_angle_degrees"))
    topology_snap_tolerance = float(
        config_value(
            args.topology_snap_tolerance,
            config,
            "topology_snap_tolerance_inches",
        )
    )
    simplify_tolerance = float(
        config_value(
            args.simplify_tolerance,
            config,
            "simplify_tolerance_inches",
        )
    )

    if offset <= 0:
        raise SystemExit("--offset must be positive.")
    if args.snap_tolerance < 0:
        raise SystemExit("--snap-tolerance cannot be negative.")
    if topology_snap_tolerance < 0:
        raise SystemExit("--topology-snap-tolerance cannot be negative.")
    if simplify_tolerance < 0:
        raise SystemExit("--simplify-tolerance cannot be negative.")
    if arc_angle <= 0:
        raise SystemExit("--arc-angle must be positive.")
    if target_width is not None and target_width <= 0:
        raise SystemExit("--target-width must be positive.")

    doc = ezdxf.readfile(input_path)
    unit_scale, unit_code = dxf_unit_scale_to_inches(doc)
    if unit_code not in COMMON_DXF_UNITS_TO_INCHES:
        print(
            f"Warning: unsupported $INSUNITS={unit_code}; treating drawing units as inches.",
            file=sys.stderr,
        )

    lines, unsupported = extract_lines(doc, unit_scale, args.snap_tolerance, arc_angle)
    if not lines:
        raise SystemExit("No usable linework found in DXF.")

    source_polygons, polygonize_report = polygonize_lines(
        lines,
        args.min_area,
        topology_snap_tolerance,
    )
    if not source_polygons:
        raise SystemExit("No closed polygons found in DXF linework.")

    raw_bounds = drawing_bounds(lines)
    clipping_box = profile_clip_box(raw_bounds, clip_x_bounds, clip_bounds)
    clipped_polygons = clip_polygons(source_polygons, clipping_box, args.min_area)
    if not clipped_polygons:
        raise SystemExit("No closed polygons remained inside the configured clip bounds.")

    profile_network, profile_polygons, profile_report = canonical_profile(
        clipped_polygons,
        args.min_area,
        topology_snap_tolerance,
        simplify_tolerance,
    )
    profile_network, bounds, scale_factor = normalize_profile(profile_network, target_width)
    profile_polygons = polygons_from_network(profile_network, args.min_area)
    if not profile_polygons:
        raise SystemExit("No closed polygons remained after sizing the cleaned profile.")

    inset, collapsed_count = inset_polygons(profile_polygons, offset, args.min_inset_area)
    if not inset:
        raise SystemExit("All polygons disappeared after applying the inward offset.")

    write_linework_svg(
        direct_svg_output_path,
        profile_network,
        bounds,
        args.stroke_width,
        input_path.name,
        len(profile_polygons),
    )
    write_svg(
        inset_output_path,
        inset,
        bounds,
        args.stroke_width,
        input_path.name,
        offset,
    )
    dxf_segment_count = write_line_only_dxf(dxf_output_path, profile_network)

    _polygons, cut_edges, dangles, invalid_rings = polygonize_report
    _profile_all_polygons, profile_cut_edges, profile_dangles, profile_invalid_rings = (
        profile_report
    )
    print(f"Read: {input_path}")
    if config_path:
        print(f"Config: {config_path}")
    else:
        print("Config: none")
    print(f"Direct SVG: {direct_svg_output_path}")
    print(f"Inset SVG: {inset_output_path}")
    print(f"LINE-only DXF: {dxf_output_path}")
    print(f"DXF $INSUNITS: {unit_code} (scale to inches: {fmt(unit_scale)})")
    print(f"Line segments used: {len(lines)}")
    print(f"Arc flattening angle: {fmt(arc_angle)} degrees")
    print(f"Topology snap tolerance: {fmt(topology_snap_tolerance)}in")
    print(f"Source polygons found before profile cleanup: {len(source_polygons)}")
    print(f"Polygons inside configured profile: {len(clipped_polygons)}")
    print(f"Direct closed regions written: {len(profile_polygons)}")
    print(f"Inset polygons written: {len(inset)}")
    print(f"Source polygons collapsed by offset: {collapsed_count}")
    print(f"LINE entities written to DXF: {dxf_segment_count}")
    if target_width is not None:
        print(
            f"Target width: {fmt(target_width)}in "
            f"(uniform scale: {fmt(scale_factor)})"
        )
    print(
        "Output size: "
        f"{fmt(bounds[2] - bounds[0])}in x {fmt(bounds[3] - bounds[1])}in"
    )
    print(
        "Polygonize leftovers: "
        f"cut_edges={len(cut_edges.geoms)}, "
        f"dangles={len(dangles.geoms)}, "
        f"invalid_rings={len(invalid_rings.geoms)}"
    )
    print(
        "Cleaned profile leftovers: "
        f"cut_edges={len(profile_cut_edges.geoms)}, "
        f"dangles={len(profile_dangles.geoms)}, "
        f"invalid_rings={len(profile_invalid_rings.geoms)}"
    )
    if unsupported:
        print(f"Unsupported ignored entities: {dict(unsupported)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
