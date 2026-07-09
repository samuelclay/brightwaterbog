#!/usr/bin/env python3
"""Export and review stained-glass candidates from Apple Photos.

The Photos library is treated as read-only. Exported images and manifests are
written under photos/apple-photos-stained-glass/, which is ignored by git.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps


APPLE_EPOCH_OFFSET = 978_307_200
DEFAULT_LIBRARY = Path.home() / "Pictures/Photos Library.photoslibrary"
DEFAULT_OUT = Path("photos/apple-photos-stained-glass")
IMAGE_KINDS = {"public.jpeg", "public.heic", "public.png", "org.webmproject.webp"}
MEDIA_SUFFIXES = {".jpg", ".jpeg", ".heic", ".png", ".webp", ".mov", ".mp4", ".m4v"}
SHEET_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".heic", ".png", ".webp"}
UUID_RE = re.compile(
    r"([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Asset:
    pk: int
    uuid: str
    created: str
    filename: str
    directory: str
    latitude: float
    longitude: float
    width: int
    height: int
    uti: str
    source_path: str
    source_kind: str
    sha256: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--since", default="2020-07-02")
    parser.add_argument("--until", default="2026-07-02")
    parser.add_argument("--lat-min", type=float, default=42.48)
    parser.add_argument("--lat-max", type=float, default=42.52)
    parser.add_argument("--lon-min", type=float, default=-72.44)
    parser.add_argument("--lon-max", type=float, default=-72.39)
    parser.add_argument("--reference-date", default="2026-06-25")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_ref = subparsers.add_parser("export-reference")
    export_ref.add_argument("--contact-sheet", action="store_true")

    export_candidates = subparsers.add_parser("export-candidates")
    export_candidates.add_argument("--contact-sheets", action="store_true")
    export_candidates.add_argument("--limit", type=int, default=None)
    export_candidates.add_argument(
        "--no-copy",
        action="store_true",
        help="Only write manifests/contact sheets; do not copy originals.",
    )

    sheets = subparsers.add_parser("contact-sheets")
    sheets.add_argument("manifest", type=Path)
    sheets.add_argument("--sheet-prefix", default="sheet")
    sheets.add_argument("--sheet-dir", type=Path, default=None)
    sheets.add_argument("--cols", type=int, default=5)
    sheets.add_argument("--rows", type=int, default=4)

    selection = subparsers.add_parser("export-selection")
    selection.add_argument("selection", type=Path)
    selection.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Source manifest. Defaults to selection.source_manifest.",
    )
    selection.add_argument(
        "--selected-dir",
        type=Path,
        default=None,
        help="Destination root. Defaults to out/selected.",
    )

    recatalog = subparsers.add_parser("recatalog")
    recatalog.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Folder roots to catalog. Defaults to out/selected and out/review when present.",
    )

    return parser.parse_args()


def apple_seconds(date_text: str, end_of_day: bool = False) -> int:
    dt = datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc)
    seconds = int(dt.timestamp() - APPLE_EPOCH_OFFSET)
    if end_of_day:
        seconds += 24 * 60 * 60 - 1
    return seconds


def photos_db(library: Path) -> Path:
    return library / "database" / "Photos.sqlite"


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def original_path(library: Path, directory: str, filename: str) -> Path:
    return library / "originals" / directory / filename


def best_derivative_path(library: Path, uuid: str) -> Path | None:
    first = uuid[0].upper()
    roots = (
        library / "resources" / "derivatives" / "masters" / first,
        library / "resources" / "renders" / first,
        library / "resources" / "derivatives" / first,
    )
    matches: list[Path] = []
    for root in roots:
        if root.exists():
            matches.extend(path for path in root.glob(f"{uuid}*") if path.is_file())
    image_matches = [
        path
        for path in matches
        if path.suffix.lower() in {".jpg", ".jpeg", ".heic", ".png", ".webp"}
    ]
    if not image_matches:
        return None
    return max(image_matches, key=lambda path: path.stat().st_size)


def resolve_source(library: Path, directory: str, filename: str, uuid: str) -> tuple[Path, str]:
    original = original_path(library, directory, filename)
    if original.exists():
        return original, "original"
    derivative = best_derivative_path(library, uuid)
    if derivative is not None:
        return derivative, "derivative"
    return original, "missing"


def row_to_asset(library: Path, row: sqlite3.Row) -> Asset:
    source, source_kind = resolve_source(
        library,
        row["ZDIRECTORY"],
        row["ZFILENAME"],
        row["ZUUID"],
    )
    return Asset(
        pk=int(row["Z_PK"]),
        uuid=row["ZUUID"],
        created=row["created"],
        filename=row["ZFILENAME"],
        directory=row["ZDIRECTORY"],
        latitude=float(row["ZLATITUDE"]),
        longitude=float(row["ZLONGITUDE"]),
        width=int(row["ZWIDTH"] or 0),
        height=int(row["ZHEIGHT"] or 0),
        uti=row["ZUNIFORMTYPEIDENTIFIER"],
        source_path=str(source),
        source_kind=source_kind,
    )


def query_assets(
    library: Path,
    *,
    since: str,
    until: str,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    reference_date: str | None = None,
    limit: int | None = None,
) -> list[Asset]:
    conn = connect_readonly(photos_db(library))
    conn.row_factory = sqlite3.Row
    params: dict[str, object] = {
        "since": since,
        "until": until,
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lon_min": lon_min,
        "lon_max": lon_max,
    }
    date_filter = ""
    if reference_date is not None:
        date_filter = "and date(a.ZDATECREATED + 978307200, 'unixepoch', 'localtime') = :reference_date"
        params["reference_date"] = reference_date
    limit_sql = ""
    if limit is not None:
        limit_sql = "limit :limit"
        params["limit"] = limit
    sql = f"""
        select
            a.Z_PK,
            a.ZUUID,
            datetime(a.ZDATECREATED + 978307200, 'unixepoch', 'localtime') as created,
            a.ZFILENAME,
            a.ZDIRECTORY,
            a.ZLATITUDE,
            a.ZLONGITUDE,
            a.ZWIDTH,
            a.ZHEIGHT,
            a.ZUNIFORMTYPEIDENTIFIER
        from ZASSET a
        where a.ZKIND = 0
          and a.ZTRASHEDSTATE = 0
          and date(a.ZDATECREATED + 978307200, 'unixepoch', 'localtime') between :since and :until
          and a.ZLATITUDE between :lat_min and :lat_max
          and a.ZLONGITUDE between :lon_min and :lon_max
          and a.ZUNIFORMTYPEIDENTIFIER in ({",".join("?" for _ in IMAGE_KINDS)})
          {date_filter}
        order by a.ZDATECREATED, a.Z_PK
        {limit_sql}
    """
    positional = tuple(IMAGE_KINDS)
    # sqlite3 cannot mix named dict binding with ad-hoc generated positional
    # placeholders, so use a small rewrite for UTI values.
    sql = sql.replace(
        f"a.ZUNIFORMTYPEIDENTIFIER in ({','.join('?' for _ in IMAGE_KINDS)})",
        "a.ZUNIFORMTYPEIDENTIFIER in (:uti0, :uti1, :uti2, :uti3)",
    )
    for idx, uti in enumerate(sorted(positional)):
        params[f"uti{idx}"] = uti
    rows = conn.execute(sql, params).fetchall()
    return [row_to_asset(library, row) for row in rows]


def safe_name(asset: Asset) -> str:
    created = asset.created.replace("-", "").replace(":", "").replace(" ", "_")
    suffix = Path(asset.filename).suffix.lower()
    return f"{created}_{asset.uuid}{suffix}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_assets(assets: Sequence[Asset], dest_dir: Path) -> list[dict[str, object]]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for asset in assets:
        src = Path(asset.source_path)
        record = asdict(asset)
        if not src.exists():
            record["missing"] = True
            records.append(record)
            continue
        dest = dest_dir / safe_name(asset)
        if not dest.exists():
            shutil.copy2(src, dest)
        record["export_path"] = str(dest)
        record["sha256"] = sha256(dest)
        records.append(record)
    return records


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def write_csv(path: Path, records: Sequence[dict[str, object]]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for record in records for key in record.keys()})
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def title_from_slug(slug: str) -> str:
    return slug.replace("_", " ").strip().title()


def load_manifest(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text())


def open_for_sheet(path: Path, max_edge: int = 420) -> Image.Image:
    try:
        im = Image.open(path)
        im.load()
    except Exception:
        # Pillow on some systems lacks HEIC support. Use sips for macOS-native
        # conversion into a temporary JPEG for contact-sheet generation.
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            subprocess.run(
                ["sips", "-s", "format", "jpeg", str(path), "--out", str(tmp_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            im = Image.open(tmp_path)
            im.load()
        finally:
            tmp_path.unlink(missing_ok=True)
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return im


def font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def contact_sheets(
    records: Sequence[dict[str, object]],
    sheet_dir: Path,
    *,
    prefix: str,
    cols: int,
    rows: int,
) -> list[Path]:
    sheet_dir.mkdir(parents=True, exist_ok=True)
    tile_w, tile_h = 430, 520
    margin = 18
    label_h = 82
    per_sheet = cols * rows
    sheets: list[Path] = []
    body_font = font(18)
    small_font = font(14)
    indexed = []
    for idx, record in enumerate(records):
        image_path = (
            record.get("catalog_path")
            or record.get("selected_export_path")
            or record.get("export_path")
            or record.get("source_path")
        )
        if image_path:
            path = Path(str(image_path))
            if path.exists() and path.suffix.lower() in SHEET_IMAGE_SUFFIXES:
                indexed.append((idx + 1, record))
    for sheet_index in range(math.ceil(len(indexed) / per_sheet)):
        chunk = indexed[sheet_index * per_sheet : (sheet_index + 1) * per_sheet]
        canvas = Image.new(
            "RGB",
            (cols * tile_w + margin * 2, rows * tile_h + margin * 2),
            "white",
        )
        draw = ImageDraw.Draw(canvas)
        for pos, (asset_index, record) in enumerate(chunk):
            col = pos % cols
            row = pos // cols
            x = margin + col * tile_w
            y = margin + row * tile_h
            image_path = (
                record.get("catalog_path")
                or record.get("selected_export_path")
                or record.get("export_path")
                or record.get("source_path")
            )
            if not image_path:
                continue
            path = Path(str(image_path))
            im = open_for_sheet(path)
            image_x = x + (tile_w - im.width) // 2
            image_y = y
            canvas.paste(im, (image_x, image_y))
            label_y = y + tile_h - label_h
            created = str(record.get("created", ""))
            name = f"{asset_index:04d}  {created[:16]}"
            if record.get("latitude") is not None and record.get("longitude") is not None:
                coords = f"{float(record['latitude']):.6f}, {float(record['longitude']):.6f}"
            else:
                coords = ""
            uuid = str(record.get("uuid", ""))[:8]
            draw.text((x + 8, label_y + 4), name, fill="black", font=body_font)
            draw.text((x + 8, label_y + 30), coords, fill="#333333", font=small_font)
            draw.text((x + 8, label_y + 52), uuid, fill="#555555", font=small_font)
            draw.rectangle((x, y, x + tile_w - 1, y + tile_h - 1), outline="#dddddd")
        sheet_path = sheet_dir / f"{prefix}_{sheet_index + 1:03d}.jpg"
        canvas.save(sheet_path, quality=92)
        sheets.append(sheet_path)
    return sheets


def export_reference(args: argparse.Namespace) -> None:
    assets = query_assets(
        args.library,
        since=args.reference_date,
        until=args.reference_date,
        lat_min=args.lat_min,
        lat_max=args.lat_max,
        lon_min=args.lon_min,
        lon_max=args.lon_max,
        reference_date=args.reference_date,
    )
    records = copy_assets(assets, args.out / "_reference" / args.reference_date)
    write_json(args.out / "_reference" / f"{args.reference_date}_manifest.json", records)
    write_csv(args.out / "_reference" / f"{args.reference_date}_manifest.csv", records)
    if args.contact_sheet:
        sheets = contact_sheets(
            records,
            args.out / "_reference" / "contact_sheets",
            prefix=f"reference_{args.reference_date}",
            cols=4,
            rows=3,
        )
        print(f"Wrote {len(records)} reference exports and {len(sheets)} contact sheets.")
    else:
        print(f"Wrote {len(records)} reference exports.")


def export_candidates(args: argparse.Namespace) -> None:
    assets = query_assets(
        args.library,
        since=args.since,
        until=args.until,
        lat_min=args.lat_min,
        lat_max=args.lat_max,
        lon_min=args.lon_min,
        lon_max=args.lon_max,
        limit=args.limit,
    )
    if args.no_copy:
        records = [asdict(asset) for asset in assets]
    else:
        records = copy_assets(assets, args.out / "_candidates")
    write_json(args.out / "_candidates_manifest.json", records)
    write_csv(args.out / "_candidates_manifest.csv", records)
    if args.contact_sheets:
        sheets = contact_sheets(
            records,
            args.out / "_candidate_contact_sheets",
            prefix="candidates",
            cols=5,
            rows=4,
        )
        print(f"Wrote {len(records)} candidate exports and {len(sheets)} contact sheets.")
    else:
        print(f"Wrote {len(records)} candidate exports.")


def build_contact_sheets(args: argparse.Namespace) -> None:
    records = load_manifest(args.manifest)
    sheet_dir = args.sheet_dir or args.manifest.parent / "contact_sheets"
    sheets = contact_sheets(
        records,
        sheet_dir,
        prefix=args.sheet_prefix,
        cols=args.cols,
        rows=args.rows,
    )
    print(f"Wrote {len(sheets)} contact sheets to {sheet_dir}.")


def selection_dest_name(record: dict[str, object]) -> str:
    created = str(record.get("created", "unknown")).replace("-", "").replace(":", "").replace(" ", "_")
    uuid = str(record.get("uuid", "unknown"))
    source_path = Path(str(record.get("source_path") or record.get("export_path") or record.get("filename") or "image.jpg"))
    suffix = source_path.suffix.lower() or Path(str(record.get("filename", ""))).suffix.lower() or ".jpg"
    return f"{created}_{uuid}{suffix}"


def export_selection(args: argparse.Namespace) -> None:
    payload = json.loads(args.selection.read_text())
    manifest_path = args.manifest or Path(payload["source_manifest"])
    records = load_manifest(manifest_path)
    by_index = {index + 1: record for index, record in enumerate(records)}
    by_pk = {int(record["pk"]): record for record in records if record.get("pk") is not None}
    selected_dir = args.selected_dir or args.out / "selected"
    summary: list[dict[str, object]] = []
    seen_destinations: set[Path] = set()

    for group in payload.get("groups", []):
        slug = group["slug"]
        dest_dir = selected_dir / slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        group_records: list[dict[str, object]] = []
        selected_records: list[dict[str, object]] = []
        for index in group.get("indices", []):
            if int(index) not in by_index:
                raise SystemExit(f"Selection index {index} not found in {manifest_path}")
            selected_records.append(by_index[int(index)])
        for pk in group.get("pks", []):
            if int(pk) not in by_pk:
                raise SystemExit(f"Selection pk {pk} not found in {manifest_path}")
            selected_records.append(by_pk[int(pk)])

        seen_group: set[int] = set()
        for record in selected_records:
            pk = int(record["pk"])
            if pk in seen_group:
                continue
            seen_group.add(pk)
            source = Path(str(record.get("source_path") or record.get("export_path")))
            export_record = dict(record)
            if not source.exists():
                export_record["missing"] = True
                group_records.append(export_record)
                continue
            dest = dest_dir / selection_dest_name(record)
            if dest in seen_destinations and not dest.exists():
                # Defensive only; filenames include UUIDs, so this should not happen.
                dest = dest_dir / f"{pk}_{dest.name}"
            if not dest.exists():
                shutil.copy2(source, dest)
            seen_destinations.add(dest)
            export_record["selected_group"] = slug
            export_record["selected_description"] = group.get("description", "")
            export_record["selected_export_path"] = str(dest)
            export_record["selected_sha256"] = sha256(dest)
            group_records.append(export_record)

        write_json(dest_dir / "_manifest.json", group_records)
        write_csv(dest_dir / "_manifest.csv", group_records)
        summary.append(
            {
                "slug": slug,
                "description": group.get("description", ""),
                "count": len(group_records),
                "missing": sum(1 for record in group_records if record.get("missing")),
            }
        )

    write_json(selected_dir / "_selection_summary.json", summary)
    write_csv(selected_dir / "_selection_summary.csv", summary)
    print(f"Wrote {sum(item['count'] for item in summary)} selected records into {selected_dir}.")


def iter_known_manifest_paths(out: Path) -> Iterable[Path]:
    for path in (
        out / "_candidates_manifest.json",
        out / "_likely_manifest.json",
    ):
        if path.exists():
            yield path
    reference_root = out / "_reference"
    if reference_root.exists():
        yield from sorted(reference_root.glob("*_manifest.json"))


def metadata_index(out: Path) -> dict[str, dict[str, object]]:
    by_uuid: dict[str, dict[str, object]] = {}
    for manifest in iter_known_manifest_paths(out):
        try:
            records = load_manifest(manifest)
        except Exception:
            continue
        for record in records:
            uuid = str(record.get("uuid", "")).upper()
            if uuid and uuid not in by_uuid:
                by_uuid[uuid] = record
    return by_uuid


def extract_uuid(path: Path) -> str | None:
    match = UUID_RE.search(path.name)
    if not match:
        return None
    return match.group(1).upper()


def media_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file()
        and path.suffix.lower() in MEDIA_SUFFIXES
        and not path.name.startswith(".")
    )


def recatalog_record(path: Path, root: Path, source: dict[str, object] | None) -> dict[str, object]:
    stat = path.stat()
    uuid = extract_uuid(path)
    record: dict[str, object] = {}
    if source:
        record.update(source)
    record.update(
        {
            "folder": path.parent.name,
            "folder_title": title_from_slug(path.parent.name),
            "catalog_path": str(path),
            "relative_path": str(path.relative_to(root)),
            "filename": path.name,
            "suffix": path.suffix.lower(),
            "bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "sha256": sha256(path),
        }
    )
    if uuid:
        record["uuid"] = uuid
    if source:
        record["catalog_source"] = "apple_photos_manifest"
    else:
        record["catalog_source"] = "filesystem"
    return record


def recatalog_root(root: Path, out: Path) -> dict[str, object]:
    by_uuid = metadata_index(out)
    summary: list[dict[str, object]] = []
    selection_groups: list[dict[str, object]] = []
    total = 0
    if not root.exists():
        return {"root": str(root), "count": 0, "folders": 0}
    for folder in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("_")):
        files = media_files(folder)
        if not files:
            continue
        records = [
            recatalog_record(path, root, by_uuid.get(extract_uuid(path) or ""))
            for path in files
        ]
        write_json(folder / "_manifest.json", records)
        write_csv(folder / "_manifest.csv", records)
        uuids = [str(record["uuid"]) for record in records if record.get("uuid")]
        pks = [int(record["pk"]) for record in records if record.get("pk") is not None]
        summary.append(
            {
                "slug": folder.name,
                "title": title_from_slug(folder.name),
                "count": len(records),
                "movies": sum(1 for record in records if str(record.get("suffix", "")).lower() in {".mov", ".mp4", ".m4v"}),
                "missing_metadata": sum(1 for record in records if record.get("catalog_source") == "filesystem"),
            }
        )
        selection_groups.append(
            {
                "slug": folder.name,
                "title": title_from_slug(folder.name),
                "description": f"Filesystem recatalog of {folder.name}.",
                "pks": pks,
                "uuids": uuids,
                "files": [str(path.relative_to(root)) for path in files],
            }
        )
        total += len(records)
    write_json(root / "_selection_summary.json", summary)
    write_csv(root / "_selection_summary.csv", summary)
    write_json(
        root / "_selection.json",
        {
            "source": "filesystem_recatalog",
            "root": str(root),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "groups": selection_groups,
        },
    )
    return {"root": str(root), "count": total, "folders": len(summary)}


def recatalog(args: argparse.Namespace) -> None:
    roots = args.roots
    if not roots:
        roots = [path for path in (args.out / "selected", args.out / "review", args.out / "poems") if path.exists()]
    results = [recatalog_root(root, args.out) for root in roots]
    for result in results:
        print(f"Recataloged {result['count']} files in {result['folders']} folders under {result['root']}.")


def main() -> int:
    args = parse_args()
    if not photos_db(args.library).exists():
        print(f"Photos database not found: {photos_db(args.library)}", file=sys.stderr)
        return 2
    if args.command == "export-reference":
        export_reference(args)
    elif args.command == "export-candidates":
        export_candidates(args)
    elif args.command == "contact-sheets":
        build_contact_sheets(args)
    elif args.command == "export-selection":
        export_selection(args)
    elif args.command == "recatalog":
        recatalog(args)
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
