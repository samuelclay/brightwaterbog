#!/usr/bin/env python3
"""Detect and crop individual photos from a flatbed scan.

A flatbed scan of loose photos is one big image of a white bed with one or
more photos lying on it. This finds each photo as a non-white rectangular
region, deskews it, and writes each as its own file.

Usage:
    crop.py SCAN.tiff OUTDIR [--min-area-frac 0.01] [--debug]

Writes OUTDIR/<scanbasename>_001.jpg, _002.jpg, ... and prints each path.
Single-photo or full-page scans come out as one crop.
"""
import sys
import os
import argparse
import cv2
import numpy as np


def find_photos(img, min_area_frac, pad_frac=0.0):
    """Return a list of rotated-rect crops (deskewed BGR images), largest first."""
    h, w = img.shape[:2]
    bed_area = h * w

    # Non-white mask: a pixel belongs to a photo if it's darker than the bed
    # OR colorful. The scanner bed reads as near-white (~245-255, low saturation).
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    sat = hsv[:, :, 1]
    nonwhite = ((value < 235) | (sat > 40)).astype(np.uint8) * 255

    # Close gaps (bright skies / white photo borders) so each photo is one blob.
    k = max(3, int(min(h, w) * 0.01) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    closed = cv2.morphologyEx(nonwhite, cv2.MORPH_CLOSE, kernel, iterations=2)
    closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    crops = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < bed_area * min_area_frac:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (rw, rh), angle = rect
        if rw < 20 or rh < 20:
            continue
        # Optional padding outward.
        if pad_frac:
            rw *= (1 + pad_frac)
            rh *= (1 + pad_frac)
        crops.append((area, ((cx, cy), (rw, rh), angle)))

    crops.sort(key=lambda t: -t[0])
    return [deskew_crop(img, rect) for _, rect in crops]


def deskew_crop(img, rect):
    """Rotate the image so the rect is axis-aligned, then crop it out."""
    (cx, cy), (rw, rh), angle = rect
    box = cv2.boxPoints(rect).astype(np.float32)
    w_i, h_i = int(round(rw)), int(round(rh))
    dst = np.array([[0, h_i - 1], [0, 0], [w_i - 1, 0], [w_i - 1, h_i - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(box, dst)
    out = cv2.warpPerspective(img, M, (w_i, h_i))
    # Portrait-orient very wide crops only if clearly landscape paper; leave as-is otherwise.
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("outdir")
    ap.add_argument("--min-area-frac", type=float, default=0.012,
                    help="min photo area as fraction of full bed (default 0.012)")
    ap.add_argument("--pad-frac", type=float, default=0.0)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    img = cv2.imread(args.scan, cv2.IMREAD_COLOR)
    if img is None:
        print(f"ERROR: could not read {args.scan}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.scan))[0]
    crops = find_photos(img, args.min_area_frac, args.pad_frac)

    if not crops:
        # Nothing detected — treat the whole scan as one image (trim pure-white margins).
        crops = [img]
        print(f"WARN: no distinct photos detected; emitting full scan", file=sys.stderr)

    written = []
    for i, crop in enumerate(crops, 1):
        out_path = os.path.join(args.outdir, f"{base}_{i:03d}.jpg")
        cv2.imwrite(out_path, crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
        written.append(out_path)
        print(out_path)

    print(f"# {len(written)} crop(s) from {args.scan}", file=sys.stderr)


if __name__ == "__main__":
    main()
