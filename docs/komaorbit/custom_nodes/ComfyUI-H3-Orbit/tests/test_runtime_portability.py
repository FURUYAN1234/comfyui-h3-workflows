import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import torch

ROOT = Path(__file__).parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class UpscaleCompatibilityTests(unittest.TestCase):
    def test_float_input_runs_with_half_model_without_changing_parameters(self):
        linear = torch.nn.Linear(3, 3, bias=False).half()
        linear.dtype = torch.float16
        linear.scale = 1
        source = torch.ones(2, 3, dtype=torch.float32)
        before = linear.weight.detach().clone()
        with self.assertRaises(RuntimeError):
            linear(source)
        wrapped = module('upscale_compat').DtypeSafeUpscaler(linear)
        result = wrapped(source)
        self.assertEqual(result.dtype, torch.float16)
        self.assertEqual(wrapped.scale, 1)
        self.assertTrue(torch.equal(before, linear.weight))
        self.assertEqual(source.dtype, torch.float32)


class RendererCompatibilityTests(unittest.TestCase):
    def backend(self, value):
        result = types.ModuleType('gsplat.cuda._backend')
        result._C = value
        return result

    def test_clean_install_uses_environment_extension(self):
        native = object()
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'gsplat.cuda._backend': self.backend(native)}):
            self.assertIs(module('runtime').load_renderer(directory), native)

    def test_missing_compiler_explains_setup_and_preserves_cause(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'gsplat.cuda._backend': self.backend(None)}):
            with self.assertRaisesRegex(RuntimeError, 'prepare_runtime.py --renderer') as caught:
                module('runtime').load_renderer(directory)
            self.assertIn('compiler', str(caught.exception.__cause__))


if __name__ == '__main__':
    unittest.main()
