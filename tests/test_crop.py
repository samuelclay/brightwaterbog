import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from pipeline.crop import (
    box_intersection_area,
    detect_binding,
    find_page_composition,
    find_sketches,
    looks_like_sketch_page,
    major_sketch_boxes,
    non_overlapping_sketch_boxes,
    page_without_binding,
)


class SketchbookSegmentationTests(unittest.TestCase):
    def sketchbook_page(self) -> np.ndarray:
        page = np.full((1200, 900, 3), 252, dtype=np.uint8)

        # Three intentionally separate content regions: two drawings and notes.
        cv2.rectangle(page, (300, 90), (470, 360), (95, 75, 90), 8)
        cv2.line(page, (320, 120), (450, 330), (120, 95, 115), 6)
        cv2.ellipse(page, (245, 705), (90, 215), 0, 0, 360, (100, 80, 95), 8)
        cv2.line(page, (205, 510), (285, 900), (125, 100, 118), 6)
        cv2.putText(
            page,
            "deep red",
            (535, 1010),
            cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
            1.25,
            (90, 65, 82),
            4,
            cv2.LINE_AA,
        )

        # A repeated dark comb at the right edge models the spiral binding.
        for y in range(25, 1180, 65):
            cv2.rectangle(page, (840, y), (899, y + 28), (10, 10, 10), -1)
        cv2.rectangle(page, (892, 0), (899, 1199), (5, 5, 5), -1)
        return page

    def test_detects_binding_and_two_drawings_while_ignoring_notes(self) -> None:
        page = self.sketchbook_page()
        binding = detect_binding(page)
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.side, "right")

        crops = find_sketches(page, binding)
        self.assertEqual(len(crops), 2)
        self.assertTrue(all(crop.shape[1] < 600 for crop in crops))

        full_page = page_without_binding(page, binding)
        self.assertEqual(full_page.shape[0], page.shape[0])
        self.assertLess(full_page.shape[1], page.shape[1])
        self.assertGreater(full_page.shape[1], page.shape[1] * 0.85)

    def test_normal_capture_keeps_detected_sketchbook_page_full_size(self) -> None:
        page = self.sketchbook_page()
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            scan = temp / "scan.jpg"
            output = temp / "output"
            self.assertTrue(cv2.imwrite(str(scan), page))
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "pipeline" / "crop.py"),
                    str(scan),
                    str(output),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            emitted = cv2.imread(str(output / "scan_001.jpg"))
            self.assertIsNotNone(emitted)
            assert emitted is not None
            self.assertEqual(emitted.shape[:2], page.shape[:2])

    def test_does_not_treat_loose_photos_as_a_binding(self) -> None:
        bed = np.full((1000, 800, 3), 250, dtype=np.uint8)
        cv2.rectangle(bed, (80, 90), (350, 420), (45, 70, 95), -1)
        cv2.rectangle(bed, (430, 520), (700, 900), (80, 50, 45), -1)
        self.assertIsNone(detect_binding(bed))
        self.assertFalse(looks_like_sketch_page(bed))

    def test_top_edge_drawing_is_not_treated_as_binding(self) -> None:
        page = np.full((1400, 1000, 3), 252, dtype=np.uint8)
        for y in range(15, 170, 22):
            cv2.line(page, (140, y), (860, y + 18), (70, 70, 70), 7)
        cv2.circle(page, (360, 470), 190, (80, 80, 80), 9)
        cv2.circle(page, (650, 960), 170, (80, 80, 80), 9)

        self.assertIsNone(detect_binding(page))

    def test_two_confident_drawings_segment_into_two_results(self) -> None:
        page = np.full((1400, 1000, 3), 252, dtype=np.uint8)
        cv2.circle(page, (500, 220), 220, (65, 65, 65), 10)
        cv2.line(page, (330, 100), (670, 340), (90, 90, 90), 8)
        cv2.circle(page, (500, 1030), 230, (65, 65, 65), 10)
        cv2.line(page, (310, 900), (690, 1160), (90, 90, 90), 8)

        boxes = major_sketch_boxes(page)
        self.assertEqual(len(boxes), 2)
        self.assertEqual(boxes[0][1], 0)

    def test_segment_boxes_are_deduplicated_and_do_not_overlap(self) -> None:
        page = np.full((500, 500, 3), 252, dtype=np.uint8)
        cv2.circle(page, (140, 140), 70, (60, 60, 60), 8)
        cv2.circle(page, (300, 300), 80, (60, 60, 60), 8)
        boxes = [
            (40, 40, 260, 260),
            (80, 80, 220, 220),
            (180, 180, 420, 420),
        ]

        partitioned = non_overlapping_sketch_boxes(page, boxes)

        self.assertEqual(len(partitioned), 2)
        self.assertEqual(box_intersection_area(partitioned[0], partitioned[1]), 0)

    def test_unbound_sparse_page_becomes_one_composition(self) -> None:
        page = np.full((1200, 900, 3), 252, dtype=np.uint8)
        cv2.rectangle(page, (80, 180), (790, 840), (115, 90, 105), 6)
        cv2.circle(page, (280, 460), 95, (85, 65, 80), 8)
        cv2.circle(page, (620, 570), 70, (85, 65, 80), 8)
        cv2.line(page, (350, 470), (550, 560), (105, 82, 98), 7)
        cv2.putText(
            page,
            "one study",
            (460, 1060),
            cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
            1.2,
            (90, 65, 82),
            4,
            cv2.LINE_AA,
        )

        self.assertTrue(looks_like_sketch_page(page))
        crops = find_page_composition(page)
        self.assertEqual(len(crops), 1)
        self.assertGreater(crops[0].shape[0], 800)
        self.assertGreater(crops[0].shape[1], 700)

    def test_thin_binding_and_shadow_yield_only_major_drawings(self) -> None:
        page = np.full((1400, 1000, 3), 253, dtype=np.uint8)

        # A broad gray scanner shadow must not be mistaken for the binding or a
        # fourth drawing.
        for y in range(220):
            shade = 175 + round(75 * y / 220)
            cv2.line(page, (0, y), (999, y), (shade, shade, shade), 1)

        # Thin comb binding at left: many small holes but little total area.
        for y in range(20, 1380, 48):
            cv2.rectangle(page, (0, y), (9, y + 15), (15, 15, 15), -1)

        # Three substantial studies, with the lower one extending between the
        # upper pair as on the real Four Stages sketchbook page.
        cv2.rectangle(page, (100, 90), (340, 470), (70, 70, 70), 8)
        cv2.line(page, (104, 94), (336, 466), (95, 95, 95), 7)
        cv2.rectangle(page, (720, 90), (950, 500), (65, 65, 65), 8)
        cv2.line(page, (724, 94), (946, 496), (100, 100, 100), 7)
        cv2.rectangle(page, (400, 360), (670, 1320), (75, 75, 75), 9)
        cv2.line(page, (404, 364), (666, 1316), (105, 105, 105), 7)

        # Low-mass garbage should not become a result.
        cv2.circle(page, (150, 850), 24, (90, 90, 90), 4)

        binding = detect_binding(page)
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.side, "left")
        self.assertTrue(looks_like_sketch_page(page))

        boxes = major_sketch_boxes(page, binding)
        self.assertEqual(len(boxes), 3)
        self.assertTrue(all((right - left) * (bottom - top) > 50_000 for left, top, right, bottom in boxes))

    def test_one_drawing_ignores_weak_page_edge_and_date(self) -> None:
        page = np.full((1400, 1000, 3), 252, dtype=np.uint8)
        cv2.rectangle(page, (250, 220), (820, 920), (70, 70, 70), 10)
        cv2.line(page, (280, 260), (790, 880), (90, 90, 90), 8)
        cv2.line(page, (10, 0), (10, 1399), (155, 155, 155), 3)
        cv2.putText(
            page,
            "1-13-99",
            (25, 110),
            cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
            0.8,
            (130, 130, 130),
            2,
            cv2.LINE_AA,
        )

        crops = find_sketches(page)
        self.assertEqual(len(crops), 1)
        self.assertLess(crops[0].shape[0], page.shape[0])
        self.assertLess(crops[0].shape[1], page.shape[1])


if __name__ == "__main__":
    unittest.main()
