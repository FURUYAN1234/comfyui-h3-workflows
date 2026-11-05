"""Portable gsplat initialization; never downloads model weights."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys


def load_renderer(pack=None):
    import torch
    pack = Path(pack or Path(__file__).parent)
    manifest = pack / 'renderer/build.json'
    binary = pack / 'renderer/gsplat_cuda.so'
    # Compatibility with a locally built runtime. No binaries ship in releases.
    if manifest.is_file() and binary.is_file():
        build = json.loads(manifest.read_text())
        if list(sys.version_info[:2]) == build.get('python') and torch.__version__ == build.get('torch'):
            spec = importlib.util.spec_from_file_location('gsplat_cuda', binary)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules['gsplat.csrc'] = module
            return module
    try:
        if shutil.which('ninja') is None:
            import ninja
            os.environ['PATH'] = str(ninja.BIN_DIR) + os.pathsep + os.environ.get('PATH', '')
        from gsplat.cuda._backend import _C
        if _C is None:
            raise RuntimeError('CUDA compiler or compatible gsplat binary is missing')
        return _C
    except Exception as exc:
        raise RuntimeError(
            'KomaOrbit renderer is unavailable. With the ComfyUI Python, run '
            'custom_nodes/ComfyUI-H3-Orbit/prepare_runtime.py --renderer. '
            'Install a CUDA Toolkit matching PyTorch; set CUDA_HOME if needed. '
            'See the underlying compiler error below.'
        ) from exc
