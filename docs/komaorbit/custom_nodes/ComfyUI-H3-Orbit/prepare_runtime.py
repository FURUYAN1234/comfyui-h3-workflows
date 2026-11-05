"""Download optional upstream code or compile gsplat in the local environment."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

WORLD_REPO = 'https://github.com/Tencent-Hunyuan/HunyuanWorld-Mirror'
WORLD_COMMIT = 'c61300e47ce06db2f02c8e2551aae68be7ae3a0b'
ROOT = Path(__file__).resolve().parent


def worldmirror():
    dest = ROOT / 'vendor/worldmirror'
    if dest.exists():
        provenance = dest / 'UPSTREAM.json'
        if provenance.is_file() and json.loads(provenance.read_text()).get('commit') == WORLD_COMMIT and (dest/'training/utils/logger.py').is_file():
            print('WorldMirror source already installed at the pinned revision.')
            return
        raise FileExistsError('Existing WorldMirror directory is not the pinned installation; preserve it before installing: '+str(dest))
    dest.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='komaorbit-world-', dir=dest.parent) as scratch:
        work = Path(scratch)
        repo = work / 'source'
        subprocess.run(['git', 'init', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', WORLD_REPO], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'remote.origin.promisor', 'true'], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'remote.origin.partialclonefilter', 'blob:none'], check=True)
        subprocess.run(['git', '-C', str(repo), 'fetch', '--depth=1', '--filter=blob:none', 'origin', WORLD_COMMIT], check=True)
        subprocess.run(['git', '-C', str(repo), 'sparse-checkout', 'set', '--no-cone', '/src/', '/training/utils/logger.py', '/License.txt', '/Notice.txt'], check=True)
        subprocess.run(['git', '-C', str(repo), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
        actual = subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'], text=True).strip()
        if actual != WORLD_COMMIT:
            raise RuntimeError('WorldMirror revision mismatch')
        candidate = work / 'runtime'
        candidate.mkdir()
        shutil.copytree(repo/'src', candidate/'src', ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        (candidate/'training/utils').mkdir(parents=True)
        shutil.copy2(repo/'training/utils/logger.py', candidate/'training/utils/logger.py')
        for name in ('License.txt','Notice.txt'):
            shutil.copy2(repo/name, candidate/name)
        (candidate/'UPSTREAM.json').write_text(json.dumps({'repository':WORLD_REPO,'commit':actual,'source_modified':False},indent=2)+'\n')
        candidate.replace(dest)
    print('Installed upstream WorldMirror inference source; no weights downloaded.')


def renderer(cuda_home=None):
    if cuda_home:
        os.environ['CUDA_HOME'] = str(Path(cuda_home).resolve())
        os.environ['PATH'] = str(Path(cuda_home).resolve()/'bin')+os.pathsep+os.environ.get('PATH','')
    os.environ.setdefault('MAX_JOBS', '3')
    # Run in this environment, with no dependency on the author's local binary.
    from runtime import load_renderer
    native = load_renderer()
    import torch
    print(json.dumps({'renderer':str(native.__file__),'torch':torch.__version__,'cuda':torch.version.cuda,'python':sys.version.split()[0]}))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worldmirror',action='store_true',help='Obtain separately licensed official WorldMirror code; read upstream terms first.')
    p.add_argument('--renderer',action='store_true',help='Build/check gsplat for the installed Python, PyTorch and CUDA.')
    p.add_argument('--cuda-home')
    a=p.parse_args()
    if not (a.worldmirror or a.renderer):p.error('Select --worldmirror and/or --renderer')
    if a.worldmirror:worldmirror()
    if a.renderer:renderer(a.cuda_home)
