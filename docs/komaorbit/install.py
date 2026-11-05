"""Install KomaOrbit source into an existing ComfyUI directory; weights stay separate."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent

def install(comfy, replace=False, dependencies=False):
    comfy=Path(comfy).resolve()
    if not (comfy/'main.py').is_file() or not (comfy/'comfy').is_dir():
        raise ValueError('Specify the ComfyUI directory containing main.py and comfy/')
    targets=[]
    for src in sorted((ROOT/'custom_nodes').iterdir()):
        if not src.is_dir():continue
        dest=comfy/'custom_nodes'/src.name
        if dest.exists() and not replace:
            raise FileExistsError(str(dest)+' already exists. Back it up and use --replace to update code.')
        targets.append((src,dest))
    for src,dest in targets:
        shutil.copytree(src,dest,dirs_exist_ok=replace,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        print('Installed',dest)
    if dependencies:
        subprocess.run([sys.executable,'-m','pip','install','-r',str(comfy/'custom_nodes/ComfyUI-H3-Orbit/requirements.txt')],check=True)
    print('Restart ComfyUI, open KomaOrbit.json, choose your input, and obtain listed models separately.')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--comfy',required=True)
    p.add_argument('--replace',action='store_true')
    p.add_argument('--dependencies',action='store_true')
    a=p.parse_args();install(a.comfy,a.replace,a.dependencies)
