#!/usr/bin/env python3
"""Heal thumbnail-sized site photos by re-exporting originals from Photos.app.

The archive exporter (photos_stained_glass_archive.py) falls back to Photos'
small derivative when iCloud has evicted the original from disk. This script
finds those stale files and replaces them with the full-size original:

  1. Walks photos/apple-photos-stained-glass/{selected/*,poems} for images
     whose filename embeds a Photos UUID (YYYYMMDD_HHMMSS_<UUID>.jpeg).
  2. Compares on-disk pixel size against the Photos database's recorded size;
     a meaningfully smaller file is a derivative that needs replacing.
  3. Batch-exports the originals via `osxphotos export --download-missing`
     (pulls from iCloud as needed, converts HEIC to JPEG, skips Live videos).
  4. Overwrites each stale file in place (same filename, so site keys and
     R2 upload keys never change) and updates the folder's _manifest.json.
  5. Repairs manifest `filename` fields that don't match the on-disk name.
  6. Clears website-fable/.img-cache when anything changed so the dev image
     server re-renders.

Idempotent: a second run finds nothing to do. Run via `make sync-photos`
from website-fable/ (which also regenerates the site catalog afterwards).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PHOTOS = REPO / "photos"
APPLE = PHOTOS / "apple-photos-stained-glass"
IMG_CACHE = REPO / "website-fable" / ".img-cache"
LIBRARY = Path.home() / "Pictures" / "Photos Library.photoslibrary"

UUID_RE = re.compile(r"_([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})\.\w+$")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# A file is stale when its long edge is under this fraction of the library's
# recorded size (tolerates rounding, catches every real derivative).
STALE_RATIO = 0.98


def image_dims(path: Path) -> tuple[int | None, int | None]:
    """Pixel dimensions from JPEG SOF or PNG IHDR (no dependencies)."""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    if data[:2] != b"\xff\xd8":
        return None, None
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
        else:
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None, None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def site_photo_dirs() -> list[Path]:
    dirs = [APPLE / "poems"]
    selected = APPLE / "selected"
    if selected.is_dir():
        dirs.extend(p for p in sorted(selected.iterdir()) if p.is_dir())
    return [d for d in dirs if d.is_dir()]


def site_images(directory: Path):
    for f in sorted(directory.iterdir()):
        if f.suffix.lower() in IMAGE_SUFFIXES and not f.name.startswith((".", "_")):
            yield f


def library_dims(uuids: list[str]) -> dict[str, tuple[int, int]]:
    db = LIBRARY / "database" / "Photos.sqlite"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: dict[str, tuple[int, int]] = {}
    for i in range(0, len(uuids), 500):
        chunk = uuids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for uuid, w, h in conn.execute(
            f"SELECT ZUUID, ZWIDTH, ZHEIGHT FROM ZASSET WHERE ZUUID IN ({marks})", chunk
        ):
            out[uuid] = (int(w or 0), int(h or 0))
    conn.close()
    return out


def find_osxphotos() -> str:
    exe = shutil.which("osxphotos") or str(Path.home() / ".local" / "bin" / "osxphotos")
    if not Path(exe).exists():
        sys.exit("sync-photos: osxphotos not found (install: uv tool install osxphotos)")
    return exe


def export_originals(uuids: list[str], export_dir: Path) -> None:
    uuid_file = export_dir / "_uuids.txt"
    uuid_file.write_text("\n".join(uuids) + "\n")
    cmd = [
        find_osxphotos(), "export", str(export_dir),
        "--uuid-from-file", str(uuid_file),
        "--download-missing", "--retry", "3",
        "--skip-live", "--convert-to-jpeg", "--jpeg-quality", "0.95",
        "--filename", "{uuid}",
    ]
    print(f"sync-photos: exporting {len(uuids)} originals from Photos "
          "(downloads from iCloud as needed; keep Photos.app responsive)…")
    subprocess.run(cmd, check=True)


def exported_file(export_dir: Path, uuid: str) -> Path | None:
    candidates = [p for p in export_dir.glob(f"{uuid}*")
                  if p.suffix.lower() in (".jpg", ".jpeg") and p.is_file()]
    return max(candidates, key=lambda p: p.stat().st_size) if candidates else None


def update_manifest(directory: Path, updates: dict[str, tuple[int, int, str, int]]) -> None:
    mpath = directory / "_manifest.json"
    if not mpath.exists():
        return
    records = json.loads(mpath.read_text())
    for r in records:
        if r.get("uuid") in updates:
            w, h, digest, nbytes = updates[r["uuid"]]
            r["width"], r["height"] = w, h
            r["sha256"] = digest
            if "bytes" in r:
                r["bytes"] = nbytes
            if "mtime" in r:
                r["mtime"] = datetime.now().isoformat(timespec="seconds")
            r["source_kind"] = "original"
    mpath.write_text(json.dumps(records, indent=2) + "\n")


def repair_manifest_filenames(directory: Path) -> int:
    """Point manifest records at the on-disk (date-prefixed) filenames."""
    mpath = directory / "_manifest.json"
    if not mpath.exists():
        return 0
    on_disk = {}
    for f in site_images(directory):
        m = UUID_RE.search(f.name)
        if m:
            on_disk[m.group(1)] = f.name
    records = json.loads(mpath.read_text())
    fixed = 0
    for r in records:
        uuid = r.get("uuid")
        name = r.get("filename")
        if uuid in on_disk and name != on_disk[uuid] and not (directory / (name or "")).exists():
            r["filename"] = on_disk[uuid]
            fixed += 1
    if fixed:
        mpath.write_text(json.dumps(records, indent=2) + "\n")
        print(f"sync-photos: repaired {fixed} manifest filename(s) in "
              f"{directory.relative_to(PHOTOS)}")
    return fixed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report stale photos without exporting/replacing")
    args = parser.parse_args()

    dirs = site_photo_dirs()
    if not args.dry_run:
        for directory in dirs:
            repair_manifest_filenames(directory)
    files = [(d, f) for d in dirs for f in site_images(d)]

    uuid_to_file: dict[str, Path] = {}
    for _, f in files:
        m = UUID_RE.search(f.name)
        if m:
            uuid_to_file[m.group(1)] = f

    lib = library_dims(list(uuid_to_file))

    stale: list[tuple[str, Path]] = []
    for uuid, f in uuid_to_file.items():
        if uuid not in lib:
            print(f"sync-photos: WARN not in Photos library: {f.relative_to(PHOTOS)}")
            continue
        lw, lh = lib[uuid]
        if not lw or not lh:
            continue
        fw, fh = image_dims(f)
        if fw and max(fw, fh) < max(lw, lh) * STALE_RATIO:
            stale.append((uuid, f))

    print(f"sync-photos: {len(uuid_to_file)} photos checked, {len(stale)} stale")
    if not stale:
        return 0
    if args.dry_run:
        for uuid, f in stale:
            fw, fh = image_dims(f)
            lw, lh = lib[uuid]
            print(f"  stale: {f.relative_to(PHOTOS)} {fw}x{fh} -> {lw}x{lh}")
        return 0

    replaced = 0
    with tempfile.TemporaryDirectory(prefix="sync-photos-") as tmp:
        export_dir = Path(tmp)
        export_originals([u for u, _ in stale], export_dir)

        per_dir: dict[Path, dict[str, tuple[int, int, str, int]]] = {}
        for uuid, dest in stale:
            src = exported_file(export_dir, uuid)
            if src is None:
                print(f"sync-photos: WARN export missing for {dest.relative_to(PHOTOS)}")
                continue
            old_w, old_h = image_dims(dest)
            old_long = max(old_w or 0, old_h or 0)
            new_w, new_h = image_dims(src)
            if not new_w or max(new_w, new_h) <= old_long:
                print(f"sync-photos: WARN export not bigger for {dest.relative_to(PHOTOS)}")
                continue
            shutil.copyfile(src, dest)
            per_dir.setdefault(dest.parent, {})[uuid] = (
                new_w, new_h, sha256(dest), dest.stat().st_size)
            replaced += 1

    for directory, updates in per_dir.items():
        update_manifest(directory, updates)

    if replaced and IMG_CACHE.is_dir():
        for f in IMG_CACHE.iterdir():
            if f.suffix == ".webp":
                f.unlink()
        print("sync-photos: cleared website-fable/.img-cache")

    print(f"sync-photos: replaced {replaced}/{len(stale)} stale photo(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
