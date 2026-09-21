#!/usr/bin/env python3
"""Archive site photos by their review-overlay number.

    tools/archive_photos.py 15 18 20 22 [--dry-run]

Numbers are the labels drawn on every strip item by src/data/duplicates.json
(`labels`: key → number). For each one this moves the photo (and a clip's
.mp4) out of the photo tree into drop-zones/_imported/_deleted/<folder>/ —
never deleted — removes its manifest row, and drops the key from pins.json,
construction.json and drawings.json. The numbering is left untouched so the
remaining numbers keep meaning what they did on screen. Then `make catalog`
in website/ and deploy.

Scanned "then" photos (photos/scanned/…) have no manifest; the file move is
enough, since those folders are cataloged by directory listing.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHOTOS = ROOT / "photos"
DATA = ROOT / "website" / "src" / "data"
TRASH = ROOT / "drop-zones" / "_imported" / "_deleted"
DUPES = DATA / "duplicates.json"


def load(p: Path) -> dict:
    return json.loads(p.read_text())


def save(p: Path, doc: dict) -> None:
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("numbers", nargs="+", help="overlay numbers to archive")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dupes = load(DUPES)
    by_label = {v: k for k, v in dupes.get("labels", {}).items()}
    pins = load(DATA / "pins.json")
    construction = load(DATA / "construction.json")
    drawings = load(DATA / "drawings.json")
    archived = dupes.setdefault("archived", [])
    moved: list[str] = []
    for num in args.numbers:
        num = num.lstrip("#")
        key = by_label.get(num)
        if not key:
            print(f"#{num}: no such number in the overlay", file=sys.stderr)
            continue
        if any(a["key"] == key for a in archived):
            print(f"#{num}: already archived ({key})")
            continue
        src = PHOTOS / key
        folder = src.parent
        files = [src]
        rows = None
        manifest = folder / "_manifest.json"
        if manifest.exists():
            rows = json.loads(manifest.read_text())
            row = next((r for r in rows if r["filename"] == src.name), None)
            if row and row.get("video"):
                files.append(folder / row["video"])
            rows = [r for r in rows if r["filename"] != src.name]
        missing = [f for f in files if not f.exists()]
        if missing:
            print(f"#{num}: missing on disk: {', '.join(str(m) for m in missing)}", file=sys.stderr)
            continue
        dest = TRASH / folder.relative_to(PHOTOS)
        print(f"#{num}: {key}" + (" (+ clip)" if len(files) > 1 else ""))
        if args.dry_run:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.move(str(f), str(dest / f.name))
            moved.append(str(f.relative_to(ROOT)))
        if rows is not None:
            manifest.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
        for slug, p in pins.items():
            if isinstance(p, dict):
                for side in ("first", "last"):
                    if key in p.get(side, []):
                        p[side].remove(key)
            elif isinstance(p, list) and key in p:
                p.remove(key)
        for doc in (construction, drawings):
            if key in doc.get("keys", []):
                doc["keys"].remove(key)
        archived.append({"label": num, "key": key, "when": datetime.now().strftime("%Y-%m-%d")})
    if not args.dry_run and moved:
        save(DATA / "pins.json", pins)
        save(DATA / "construction.json", construction)
        save(DATA / "drawings.json", drawings)
        save(DUPES, dupes)
        (TRASH / "moved.log").open("a").write("\n".join(moved) + "\n")
        print(f"\narchived {len(moved)} files → drop-zones/_imported/_deleted/  (numbering unchanged)")
        print("next: cd website && make catalog && make deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
