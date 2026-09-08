"""Build a deterministic source ZIP, then verify its extracted contents.

Run from a reviewed clean source tree. Does not publish, install or generate.
"""
import hashlib
import json
import pathlib
import re
import tempfile
import zipfile

from verify_package import verify

ROOT = pathlib.Path(__file__).resolve().parent
EXCLUDED = {'.git', 'dist', '__pycache__'}


def main():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must be numeric SemVer')
    paths = sorted(p for p in ROOT.rglob('*') if p.is_file()
                   and not set(p.relative_to(ROOT).parts) & EXCLUDED
                   and p.name != 'SHA256SUMS.json')
    for path in paths:
        if path.is_symlink():
            raise ValueError('Symlink not allowed: ' + str(path))
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    manifest = ROOT / 'SHA256SUMS.json'
    manifest.write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    verify(ROOT)
    destination = ROOT / 'dist' / ('ComfyUI_H3_Workflows-v' + version + '.zip')
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths + [manifest]):
            info = zipfile.ZipInfo('ComfyUI_H3_Workflows/' + path.relative_to(ROOT).as_posix(), (2026, 9, 8, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with tempfile.TemporaryDirectory(prefix='h3-zip-check-') as temp:
        with zipfile.ZipFile(destination) as archive:
            if archive.testzip() is not None:
                raise AssertionError('ZIP CRC failure')
            archive.extractall(temp)
        verify(pathlib.Path(temp) / 'ComfyUI_H3_Workflows')
    print('ZIP:', destination)
    print('Bytes:', destination.stat().st_size)
    print('SHA256:', hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
