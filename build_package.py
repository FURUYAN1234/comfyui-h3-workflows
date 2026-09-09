"""Build the portable ZIP, then verify its extracted contents.

Run from a reviewed clean source tree. Does not publish, install or generate.
"""
import argparse
import hashlib
import json
import pathlib
import re
import shutil
import tempfile
import zipfile

from verify_package import verify

ROOT = pathlib.Path(__file__).resolve().parent
EXCLUDED = {'.git', 'dist', '__pycache__', 'docs', 'patches', 'tests'}
DEVELOPMENT_ROOT_FILES = {
    '.gitattributes', '.gitignore', 'CHANGELOG.md', 'LICENSE', 'README.md',
    'VERSION', 'THIRD_PARTY_NOTICES.md', 'build_package.py',
    'dependencies.lock.json', 'verify_dependencies.py'
}


def payload_paths():
    return sorted(
        path for path in ROOT.rglob('*')
        if path.is_file()
        and not set(path.relative_to(ROOT).parts) & EXCLUDED
        and not (len(path.relative_to(ROOT).parts) == 1 and path.name in DEVELOPMENT_ROOT_FILES)
        and path.name != 'SHA256SUMS.json'
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--update-manifest', action='store_true',
        help='Update the reviewed source manifest before building; does not publish or install.'
    )
    args = parser.parse_args()
    metadata = json.loads((ROOT / 'VERSION.json').read_text(encoding='utf-8'))
    version = metadata['version']
    built_at = metadata['built_at_jst']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION.json version must be numeric SemVer')
    if not re.fullmatch(r'\d{14}', built_at):
        raise ValueError('VERSION.json built_at_jst must be yyyyMMddHHmmss')
    paths = payload_paths()
    for path in paths:
        if path.is_symlink():
            raise ValueError('Symlink not allowed: ' + str(path))
    destination = ROOT / 'dist' / ('ComfyUI_H3_Workflows_' + built_at + '_v' + version + '.zip')
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='h3-zip-build-') as temp:
        payload = pathlib.Path(temp) / 'ComfyUI_H3_Workflows'
        payload.mkdir()
        for path in paths:
            target = payload / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        hashes = {path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(payload.rglob('*')) if path.is_file()}
        source_manifest = ROOT / 'SHA256SUMS.json'
        manifest_text = json.dumps(hashes, ensure_ascii=False, indent=2) + '\n'
        if args.update_manifest:
            source_manifest.write_text(manifest_text, encoding='utf-8', newline='\n')
        manifest = payload / 'SHA256SUMS.json'
        if source_manifest.is_file() and source_manifest.read_text(encoding='utf-8') == manifest_text:
            shutil.copy2(source_manifest, manifest)
        else:
            manifest.write_text(manifest_text, encoding='utf-8', newline='\n')
        verify(payload)
        timestamp = tuple(map(int, (built_at[0:4], built_at[4:6], built_at[6:8], built_at[8:10], built_at[10:12], built_at[12:14])))
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(payload.rglob('*')):
                if not path.is_file():
                    continue
                info = zipfile.ZipInfo('ComfyUI_H3_Workflows/' + path.relative_to(payload).as_posix(), timestamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
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
