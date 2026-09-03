#!/usr/bin/env python3
"""Download each NYT recipe's photo (from data/nyt/raw/<id>.json) into
images/full/nyt_<id>.jpg (≤2000px, lightbox) and images/card/nyt_<id>.jpg
(900px, grid cover + detail photo). Skips ids that already have both files,
and ids listed in data/nyt/excluded.txt.
Prefers the 3:2 "superJumbo" rendition, else the widest available.
"""
import io, json, glob, os, sys, time, urllib.request
from PIL import Image, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "nyt", "raw")
FULL = os.path.join(ROOT, "images", "full")
CARD = os.path.join(ROOT, "images", "card")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def pick(images):
    if isinstance(images, dict):
        images = [images]
    objs = [i for i in images if isinstance(i, dict) and i.get("contentUrl")]
    if not objs:
        urls = [i for i in images if isinstance(i, str)]
        return urls[0] if urls else None
    for o in objs:
        if str(o.get("@id", "")).endswith("#superJumbo"):
            return o["contentUrl"]
    return max(objs, key=lambda o: int(o.get("width") or 0))["contentUrl"]


def save(img, path, max_w, q):
    img = img.copy()
    if img.width > max_w:
        img = img.resize((max_w, round(img.height * max_w / img.width)), Image.LANCZOS)
    img.save(path, "JPEG", quality=q, optimize=True, progressive=True)


def excluded_ids():
    f = os.path.join(ROOT, "data", "nyt", "excluded.txt")
    if not os.path.exists(f):
        return set()
    return {line.split("#", 1)[0].strip() for line in open(f)} - {""}


def main():
    files = sorted(glob.glob(os.path.join(RAW, "*.json")))
    skip = excluded_ids()
    n_ok = n_skip = n_fail = 0
    for f in files:
        rid = os.path.basename(f)[:-5]
        if rid in skip:
            continue
        full = os.path.join(FULL, f"nyt_{rid}.jpg")
        card = os.path.join(CARD, f"nyt_{rid}.jpg")
        if os.path.exists(full) and os.path.exists(card):
            n_skip += 1
            continue
        ld = json.load(open(f))
        url = pick(ld.get("image") or [])
        if not url:
            print(f"NO IMAGE {rid} {ld.get('name')}")
            n_fail += 1
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
            save(img, full, 2000, 82)
            save(img, card, 900, 84)
            print(f"ok {rid} {img.width}x{img.height} {ld.get('name')}", flush=True)
            n_ok += 1
        except Exception as e:
            print(f"FAIL {rid} {e}", flush=True)
            n_fail += 1
        time.sleep(0.3)
    print(f"done: {n_ok} fetched, {n_skip} skipped, {n_fail} failed")


if __name__ == "__main__":
    main()
