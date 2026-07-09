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
import io
import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps


def srgb_profile_bytes():
    try:
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        return profile.tobytes()
    except Exception:
        return None


SRGB_PROFILE = srgb_profile_bytes()


def load_scan(path):
    """Load scanner output as BGR pixels after converting its ICC profile to sRGB."""
    try:
        im = Image.open(path)
        im = ImageOps.exif_transpose(im)
        icc = im.info.get("icc_profile")
        if icc:
            source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(
                im.convert("RGB"),
                source_profile,
                ImageCms.createProfile("sRGB"),
                outputMode="RGB",
            )
        else:
            im = im.convert("RGB")
        return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    except Exception as e:
        print(f"WARN: color-managed load failed ({e}); falling back to OpenCV", file=sys.stderr)
        return cv2.imread(path, cv2.IMREAD_COLOR)


def write_jpeg(path, bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    im = Image.fromarray(rgb)
    save_kwargs = {"quality": 95}
    if SRGB_PROFILE:
        save_kwargs["icc_profile"] = SRGB_PROFILE
    im.save(path, "JPEG", **save_kwargs)


def rotate_crop(crop, degrees):
    degrees = degrees % 360
    if degrees == 0:
        return crop
    if degrees == 90:
        return cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(crop, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError("--rotate must be one of 0, 90, 180, 270")


def split_oversized_crop(crop, max_aspect=2.2, target_aspect=1.5, seam_trim_frac=0.02):
    """Split an over-wide/over-tall crop that likely contains multiple prints."""
    h, w = crop.shape[:2]
    if h <= 0 or w <= 0:
        return [crop]

    aspect = w / h
    if aspect > max_aspect:
        count = max(2, min(6, int(round(aspect / target_aspect))))
        xs = [round(i * w / count) for i in range(count + 1)]
        trim = max(4, int(round(w * seam_trim_frac)))
        pieces = []
        for i in range(count):
            left = xs[i] + (trim if i > 0 else 0)
            right = xs[i + 1] - (trim if i < count - 1 else 0)
            if right > left:
                pieces.append(crop[:, left:right])
        return pieces

    inv_aspect = h / w
    if inv_aspect > max_aspect:
        count = max(2, min(6, int(round(inv_aspect / target_aspect))))
        ys = [round(i * h / count) for i in range(count + 1)]
        trim = max(4, int(round(h * seam_trim_frac)))
        pieces = []
        for i in range(count):
            top = ys[i] + (trim if i > 0 else 0)
            bottom = ys[i + 1] - (trim if i < count - 1 else 0)
            if bottom > top:
                pieces.append(crop[top:bottom, :])
        return pieces

    return [crop]


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
    box = cv2.boxPoints(rect).astype(np.float32)

    # OpenCV does not guarantee a useful start corner for boxPoints(), and
    # minAreaRect() may swap width/height for portrait prints. Recompute the
    # output rectangle from ordered corners so portrait photos stay portrait.
    sums = box.sum(axis=1)
    diffs = np.diff(box, axis=1).reshape(-1)
    ordered = np.array([
        box[np.argmin(sums)],   # top-left
        box[np.argmin(diffs)],  # top-right
        box[np.argmax(sums)],   # bottom-right
        box[np.argmax(diffs)],  # bottom-left
    ], dtype=np.float32)

    tl, tr, br, bl = ordered
    width_top = np.linalg.norm(tr - tl)
    width_bottom = np.linalg.norm(br - bl)
    height_right = np.linalg.norm(br - tr)
    height_left = np.linalg.norm(bl - tl)
    w_i = max(1, int(round(max(width_top, width_bottom))))
    h_i = max(1, int(round(max(height_right, height_left))))

    dst = np.array([[0, 0], [w_i - 1, 0], [w_i - 1, h_i - 1], [0, h_i - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(ordered, dst)
    out = cv2.warpPerspective(img, M, (w_i, h_i))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("outdir")
    ap.add_argument("--min-area-frac", type=float, default=0.012,
                    help="min photo area as fraction of full bed (default 0.012)")
    ap.add_argument("--pad-frac", type=float, default=0.0)
    ap.add_argument("--rotate", type=int, default=0,
                    help="rotate each emitted crop clockwise by 0, 90, 180, or 270 degrees")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    if args.rotate % 360 not in (0, 90, 180, 270):
        print("ERROR: --rotate must be one of 0, 90, 180, 270", file=sys.stderr)
        sys.exit(2)

    img = load_scan(args.scan)
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
    for crop in crops:
        crop = rotate_crop(crop, args.rotate)
        for piece in split_oversized_crop(crop):
            out_path = os.path.join(args.outdir, f"{base}_{len(written) + 1:03d}.jpg")
            write_jpeg(out_path, piece)
            written.append(out_path)
            print(out_path)

    print(f"# {len(written)} crop(s) from {args.scan}", file=sys.stderr)


if __name__ == "__main__":
    main()
