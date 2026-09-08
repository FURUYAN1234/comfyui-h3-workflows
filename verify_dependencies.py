"""Read-only checks; never import nodes, download models or modify ComfyUI."""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent


def verify(comfy):
    nodes = pathlib.Path(comfy).resolve() / 'custom_nodes'
    count = 0
    for dep in json.loads((ROOT / 'dependencies.lock.json').read_text(encoding='utf-8')):
        for relative, expected in dep['files_sha256_lf'].items():
            path = nodes / dep['name'] / relative
            if not path.is_file():
                raise AssertionError('Missing dependency file: ' + str(path))
            data = path.read_bytes().replace(b'\r\n', b'\n')
            if hashlib.sha256(data).hexdigest() != expected:
                raise AssertionError('Dependency content mismatch: ' + str(path))
            count += 1
    helper = 'comfyui-h3-standard-prompt'
    for path in (ROOT / 'custom_nodes' / helper).rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        installed = nodes / helper / path.relative_to(ROOT / 'custom_nodes' / helper)
        if not installed.is_file() or installed.read_bytes().replace(b'\r\n', b'\n') != path.read_bytes().replace(b'\r\n', b'\n'):
            raise AssertionError('Original helper missing or changed: ' + str(installed))
    print('Dependency content checks passed:', count, 'third-party files plus original helper')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python verify_dependencies.py /path/to/ComfyUI')
    verify(sys.argv[1])
