from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ezdxf
from shapely.geometry import box
from shapely.ops import polygonize

import inset_dxf_polygons as converter


class ConversionTests(unittest.TestCase):
    def test_shared_profile_stays_closed_and_scales_to_target_width(self) -> None:
        source_regions = [box(0, 0, 4, 2), box(4, 0, 8, 2)]
        network, regions, report = converter.canonical_profile(
            source_regions,
            min_area=1e-8,
            topology_snap_tolerance=0.0002,
            simplify_tolerance=0.0002,
        )
        normalized, bounds, scale_factor = converter.normalize_profile(network, 9.5)

        _polygons, cut_edges, dangles, invalid_rings = report
        self.assertEqual(len(regions), 2)
        self.assertEqual(len(cut_edges.geoms), 0)
        self.assertEqual(len(dangles.geoms), 0)
        self.assertEqual(len(invalid_rings.geoms), 0)
        self.assertAlmostEqual(scale_factor, 9.5 / 8)
        self.assertAlmostEqual(bounds[2] - bounds[0], 9.5)
        self.assertEqual(len(list(polygonize(normalized))), 2)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "panel.dxf"
            written = converter.write_line_only_dxf(output, normalized)
            document = ezdxf.readfile(output)
            entities = list(document.modelspace())

        self.assertGreater(written, 0)
        self.assertEqual(document.header["$INSUNITS"], 1)
        self.assertTrue(all(entity.dxftype() == "LINE" for entity in entities))


if __name__ == "__main__":
    unittest.main()
