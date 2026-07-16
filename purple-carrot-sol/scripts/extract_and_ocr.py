#!/usr/bin/env python3
"""Export the Purple Carrot capture runs from Photos and OCR every page.

The script is intentionally deterministic: it uses the two uninterrupted June 5,
2026 capture runs, sorts each run oldest-first (front, then reverse), and pairs
adjacent frames. It compares Photos' current derivative with the original to
recover saved rotations, then uses Vision OCR geometry to catch pages that are
still sideways in Photos.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PHOTO_LIBRARY = Path(
    os.environ.get(
        "PHOTO_LIBRARY",
        str(Path.home() / "Pictures" / "Photos Library.photoslibrary"),
    )
)
DATABASE = PHOTO_LIBRARY / "database" / "Photos.sqlite"
ORIGINALS = PHOTO_LIBRARY / "originals"
DERIVATIVES = PHOTO_LIBRARY / "resources" / "derivatives"
VISION_SOURCE = ROOT / "scripts" / "vision_ocr.swift"
VISION_BINARY = ROOT / ".cache" / "vision_ocr"
IMAGE_DIR = ROOT / "public" / "images"
CACHE_DIR = ROOT / ".cache"
RAW_OUTPUT = ROOT / "data" / "source-pages.json"

CAPTURE_RUNS = [
    ("2026-06-05 12:52:00", "2026-06-05 13:03:00"),
    ("2026-06-05 16:47:00", "2026-06-05 16:56:00"),
]


@dataclass
class Asset:
    pk: int
    uuid: str
    filename: str
    directory: str
    created_at: str
    added_at: str | None
    width: int
    height: int
    photos_orientation: int
    favorite: bool
    latitude: float | None
    longitude: float | None
    run: int
    sequence: int

    @property
    def original_path(self) -> Path:
        return ORIGINALS / self.directory / self.filename

    @property
    def derivative_path(self) -> Path:
        return DERIVATIVES / self.directory / f"{self.uuid}_1_105_c.jpeg"


def compile_vision_ocr() -> None:
    VISION_BINARY.parent.mkdir(parents=True, exist_ok=True)
    if (
        VISION_BINARY.exists()
        and VISION_BINARY.stat().st_mtime >= VISION_SOURCE.stat().st_mtime
    ):
        return
    subprocess.run(
        ["swiftc", str(VISION_SOURCE), "-o", str(VISION_BINARY)],
        check=True,
    )


def load_assets() -> list[Asset]:
    if not DATABASE.exists():
        raise SystemExit(f"Photos database not found: {DATABASE}")

    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    query = """
        SELECT
            Z_PK AS pk,
            ZUUID AS uuid,
            ZFILENAME AS filename,
            ZDIRECTORY AS directory,
            datetime(ZDATECREATED + 978307200, 'unixepoch', 'localtime') AS created_at,
            datetime(ZADDEDDATE + 978307200, 'unixepoch', 'localtime') AS added_at,
            ZWIDTH AS width,
            ZHEIGHT AS height,
            ZORIENTATION AS photos_orientation,
            ZFAVORITE AS favorite,
            CASE WHEN ZLATITUDE = -180.0 THEN NULL ELSE ZLATITUDE END AS latitude,
            CASE WHEN ZLONGITUDE = -180.0 THEN NULL ELSE ZLONGITUDE END AS longitude
        FROM ZASSET
        WHERE ZTRASHEDSTATE = 0
          AND datetime(ZDATECREATED + 978307200, 'unixepoch', 'localtime')
              BETWEEN ? AND ?
        ORDER BY ZDATECREATED ASC
    """

    assets: list[Asset] = []
    sequence = 0
    for run_index, bounds in enumerate(CAPTURE_RUNS, start=1):
        rows = connection.execute(query, bounds).fetchall()
        if len(rows) % 2:
            raise SystemExit(
                f"Capture run {run_index} has an odd number of images: {len(rows)}"
            )
        for row in rows:
            sequence += 1
            assets.append(
                Asset(
                    pk=row["pk"],
                    uuid=row["uuid"],
                    filename=row["filename"],
                    directory=row["directory"],
                    created_at=row["created_at"],
                    added_at=row["added_at"],
                    width=row["width"],
                    height=row["height"],
                    photos_orientation=row["photos_orientation"],
                    favorite=bool(row["favorite"]),
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    run=run_index,
                    sequence=sequence,
                )
            )
    connection.close()

    if len(assets) != 254:
        raise SystemExit(f"Expected 254 recipe images, found {len(assets)}")
    for asset in assets:
        if not asset.original_path.exists():
            raise SystemExit(f"Original is not local: {asset.original_path}")
        if not asset.derivative_path.exists():
            raise SystemExit(f"Photos derivative is missing: {asset.derivative_path}")
    return assets


def centered_correlation(left: Image.Image, right: Image.Image) -> float:
    left_values = list(left.resize((64, 64)).convert("L").getdata())
    right_values = list(right.resize((64, 64)).convert("L").getdata())
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_values, right_values)
    )
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left_values)
        * sum((b - right_mean) ** 2 for b in right_values)
    )
    return numerator / denominator if denominator else 0.0


def photos_rotation(asset: Asset) -> tuple[int, float]:
    original = Image.open(asset.original_path)
    derivative = Image.open(asset.derivative_path)
    scores = {
        angle: centered_correlation(original.rotate(angle, expand=True), derivative)
        for angle in (0, 90, 180, 270)
    }
    angle = max(scores, key=scores.get)
    return angle, scores[angle]


def save_jpeg(image: Image.Image, path: Path, max_size: int, quality: int = 84) -> None:
    image = image.convert("RGB")
    image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=quality, optimize=True, progressive=True)


def run_ocr(paths: Iterable[Path], chunk_size: int = 24) -> dict[str, dict[str, Any]]:
    compile_vision_ocr()
    items = list(paths)
    results: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(items), chunk_size):
        chunk = items[offset : offset + chunk_size]
        process = subprocess.run(
            [str(VISION_BINARY), *(str(path) for path in chunk)],
            check=True,
            stdout=subprocess.PIPE,
        )
        for result in json.loads(process.stdout):
            results[str(Path(result["image"]).resolve())] = result
        print(
            f"OCR {min(offset + len(chunk), len(items)):>3}/{len(items)}",
            file=sys.stderr,
        )
    return results


def horizontal_ratio(result: dict[str, Any]) -> float:
    horizontal = 0
    total = 0
    for line in result["lines"]:
        length = len(line["text"])
        total += length
        box = line["box"]
        if box["width"] >= box["height"] * 1.8:
            horizontal += length
    return horizontal / total if total else 0.0


def upright_score(result: dict[str, Any]) -> float:
    lines = result["lines"]
    score = horizontal_ratio(result) * 1_000

    def matching(pattern: str) -> list[dict[str, Any]]:
        regex = re.compile(pattern, re.I)
        return [line for line in lines if regex.search(line["text"])]

    directions = matching(r"^\s*directions\s*$")
    if directions:
        score += max(line["box"]["y"] for line in directions) * 700

    summary = matching(r"^\s*summary\s*$")
    if summary:
        score += (1 - min(line["box"]["y"] for line in summary)) * 350

    purple_carrot = matching(r"purple\s*[|/\-]?\s*carrot")
    if purple_carrot:
        score += (1 - min(line["box"]["y"] for line in purple_carrot)) * 300

    ingredients = matching(r"^\s*ingredients:?\s*$")
    servings = matching(r"servings")
    if ingredients and servings:
        ingredient_y = max(line["box"]["y"] for line in ingredients)
        serving_y = max(line["box"]["y"] for line in servings)
        if serving_y > ingredient_y:
            score += 400

    numbered: list[tuple[float, float, int]] = []
    for line in lines:
        match = re.match(r"^\s*([1-8])[.)]\s", line["text"])
        if match:
            numbered.append(
                (-line["box"]["y"], line["box"]["x"], int(match.group(1)))
            )
    numbers = [number for _, _, number in sorted(numbered)]
    if len(numbers) >= 3:
        increasing = sum(a < b for a, b in zip(numbers, numbers[1:]))
        decreasing = sum(a > b for a, b in zip(numbers, numbers[1:]))
        score += (increasing - decreasing) * 180

    return score


def crop_dish(cover: Image.Image) -> Image.Image:
    width, height = cover.size
    if height > width:
        box = (int(width * 0.045), 0, int(width * 0.93), int(height * 0.54))
    else:
        box = (int(width * 0.025), 0, int(width * 0.975), int(height * 0.70))
    return cover.crop(box)


def main() -> None:
    assets = load_assets()
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    page_records: list[dict[str, Any]] = []
    base_paths: list[Path] = []
    for index, asset in enumerate(assets):
        recipe_number = index // 2 + 1
        side = "cover" if index % 2 == 0 else "method"
        output = IMAGE_DIR / f"recipe-{recipe_number:03d}-{side}.jpg"
        angle, correlation = photos_rotation(asset)
        image = Image.open(asset.original_path).rotate(angle, expand=True)
        save_jpeg(image, output, max_size=2200)
        base_paths.append(output)
        page_records.append(
            {
                "recipe_number": recipe_number,
                "side": side,
                "image": f"images/{output.name}",
                "rotation_degrees_ccw": angle,
                "rotation_source": "photos-derivative",
                "rotation_correlation": round(correlation, 6),
                "metadata": asdict(asset),
            }
        )
        if (index + 1) % 20 == 0 or index + 1 == len(assets):
            print(f"Export {index + 1:>3}/{len(assets)}", file=sys.stderr)

    initial_ocr = run_ocr(base_paths)
    sideways_indices = [
        index
        for index, path in enumerate(base_paths)
        if horizontal_ratio(initial_ocr[str(path.resolve())]) < 0.55
    ]
    print(f"Sideways pages detected: {len(sideways_indices)}", file=sys.stderr)

    candidate_paths: list[Path] = []
    candidates: dict[int, list[tuple[int, Path]]] = {}
    for index in sideways_indices:
        path = base_paths[index]
        image = Image.open(path)
        candidate_set: list[tuple[int, Path]] = []
        for correction in (90, 270):
            candidate_path = CACHE_DIR / f"orientation-{index:03d}-{correction}.jpg"
            save_jpeg(image.rotate(correction, expand=True), candidate_path, max_size=1600)
            candidate_paths.append(candidate_path)
            candidate_set.append((correction, candidate_path))
        candidates[index] = candidate_set

    candidate_ocr = run_ocr(candidate_paths) if candidate_paths else {}
    for index, candidate_set in candidates.items():
        correction, candidate_path = max(
            candidate_set,
            key=lambda item: upright_score(candidate_ocr[str(item[1].resolve())]),
        )
        record = page_records[index]
        record["rotation_degrees_ccw"] = (
            record["rotation_degrees_ccw"] + correction
        ) % 360
        record["rotation_source"] = "photos-derivative+vision"
        image = Image.open(base_paths[index]).rotate(correction, expand=True)
        save_jpeg(image, base_paths[index], max_size=2200)

    final_ocr = run_ocr(base_paths)
    for index, path in enumerate(base_paths):
        result = final_ocr[str(path.resolve())]
        # Vision reports the local absolute input path; keep the archive portable.
        result["image"] = page_records[index]["image"]
        page_records[index]["ocr"] = result
        page_records[index]["ocr_horizontal_ratio"] = round(
            horizontal_ratio(result), 4
        )

    for recipe_number in range(1, len(assets) // 2 + 1):
        cover_path = IMAGE_DIR / f"recipe-{recipe_number:03d}-cover.jpg"
        dish_path = IMAGE_DIR / f"recipe-{recipe_number:03d}-dish.jpg"
        save_jpeg(crop_dish(Image.open(cover_path)), dish_path, max_size=1400, quality=86)

    payload = {
        "source": "Photos Library.photoslibrary",
        "selection": {
            "capture_runs": CAPTURE_RUNS,
            "image_count": len(page_records),
            "recipe_pair_count": len(page_records) // 2,
            "sort": "capture time ascending within each run",
            "pairing": "adjacent images: cover then method",
        },
        "pages": page_records,
    }
    RAW_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    RAW_OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {RAW_OUTPUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
