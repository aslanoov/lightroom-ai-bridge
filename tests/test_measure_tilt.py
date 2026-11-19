"""Geometry audit regressions. Run with numpy/Pillow installed:
python3 -m unittest discover -s tests -p test_measure_tilt.py -v
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location("measure_tilt", Path(__file__).parents[1] / "bridge/measure_tilt.py")
tilt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tilt)


class GeometryAuditTests(unittest.TestCase):
    def reference(self, axis, degrees, size=(1200, 2000)):
        theta = np.radians(degrees + (90 if axis == "vertical" else 0))
        center = np.asarray(size)/2
        direction = np.array([np.cos(theta), np.sin(theta)])
        points = [(center + distance*direction)/size for distance in (-300, 0, 300)]
        return dict(label="known line", axis=axis, reason="synthetic calibration",
                    points=np.asarray(points).tolist())

    def test_both_axes_sign_and_non_square_pixel_scaling(self):
        for axis in ("vertical", "horizontal"):
            for angle in (-5, 0, 3):
                with self.subTest(axis=axis, angle=angle):
                    result = tilt.fit_reference(self.reference(axis, angle), (1200, 2000))
                    self.assertAlmostEqual(result["angle_deg_clockwise"], angle)
                    self.assertAlmostEqual(result["span_px"], 600)

    def test_endpoint_order_does_not_change_angle(self):
        ref = self.reference("vertical", 4)
        ref["points"].reverse()
        self.assertAlmostEqual(tilt.fit_reference(ref, (1200, 2000))["angle_deg_clockwise"], 4)

    def test_missing_reference_cannot_pass(self):
        review = tilt.axis_review([], {})
        self.assertEqual(review["vertical"]["status"], "unresolved")
        self.assertEqual(review["horizontal"]["status"], "unresolved")
        review = tilt.axis_review([self.reference("vertical", 0)], {"horizontal": "No horizon in this scene"})
        self.assertEqual(review["vertical"]["status"], "measured_review_required")
        self.assertEqual(review["horizontal"]["status"], "unavailable")

    def test_invalid_points_rejected(self):
        for points in ([[0.5, 0.5], [0.5, 0.5]], [[0, 0], [float("nan"), 0]], [[0, 0], [2, 1]]):
            ref = self.reference("horizontal", 0)
            ref["points"] = points
            with self.assertRaises(ValueError):
                tilt.fit_reference(ref, (1200, 2000))

    def test_stale_render_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"references.json"
            p.write_text(json.dumps({"image_size": [1200, 2000], "image_sha256": "original",
                                     "references": [self.reference("vertical", 0)]}))
            with self.assertRaisesRegex(ValueError, "does not match"):
                tilt.load_references(p, (1200, 2000), "changed")
            with self.assertRaisesRegex(ValueError, "does not match"):
                tilt.load_references(p, (1000, 2000), "original")

    def test_blank_texture_is_no_signal(self):
        self.assertEqual(tilt.deviation_from_vertical(np.zeros((200, 300))), (None, 0))

    def test_two_endpoints_do_not_claim_precision(self):
        ref = self.reference("horizontal", 1)
        ref["points"] = ref["points"][::2]
        self.assertTrue(tilt.fit_reference(ref, (1200, 2000))["warnings"])


if __name__ == "__main__":
    unittest.main()
