import unittest
import torch
from test_grid import H3OrbitPrepare
from h3_orbit_grid_tests.wide import H3OrbitWideCanvas, H3OrbitWideComposite, canvas_parts


class WideTests(unittest.TestCase):
    def test_landscape_and_portrait_preserve_known_pixels(self):
        for h, w in ((61, 97), (97, 61)):
            image = torch.rand(1, h, w, 3)
            prepared, _, _, g = H3OrbitPrepare().prepare(image, 128)
            canvas, mask, _, wide_geometry, _ = H3OrbitWideCanvas().prepare(prepared, g)
            known, left, top, cw, ch = canvas_parts(prepared, g)
            self.assertTrue(torch.equal(canvas[:, top:top+ch, left:left+cw], known))
            self.assertEqual(float(mask[:, top:top+ch, left:left+cw].sum()), 0)
            result, wide_g, basis = H3OrbitWideComposite().compose(torch.zeros_like(canvas), prepared, g)
            self.assertTrue(torch.equal(result[:, top:top+ch, left:left+cw], known))
            self.assertTrue(torch.equal(result[:, :1, :1], torch.zeros_like(result[:, :1, :1])))
            self.assertEqual(basis, .5)
            self.assertEqual(wide_g['source_width'], w)
            self.assertEqual(wide_g, wide_geometry)
            self.assertNotIn('frame_basis_zoom', g)

    def test_mismatched_generation_rejected(self):
        image = torch.rand(1, 64, 96, 3)
        prepared, _, _, g = H3OrbitPrepare().prepare(image, 128)
        with self.assertRaises(ValueError):
            H3OrbitWideComposite().compose(prepared[:, :32], prepared, g)

    def test_rgba_decoder_output_is_flattened_before_rgb_orbit(self):
        image = torch.rand(1, 64, 96, 3)
        prepared, _, _, g = H3OrbitPrepare().prepare(image, 128)
        generated = torch.zeros((*prepared.shape[:3], 4))
        generated[..., 0] = 1
        generated[..., 3] = .5
        result, _, _ = H3OrbitWideComposite().compose(generated, prepared, g)
        self.assertEqual(result.shape, prepared.shape)
        self.assertTrue(torch.equal(result[0, 0, 0], torch.tensor([1., .5, .5])))
        known, left, top, cw, ch = canvas_parts(prepared, g)
        self.assertTrue(torch.equal(result[:, top:top+ch, left:left+cw], known))
