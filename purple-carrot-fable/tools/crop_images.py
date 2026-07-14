#!/usr/bin/env python3
"""Crop hero and step photos out of the card images using agent-mapped boxes.

Reads data/crops/NNN.json ({id, hero: [x0,y0,x1,y1] fractions, steps:[{n,box}]}),
crops from images/full/, writes images/hero/NNN.jpg and images/steps/NNN_n.jpg.
"""
import glob
import json
import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def full_path(prefix):
    m = glob.glob(os.path.join(ROOT, "images", "full", prefix + "_*.jpg"))
    return m[0] if m else None


def source_images(cid, pairs):
    """Return (hero_source, steps_source) full-image paths for a crop id."""
    if cid in pairs:
        return full_path(f"{cid:03d}a"), full_path(f"{cid:03d}b")
    # standalone single-page recipes: both crops come from the pair's b image
    b = {128: "015b", 129: "025b", 130: "059b", 131: "102b"}[cid]
    p = full_path(b)
    return p, p


def crop(img, box):
    w, h = img.size
    x0, y0, x1, y1 = box
    px = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
    if px[2] - px[0] < 20 or px[3] - px[1] < 20:
        return None
    return img.crop(px)


def save(img, path, maxw, q):
    if img.width > maxw:
        img = img.resize((maxw, round(img.height * maxw / img.width)), Image.LANCZOS)
    img.save(path, "JPEG", quality=q, optimize=True, progressive=True)


def main():
    pairs = {p["pair"] for p in json.load(open(os.path.join(ROOT, "data", "pairs.json")))}
    n_hero = n_step = 0
    problems = []
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "crops", "*.json"))):
        d = json.load(open(f))
        cid = d["id"]
        hero_src, steps_src = source_images(cid, pairs)
        if d.get("hero") and hero_src:
            c = crop(Image.open(hero_src), d["hero"])
            if c:
                save(c, os.path.join(ROOT, "images", "hero", f"{cid:03d}.jpg"), 900, 78)
                n_hero += 1
            else:
                problems.append(f"{cid}: degenerate hero box")
        for s in d.get("steps") or []:
            if not steps_src:
                continue
            c = crop(Image.open(steps_src), s["box"])
            if c:
                save(c, os.path.join(ROOT, "images", "steps", f"{cid:03d}_{s['n']}.jpg"), 560, 74)
                n_step += 1
            else:
                problems.append(f"{cid}: degenerate step {s['n']} box")
    print(f"wrote {n_hero} hero crops, {n_step} step crops")
    for p in problems:
        print("PROBLEM", p)


if __name__ == "__main__":
    main()
