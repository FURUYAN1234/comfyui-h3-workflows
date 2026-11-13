"""Build independent H3 Video or MV kits; never install, download or publish."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
PACKAGES = {
    'video': ('H3_Video', {'T2V', 'I2V', 'Ref2V'}),
    'mv': ('H3_MV', {'MV'}),
}
ROOT_FILES = (
    'LICENSE', 'FURU_TERMS.md', 'LICENSE_SCOPE.md', 'LICENSES_AND_NOTICES.md',
    'requirements.txt', 'models.json', 'configure_audio_audit.py',
    'verify_package.py', 'build_release.py', 'VALIDATION.md', 'VERSION', 'CHANGELOG.md',
)


def build(kind, destination, stamp):
    prefix, kinds = PACKAGES[kind]
    datetime.strptime(stamp, '%Y%m%d%H%M%S')
    destination = Path(destination).resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError('Archive must be outside the source tree')
    if destination.exists():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(prefix='h3-kit-') as temp:
        root = Path(temp)
        for name in ROOT_FILES:
            shutil.copy2(ROOT/name, root/name)
        for name in ('custom_nodes', 'licenses'):
            shutil.copytree(ROOT/name, root/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache', '.git'))
        if kind == 'video':
            shutil.rmtree(root/'custom_nodes/comfyui-mv-workflow')
        (root/'workflows').mkdir()
        chosen = sorted(p for p in (ROOT/'workflows').glob('*.json') if p.name.split('_')[0] in kinds)
        if len(chosen) != len(kinds):
            raise ValueError('Missing or duplicate source workflow')
        for path in chosen:
            shutil.copy2(path, root/'workflows'/path.name)
        docs = root/'docs/h3'
        docs.mkdir(parents=True)
        for name in ('SETUP.md', 'SETTINGS.md'):
            shutil.copy2(ROOT/'docs/h3'/name, docs/name)
        for workflow in sorted(kinds):
            shutil.copytree(ROOT/'docs/h3'/workflow.lower(), docs/workflow.lower())
        if kind == 'mv':
            assets = root/'docs/assets'
            assets.mkdir()
            for name in ('workflow-mv-v1.2.0.png', 'workflow-bridge-yue2-to-mv-v1.2.0.png'):
                shutil.copy2(ROOT/'docs/assets'/name, assets/name)
        meta = json.loads((ROOT/'VERSION.json').read_text())
        meta.update(distribution_kind=kind, distribution_revision='split1',
                    workflow_built_at_jst=meta.get('workflow_built_at_jst', meta['built_at_jst']), built_at_jst=stamp,
                    archive_prefix=prefix, archive_root=f"{prefix}_{stamp}_v{meta['version']}",
                    workflow_files=[p.name for p in chosen],
                    custom_node_packages=sorted(p.name for p in (root/'custom_nodes').iterdir() if p.is_dir()),
                    source_release='https://github.com/FURUYAN1234/comfyui-h3-workflows/tree/h3-split-'+stamp)
        (root/'VERSION.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2)+'\n')
        label = 'T2V / I2V / Ref2V' if kind == 'video' else 'MV'
        readme = f'# {prefix} v{meta["version"]} / {label}専用配布\n<!-- bilingual-readme: paired english-japanese -->\n\n'
        readme += 'Start with the guide for the workflow you want. No other workflow package is required. / 使いたい方式の専用ガイドから始めてください。他のワークフロー配布物は不要です。\n\n'
        readme += '\n'.join(f'- [{k}: instructions / 使い方](docs/h3/{k.lower()}/README.md)' for k in sorted(kinds))
        readme += '\n\n- [Setup / 共通導入](docs/h3/SETUP.md)\n- [Settings and recovery / 設定と再開](docs/h3/SETTINGS.md)\n- [License notices / 権利表示](LICENSES_AND_NOTICES.md)\n\nRun `python -B verify_package.py` before installation. / 導入前に配布物を検査します。\n'
        readme += f'\nContains {len(chosen)} workflow JSONs and {len(meta["custom_node_packages"])} node folders. Models and inputs are not bundled. / JSONは{len(chosen)}本、ノードは{len(meta["custom_node_packages"])}フォルダーです。モデル重み・入力素材は同梱しません。\n'
        (root/'README.md').write_text(readme)
        # This inventory records the exact unchanged runtime source in the kit.
        baseline = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for folder in ('custom_nodes', 'workflows') for p in (root/folder).rglob('*') if p.is_file()}
        (root/'RUNTIME_SHA256.json').write_text(json.dumps(baseline, indent=2)+'\n')
        # The ordinary builder generates the exact manifest. Its verifier then
        # checks graph/settings, dependencies, licenses and every file hash.
        subprocess.run([sys.executable, '-B', str(root/'build_release.py'), str(destination), '--update-manifest'], check=True)
        subprocess.run([sys.executable, '-B', str(root/'verify_package.py')], check=True)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=PACKAGES)
    parser.add_argument('destination')
    parser.add_argument('--stamp', required=True)
    args = parser.parse_args()
    build(args.kind, args.destination, args.stamp)
