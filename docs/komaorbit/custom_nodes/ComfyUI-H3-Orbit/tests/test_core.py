import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("orbit_core", Path(__file__).parents[1] / "core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class GeometryTests(unittest.TestCase):
    def test_aspect_and_content_bounds(self):
        for w, h in [(1920, 1080), (1080, 1920), (1001, 667), (4001, 3001), (32, 32), (1, 80), (80, 1)]:
            with self.subTest(size=(w, h)):
                g = core.image_geometry(w, h, 1024)
                self.assertEqual(g["export_width"] * h, g["export_height"] * w)
                self.assertEqual(g["canvas_width"] % 32, 0)
                self.assertEqual(g["canvas_height"] % 32, 0)
                self.assertLessEqual(g["left"] + g["content_width"], g["canvas_width"])
                self.assertLessEqual(g["top"] + g["content_height"], g["canvas_height"])
                self.assertLess(g["canvas_width"] - g["content_width"], 32)
                self.assertLess(g["canvas_height"] - g["content_height"], 32)
                core.validate_geometry(g, g["canvas_width"], g["canvas_height"], w, h)

    def test_invalid_source_dimensions(self):
        for args in [(0, 8, 1024), (8, -1, 1024), (8, 8, 0), (True, 8, 1024)]:
            with self.assertRaises(ValueError):
                core.image_geometry(*args)

    def test_mismatched_canvas_is_rejected_instead_of_cropping_wrong_area(self):
        g = core.image_geometry(1001, 667, 1024)
        with self.assertRaisesRegex(ValueError, "生成画像"):
            core.validate_geometry(g, 1024, 768, 1001, 667)

    def test_mismatched_original_and_changed_ratio(self):
        g = core.image_geometry(1200, 800, 1024)
        with self.assertRaisesRegex(ValueError, "元画像"):
            core.validate_geometry(g, g["canvas_width"], g["canvas_height"], 800, 1200)
        g["export_width"] += 1
        with self.assertRaisesRegex(ValueError, "縦横比"):
            core.validate_geometry(g, g["canvas_width"], g["canvas_height"], 1200, 800)


if __name__ == "__main__":
    unittest.main()
