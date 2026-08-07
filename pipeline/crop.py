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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Binding:
    """A detected sketchbook binding and its page-side boundary."""

    side: str
    cut: int
    score: float


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


def analysis_image(img: np.ndarray, max_side: int = 1400) -> tuple[np.ndarray, float]:
    """Return a consistently sized image for resolution-independent detection."""
    h, w = img.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale == 1.0:
        return img.copy(), scale
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), scale


def detect_binding(img: np.ndarray) -> Binding | None:
    """Detect a dark spiral/comb binding along one edge of a scanned page.

    A binding is both unusually dark across an outer band and several percent
    deep. Requiring both signals avoids treating an ordinary scanner-bed border
    or a dark mark near an edge as a bound page.
    """
    small, _ = analysis_image(img)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    band_x = max(12, round(w * 0.12))
    band_y = max(12, round(h * 0.12))
    trim_x = max(2, round(w * 0.015))
    trim_y = max(2, round(h * 0.015))
    strong_dark = gray < 170

    # Some spiral bindings are a thin comb of black holes rather than a broad
    # dark strip. Count the repeated short marks in the outermost sliver before
    # comparing broad edge darkness; a page shadow can otherwise outscore the
    # physically much narrower binding.
    comb_dark = gray < 120
    strip_x = max(4, round(w * 0.012))
    strip_y = max(4, round(h * 0.012))
    comb_signals = {
        "left": comb_dark[:, :strip_x].mean(axis=1),
        "right": comb_dark[:, w - strip_x :].mean(axis=1),
        "top": comb_dark[:strip_y, :].mean(axis=0),
        "bottom": comb_dark[h - strip_y :, :].mean(axis=0),
    }
    comb_counts: dict[str, int] = {}
    for edge, signal in comb_signals.items():
        active = signal > 0.10
        changes = np.diff(np.r_[False, active, False].astype(np.int8))
        starts = np.flatnonzero(changes == 1)
        ends = np.flatnonzero(changes == -1)
        max_run = max(4, round(len(signal) * 0.04))
        comb_counts[edge] = sum(2 <= end - start <= max_run for start, end in zip(starts, ends))

    comb_ranked = sorted(comb_counts.items(), key=lambda item: item[1], reverse=True)
    comb_side, comb_count = comb_ranked[0]
    comb_runner_up = comb_ranked[1][1]
    if comb_count >= 8 and comb_count >= comb_runner_up + 5:
        if comb_side in {"left", "right"}:
            profile = comb_dark.mean(axis=0)
            axis_size = w
        else:
            profile = comb_dark.mean(axis=1)
            axis_size = h
        window = max(7, round(axis_size * 0.014))
        if window % 2 == 0:
            window += 1
        smooth = np.convolve(profile, np.ones(window) / window, mode="same")
        edge_depth = round(axis_size * 0.12)
        if comb_side in {"left", "top"}:
            active = smooth[:edge_depth] > 0.04
            inactive = np.flatnonzero(~active)
            cut = int(inactive.min()) if len(inactive) else edge_depth
            binding_depth = cut
        else:
            offset = axis_size - edge_depth
            active = smooth[offset:] > 0.04
            inactive = np.flatnonzero(~active)
            cut = int(offset + inactive.max() + 1) if len(inactive) else offset
            binding_depth = axis_size - cut
        if axis_size * 0.005 <= binding_depth <= axis_size * 0.12:
            return Binding(side=comb_side, cut=cut, score=comb_count / len(comb_signals[comb_side]))

    scores = {
        "left": float(strong_dark[trim_y : h - trim_y, :band_x].mean()),
        "right": float(strong_dark[trim_y : h - trim_y, w - band_x :].mean()),
        "top": float(strong_dark[:band_y, trim_x : w - trim_x].mean()),
        "bottom": float(strong_dark[h - band_y :, trim_x : w - trim_x].mean()),
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    side, score = ranked[0]
    runner_up = ranked[1][1]
    min_score = 0.14 if side in {"top", "bottom"} else 0.065
    if score < min_score or score < max(0.02, runner_up * 1.8):
        return None

    if side in {"left", "right"}:
        profile = strong_dark.mean(axis=0)
        axis_size = w
    else:
        profile = strong_dark.mean(axis=1)
        axis_size = h
    window = max(7, round(axis_size * 0.014))
    if window % 2 == 0:
        window += 1
    smooth = np.convolve(profile, np.ones(window) / window, mode="same")
    edge_depth = round(axis_size * 0.18)
    if side in {"left", "top"}:
        candidates = np.flatnonzero(smooth[:edge_depth] > 0.075)
        if not len(candidates):
            return None
        cut = int(candidates.max() + 1)
        binding_depth = cut
    else:
        offset = axis_size - edge_depth
        candidates = np.flatnonzero(smooth[offset:] > 0.075)
        if not len(candidates):
            return None
        cut = int(offset + candidates.min())
        binding_depth = axis_size - cut
    max_depth_fraction = 0.10 if side in {"top", "bottom"} else 0.18
    if not axis_size * 0.02 <= binding_depth <= axis_size * max_depth_fraction:
        return None
    return Binding(side=side, cut=cut, score=score)


def major_sketch_boxes(
    img: np.ndarray,
    binding: Binding | None = None,
) -> list[tuple[int, int, int, int]]:
    """Find major drawings while rejecting shadows, notes, and stray marks.

    Very dark strokes establish high-confidence drawing seeds. Lighter pencil
    strokes are then assigned to the nearest seed, but only across a limited
    distance. This lets faint outlines grow around a drawing without allowing a
    tiny doodle or paper shadow to become another result.
    """
    small, scale = analysis_image(img)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    margin = max(8, round(min(h, w) * 0.015))
    x1, y1, x2, y2 = margin, margin, w - margin, h - margin
    crop_left, crop_top, crop_right, crop_bottom = 0, 0, w, h
    if binding:
        if binding.side == "left":
            x1 = max(x1, binding.cut)
            crop_left = binding.cut
        elif binding.side == "right":
            x2 = min(x2, binding.cut)
            crop_right = binding.cut
        elif binding.side == "top":
            y1 = max(y1, binding.cut)
            crop_top = binding.cut
        else:
            y2 = min(y2, binding.cut)
            crop_bottom = binding.cut
    if x2 <= x1 or y2 <= y1:
        return []

    valid = np.zeros_like(gray, dtype=np.uint8)
    valid[y1:y2, x1:x2] = 1

    # Broad shadows disappear from the locally flattened pencil-stroke mask.
    sigma = max(9.0, min(h, w) * 0.025)
    background = cv2.GaussianBlur(gray, (0, 0), sigma)
    local_darkness = cv2.subtract(background, gray)
    soft = ((((local_darkness > 15) & (gray < 242)) | (gray < 175)) & (valid > 0)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(soft)
    clean = np.zeros_like(soft)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= 3:
            clean[labels == label] = 1
    if not np.any(clean):
        return []

    # A mild dilation connects the darkest lines within each real drawing. It
    # deliberately remains much smaller than the whitespace between studies.
    core = ((gray < 120) & (valid > 0)).astype(np.uint8)
    core_join = max(9, round(min(h, w) * 0.013))
    if core_join % 2 == 0:
        core_join += 1
    core_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (core_join, core_join))
    joined_core = cv2.dilate(core, core_kernel)
    count, core_labels, core_stats, _ = cv2.connectedComponentsWithStats(joined_core)
    candidates: list[tuple[int, int]] = []
    for label in range(1, count):
        core_pixels = int(np.count_nonzero(core[core_labels == label]))
        if core_pixels >= 40:
            candidates.append((core_pixels, label))
    if not candidates:
        return []
    candidates.sort(reverse=True)
    strongest = candidates[0][0]
    min_seed_pixels = max(80, round(strongest * 0.12))
    seeds = [(pixels, label) for pixels, label in candidates if pixels >= min_seed_pixels][:6]

    # A single outlined drawing can produce nested dark-core components (for
    # example, its outer contour and an interior diagonal). Treat substantially
    # overlapping core bounds as one seed while preserving genuinely separate
    # studies elsewhere on the page.
    distinct_seeds: list[tuple[int, int]] = []
    for pixels, label in seeds:
        x, y, width, height, _ = core_stats[label]
        area = int(width * height)
        duplicate = False
        for _, kept_label in distinct_seeds:
            kept_x, kept_y, kept_width, kept_height, _ = core_stats[kept_label]
            intersection_width = max(0, min(x + width, kept_x + kept_width) - max(x, kept_x))
            intersection_height = max(0, min(y + height, kept_y + kept_height) - max(y, kept_y))
            intersection = int(intersection_width * intersection_height)
            kept_area = int(kept_width * kept_height)
            if intersection / max(1, min(area, kept_area)) >= 0.65:
                duplicate = True
                break
        if not duplicate:
            distinct_seeds.append((pixels, label))
    seeds = distinct_seeds

    # Weak pencil pages do not have reliable dark cores; the softer legacy
    # clustering is safer for those scans.
    min_confident_core = max(250, round(h * w * 0.0008))
    if len(seeds) < 2 or strongest < min_confident_core:
        return []

    distances = []
    for _, label in seeds:
        seed_mask = (core_labels == label).astype(np.uint8)
        distances.append(cv2.distanceTransform(1 - seed_mask, cv2.DIST_L2, 5))
    distance_stack = np.stack(distances)
    assignments = np.argmin(distance_stack, axis=0)
    nearest = np.min(distance_stack, axis=0)
    max_growth = round(min(h, w) * 0.12)
    # Sketch lines often continue faintly beyond their dark core. A generous
    # margin prevents those ends from touching the crop edge; the rejection mask
    # below removes any neighboring drawing that enters this extra space.
    extra_pad = max(12, round(min(h, w) * 0.04))

    ordered_boxes: list[tuple[int, int, tuple[int, int, int, int]]] = []
    full_h, full_w = img.shape[:2]
    for index, (_, label) in enumerate(seeds):
        region = (clean > 0) & (assignments == index) & (nearest <= max_growth)
        ys, xs = np.nonzero(region)
        if not len(xs):
            continue
        crop_x1 = max(crop_left, int(xs.min()) - extra_pad)
        crop_y1 = max(crop_top, int(ys.min()) - extra_pad)
        crop_x2 = min(crop_right, int(xs.max()) + extra_pad + 1)
        crop_y2 = min(crop_bottom, int(ys.max()) + extra_pad + 1)
        left = max(0, int(np.floor(crop_x1 / scale)))
        top = max(0, int(np.floor(crop_y1 / scale)))
        right = min(full_w, int(np.ceil(crop_x2 / scale)))
        bottom = min(full_h, int(np.ceil(crop_y2 / scale)))
        if right <= left or bottom <= top:
            continue

        seed_x, seed_y = int(core_stats[label, cv2.CC_STAT_LEFT]), int(core_stats[label, cv2.CC_STAT_TOP])
        ordered_boxes.append((seed_y, seed_x, (left, top, right, bottom)))

    ordered_boxes.sort(key=lambda item: (item[0], item[1]))
    return [box for _, _, box in ordered_boxes]


def box_intersection_area(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> int:
    """Return the pixel area shared by two axis-aligned boxes."""
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    return max(0, right - left) * max(0, bottom - top)


def deduplicate_contained_boxes(
    boxes: list[tuple[int, int, int, int]],
    threshold: float = 0.85,
) -> list[tuple[int, int, int, int]]:
    """Drop boxes that are almost entirely contained by a stronger region."""
    by_area = sorted(
        boxes,
        key=lambda box: (box[2] - box[0]) * (box[3] - box[1]),
        reverse=True,
    )
    kept: list[tuple[int, int, int, int]] = []
    for box in by_area:
        area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
        if any(box_intersection_area(box, other) / area >= threshold for other in kept):
            continue
        kept.append(box)
    kept.sort(key=lambda box: (box[1], box[0]))
    return kept


def lowest_ink_seam(
    gray: np.ndarray,
    axis: str,
    start: int,
    end: int,
    span_start: int,
    span_end: int,
) -> int:
    """Choose the quietest row or column inside an overlap as its boundary."""
    if end <= start + 1:
        return max(start, min(end, (start + end) // 2))
    inset = max(0, round((end - start) * 0.12))
    search_start = min(end - 1, start + inset)
    search_end = max(search_start + 1, end - inset)
    if axis == "y":
        region = gray[search_start:search_end, span_start:span_end]
        density = (region < 225).mean(axis=1) if region.size else np.array([])
    else:
        region = gray[span_start:span_end, search_start:search_end]
        density = (region < 225).mean(axis=0) if region.size else np.array([])
    if not density.size:
        return (start + end) // 2
    window = max(3, min(21, round(len(density) * 0.08)))
    if window % 2 == 0:
        window += 1
    smoothed = np.convolve(density, np.ones(window) / window, mode="same")
    return search_start + int(np.argmin(smoothed))


def non_overlapping_sketch_boxes(
    img: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Deduplicate regions and partition remaining overlaps at low-ink seams."""
    boxes = deduplicate_contained_boxes(boxes)
    if len(boxes) < 2:
        return boxes

    small, scale = analysis_image(img)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    scaled = [
        [
            max(0, int(np.floor(left * scale))),
            max(0, int(np.floor(top * scale))),
            min(gray.shape[1], int(np.ceil(right * scale))),
            min(gray.shape[0], int(np.ceil(bottom * scale))),
        ]
        for left, top, right, bottom in boxes
    ]

    for first_index in range(len(scaled)):
        for second_index in range(first_index + 1, len(scaled)):
            first = scaled[first_index]
            second = scaled[second_index]
            overlap_left = max(first[0], second[0])
            overlap_top = max(first[1], second[1])
            overlap_right = min(first[2], second[2])
            overlap_bottom = min(first[3], second[3])
            if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
                continue

            first_center_x = (first[0] + first[2]) / 2
            first_center_y = (first[1] + first[3]) / 2
            second_center_x = (second[0] + second[2]) / 2
            second_center_y = (second[1] + second[3]) / 2
            if abs(second_center_y - first_center_y) >= abs(second_center_x - first_center_x):
                seam = lowest_ink_seam(
                    gray,
                    "y",
                    overlap_top,
                    overlap_bottom,
                    min(first[0], second[0]),
                    max(first[2], second[2]),
                )
                upper, lower = (first, second) if first_center_y <= second_center_y else (second, first)
                upper[3] = min(upper[3], seam)
                lower[1] = max(lower[1], seam)
            else:
                seam = lowest_ink_seam(
                    gray,
                    "x",
                    overlap_left,
                    overlap_right,
                    min(first[1], second[1]),
                    max(first[3], second[3]),
                )
                left_box, right_box = (first, second) if first_center_x <= second_center_x else (second, first)
                left_box[2] = min(left_box[2], seam)
                right_box[0] = max(right_box[0], seam)

    full_h, full_w = img.shape[:2]
    results: list[tuple[int, int, int, int]] = []
    for left, top, right, bottom in scaled:
        full_box = (
            max(0, int(round(left / scale))),
            max(0, int(round(top / scale))),
            min(full_w, int(round(right / scale))),
            min(full_h, int(round(bottom / scale))),
        )
        if full_box[2] > full_box[0] and full_box[3] > full_box[1]:
            results.append(full_box)
    results.sort(key=lambda box: (box[1], box[0]))
    return results


def sketch_boxes(
    img: np.ndarray,
    binding: Binding | None = None,
) -> list[tuple[int, int, int, int]]:
    """Return padded full-resolution boxes around meaningful stroke clusters."""
    small, scale = analysis_image(img)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    margin = max(8, round(min(h, w) * 0.015))

    x1, y1, x2, y2 = margin, margin, w - margin, h - margin
    crop_left, crop_top, crop_right, crop_bottom = 0, 0, w, h
    if binding:
        if binding.side == "left":
            x1 = max(x1, binding.cut)
            crop_left = binding.cut
        elif binding.side == "right":
            x2 = min(x2, binding.cut)
            crop_right = binding.cut
        elif binding.side == "top":
            y1 = max(y1, binding.cut)
            crop_top = binding.cut
        else:
            y2 = min(y2, binding.cut)
            crop_bottom = binding.cut
    if x2 <= x1 or y2 <= y1:
        return []

    # Flatten slow paper/shadow variation, then keep narrow dark strokes. The
    # absolute-dark fallback retains dense ink even when a broad drawing affects
    # the local background estimate.
    sigma = max(9.0, min(h, w) * 0.025)
    background = cv2.GaussianBlur(gray, (0, 0), sigma)
    local_darkness = cv2.subtract(background, gray)
    strokes = (((local_darkness > 15) & (gray < 242)) | (gray < 175)).astype(np.uint8) * 255
    valid = np.zeros_like(strokes)
    valid[y1:y2, x1:x2] = 255
    strokes = cv2.bitwise_and(strokes, valid)

    # Dust and paper freckles are isolated; actual pencil marks contain several
    # adjacent pixels even after the scan is reduced to the analysis size.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(strokes)
    clean = np.zeros_like(strokes)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= 3:
            clean[labels == label] = 255
    if not np.any(clean):
        return []

    # Join neighboring strokes into drawings without bridging the page-scale
    # whitespace between separate sketches. The dilation also supplies a useful
    # white margin around every emitted crop.
    cluster_size = max(25, round(min(h, w) * 0.055))
    if cluster_size % 2 == 0:
        cluster_size += 1
    cluster_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cluster_size, cluster_size))
    clusters = cv2.dilate(clean, cluster_kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(clusters)
    min_stroke_pixels = max(80, round(h * w * 0.00045))
    candidates: list[tuple[int, int, int, int, int]] = []
    for label in range(1, count):
        stroke_pixels = int(np.count_nonzero(clean[labels == label]))
        if stroke_pixels < min_stroke_pixels:
            continue
        x, y, width, height, _ = stats[label]
        candidates.append((stroke_pixels, int(x), int(y), int(width), int(height)))

    # When a page contains one dominant drawing, the book edge and a short date
    # can still join into a weak page-height component. Keep meaningful secondary
    # studies, but discard clusters with less than a tenth of the dominant ink.
    strongest = max((candidate[0] for candidate in candidates), default=0)
    relative_floor = round(strongest * 0.10)
    boxes = [
        (x, y, width, height)
        for stroke_pixels, x, y, width, height in candidates
        if stroke_pixels >= relative_floor
    ]

    # Natural page order makes a scan's results predictable in the gallery.
    boxes.sort(key=lambda box: (box[1], box[0]))
    full_boxes: list[tuple[int, int, int, int]] = []
    full_h, full_w = img.shape[:2]
    extra_pad = max(6, round(min(h, w) * 0.012))
    for x, y, width, height in boxes:
        crop_x1 = max(crop_left, x - extra_pad)
        crop_y1 = max(crop_top, y - extra_pad)
        crop_x2 = min(crop_right, x + width + extra_pad)
        crop_y2 = min(crop_bottom, y + height + extra_pad)
        left = max(0, int(np.floor(crop_x1 / scale)))
        top = max(0, int(np.floor(crop_y1 / scale)))
        right = min(full_w, int(np.ceil(crop_x2 / scale)))
        bottom = min(full_h, int(np.ceil(crop_y2 / scale)))
        if right > left and bottom > top:
            full_boxes.append((left, top, right, bottom))
    return full_boxes


def find_sketches(img: np.ndarray, binding: Binding | None = None) -> list[np.ndarray]:
    """Cluster pencil/ink strokes into semantic regions on a sketch page."""
    boxes = major_sketch_boxes(img, binding) or sketch_boxes(img, binding)
    boxes = non_overlapping_sketch_boxes(img, boxes)
    return [img[top:bottom, left:right] for left, top, right, bottom in boxes]


def page_without_binding(img: np.ndarray, binding: Binding) -> np.ndarray:
    """Return one full sketchbook page with its detected binding removed."""
    small, scale = analysis_image(img)
    h, w = small.shape[:2]
    left, top, right, bottom = 0, 0, w, h
    if binding.side == "left":
        left = binding.cut
    elif binding.side == "right":
        right = binding.cut
    elif binding.side == "top":
        top = binding.cut
    else:
        bottom = binding.cut
    return img[
        int(np.floor(top / scale)) : int(np.ceil(bottom / scale)),
        int(np.floor(left / scale)) : int(np.ceil(right / scale)),
    ]


def looks_like_sketch_page(img: np.ndarray) -> bool:
    """Distinguish a sparse paper drawing from dense rectangular photographs."""
    small, _ = analysis_image(img)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h, w = hsv.shape[:2]
    margin = max(4, round(min(h, w) * 0.015))
    interior = hsv[margin : h - margin, margin : w - margin]
    if interior.size == 0:
        return False
    saturation = interior[:, :, 1]
    value = interior[:, :, 2]
    nonwhite_fraction = float(((value < 235) | (saturation > 40)).mean())
    colorful_fraction = float((saturation > 55).mean())
    median_value = float(np.median(value))

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    sigma = max(9.0, min(h, w) * 0.025)
    background = cv2.GaussianBlur(gray, (0, 0), sigma)
    local_darkness = cv2.subtract(background, gray)
    stroke_fraction = float((((local_darkness > 15) & (gray < 242)) | (gray < 175)).mean())

    sparse_page = nonwhite_fraction < 0.14 and colorful_fraction < 0.06
    shadowed_page = (
        colorful_fraction < 0.01
        and median_value > 248
        and stroke_fraction < 0.12
    )
    return sparse_page or shadowed_page


def find_page_composition(
    img: np.ndarray,
    binding: Binding | None = None,
) -> list[np.ndarray]:
    """Unify all meaningful page strokes with one convex hull and crop."""
    boxes = sketch_boxes(img, binding)
    if not boxes:
        return []
    points = np.array(
        [
            point
            for left, top, right, bottom in boxes
            for point in ((left, top), (right, top), (right, bottom), (left, bottom))
        ],
        dtype=np.int32,
    )
    hull = cv2.convexHull(points)
    x, y, width, height = cv2.boundingRect(hull)
    full_h, full_w = img.shape[:2]
    pad = max(24, round(min(full_h, full_w) * 0.02))
    left = max(0, x - pad)
    top = max(0, y - pad)
    right = min(full_w, x + width + pad)
    bottom = min(full_h, y + height + pad)
    if right <= left or bottom <= top:
        return []
    return [img[top:bottom, left:right]]


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
    sketch_mode = ap.add_mutually_exclusive_group()
    sketch_mode.add_argument(
        "--segment-sketches",
        action="store_true",
        help="split a sketch page into its major drawings instead of keeping one page",
    )
    sketch_mode.add_argument(
        "--crop-sketch",
        action="store_true",
        help="crop one image around the combined sketch composition",
    )
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
    binding = detect_binding(img)
    page_mode = binding is None and looks_like_sketch_page(img)
    if args.segment_sketches:
        print(
            "# segmenting sketch page into major drawing regions",
            file=sys.stderr,
        )
        crops = find_sketches(img, binding)
    elif args.crop_sketch:
        print("# cropping around combined sketch composition", file=sys.stderr)
        crops = find_page_composition(img, binding)
    elif binding:
        print(
            f"# sketchbook binding detected on {binding.side}; keeping full page",
            file=sys.stderr,
        )
        # Binding detection classifies this as a sketchbook scan, but normal
        # capture must remain lossless. Edge removal belongs to the explicit
        # --segment-sketches workflow, where the crop can be reviewed afterward.
        crops = [img]
    elif page_mode:
        print("# sparse sketch page detected; keeping one page", file=sys.stderr)
        crops = [img]
    else:
        crops = find_photos(img, args.min_area_frac, args.pad_frac)

    if not crops:
        # Nothing detected — keep a bound page minus its binding, or preserve the
        # full scan for the ordinary loose-photo fallback.
        if binding:
            crops = [page_without_binding(img, binding)]
            print("WARN: no sketch regions detected; emitting page without binding", file=sys.stderr)
        elif page_mode:
            crops = [img]
            print("WARN: no sketch hull detected; emitting full page", file=sys.stderr)
        else:
            crops = [img]
            print("WARN: no distinct photos detected; emitting full scan", file=sys.stderr)

    written = []
    for crop in crops:
        crop = rotate_crop(crop, args.rotate)
        pieces = [crop] if binding or page_mode or args.segment_sketches or args.crop_sketch else split_oversized_crop(crop)
        for piece in pieces:
            out_path = os.path.join(args.outdir, f"{base}_{len(written) + 1:03d}.jpg")
            write_jpeg(out_path, piece)
            written.append(out_path)
            print(out_path)

    print(f"# {len(written)} crop(s) from {args.scan}", file=sys.stderr)


if __name__ == "__main__":
    main()
