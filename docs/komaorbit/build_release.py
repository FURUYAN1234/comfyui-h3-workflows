"""Rebuild the source-only release deterministically; never install or publish."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import zipfile
from verify_package import ROOT, check_source, digest_map, verify


def build(destination, update_manifest=False):
    destination = Path(destination).resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError('Archive destination must be outside the source tree')
    if destination.exists():
        raise FileExistsError(destination)
    check_source()
    if update_manifest:
        (ROOT/'SHA256SUMS.json').write_text(json.dumps(digest_map(), ensure_ascii=False, indent=2)+'\n')
    verify()
    version = json.loads((ROOT/'VERSION.json').read_text())
    stamp = datetime.strptime(version['built_at_jst'], '%Y%m%d%H%M%S').timetuple()[:6]
    paths = list(digest_map()) + ['SHA256SUMS.json']
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(paths):
            info = zipfile.ZipInfo(version['archive_root']+'/'+name, stamp)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (ROOT/name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    print(destination, hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    parser.add_argument('--update-manifest', action='store_true')
    args = parser.parse_args()
    build(args.destination, args.update_manifest)
