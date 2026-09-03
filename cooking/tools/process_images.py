#!/usr/bin/env python3
"""Orient, resize, and pair the exported Purple Carrot photos.

Reads photos/original/, writes:
  images/full/  - max 2000px, q82 (lightbox / OCR source)
  images/card/  - max 900px,  q78 (in-page cards)
  data/pairs.json - manifest: pair number, front/back files, capture times
"""
import glob
import json
import os
import re
import sys

from PIL import Image, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "photos", "original")
EXCLUDE = {"IMG_9301"}  # beach photo shot between the two sessions

# Manual rotation fixes (degrees CCW applied after EXIF transpose), by IMG name
ROTATION_FIX = json.load(open(os.path.join(ROOT, "tools", "rotation_fixes.json"))) if os.path.exists(os.path.join(ROOT, "tools", "rotation_fixes.json")) else {}


def collect():
    photos = {}
    for f in glob.glob(os.path.join(SRC, "*.JPG")) + glob.glob(os.path.join(SRC, "*.jpeg")):
        base = os.path.basename(f)
        m = re.match(r"(\d{8}_\d{6})_(IMG_\d+)(_edited)?\.", base)
        if not m:
            continue
        ts, name, edited = m.groups()
        if name in EXCLUDE:
            continue
        rec = photos.setdefault(name, {"ts": ts, "name": name})
        if edited:
            rec["edited"] = f
        else:
            rec["original"] = f
    return sorted(photos.values(), key=lambda r: (r["ts"], r["name"]))


def main():
    items = collect()
    assert len(items) % 2 == 0, f"odd count: {len(items)}"
    for d in ("images/full", "images/card"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)

    pairs = []
    for i, rec in enumerate(items):
        pair_no = i // 2 + 1
        role = "front" if i % 2 == 0 else "back"
        src = rec.get("edited") or rec["original"]
        img = Image.open(src)
        img = ImageOps.exif_transpose(img)
        deg = ROTATION_FIX.get(rec["name"])
        if deg:
            img = img.rotate(deg, expand=True)
        img = img.convert("RGB")
        stem = f"{pair_no:03d}{'a' if role == 'front' else 'b'}_{rec['name']}"
        for sub, maxpx, q in (("full", 2000, 82), ("card", 900, 78)):
            out = os.path.join(ROOT, "images", sub, stem + ".jpg")
            im2 = img.copy()
            im2.thumbnail((maxpx, maxpx))
            im2.save(out, "JPEG", quality=q, optimize=True, progressive=True)
        # capture time from sidecar
        sidecar = rec["original"] + ".json"
        date = None
        if os.path.exists(sidecar):
            meta = json.load(open(sidecar))
            if isinstance(meta, list):
                meta = meta[0] if meta else {}
            date = meta.get("date")
        if role == "front":
            pairs.append({"pair": pair_no, "front": stem + ".jpg", "front_img": rec["name"], "date": date})
        else:
            pairs[-1]["back"] = stem + ".jpg"
            pairs[-1]["back_img"] = rec["name"]
        if i % 50 == 0:
            print(f"{i}/{len(items)}", flush=True)

    with open(os.path.join(ROOT, "data", "pairs.json"), "w") as f:
        json.dump(pairs, f, indent=1)
    print(f"done: {len(items)} images, {len(pairs)} pairs")


if __name__ == "__main__":
    sys.exit(main())
