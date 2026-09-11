"""Rebuild the exact reviewed payload; never install or publish."""
import argparse,hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def files():
    return sorted(p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.relative_to(ROOT).parts and "__pycache__" not in p.parts and p.suffix != ".pyc" and p.name != "SHA256SUMS.json")
def build(destination,update=False):
    dest=Path(destination).resolve()
    if dest==ROOT or ROOT in dest.parents: raise ValueError("Destination must be outside source tree")
    if (ROOT/"custom_nodes/ComfyUI-Custom-Scripts/pysssss.json").exists(): raise ValueError("Runtime UI configuration must be removed before packaging")
    data={p.relative_to(ROOT).as_posix():p.read_bytes() for p in files()}
    if any(p.is_symlink() for p in files()):raise ValueError("Symlink in payload")
    manifest=(json.dumps({n:hashlib.sha256(b).hexdigest() for n,b in data.items()},ensure_ascii=False,indent=2)+"\n").encode()
    mf=ROOT/"SHA256SUMS.json"
    if update:mf.write_bytes(manifest)
    if mf.read_bytes()!=manifest:raise ValueError("Manifest is stale")
    data[mf.name]=manifest
    meta=json.loads((ROOT/"VERSION.json").read_text())
    from datetime import datetime
    stamp=datetime.strptime(meta["built_at_jst"],"%Y%m%d%H%M%S").timetuple()[:6]
    dest.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(dest,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for n,b in sorted(data.items()):
            info=zipfile.ZipInfo(meta["archive_root"]+"/"+n,stamp)
            info.create_system=3;info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            z.writestr(info,b,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    print(dest.name,hashlib.sha256(dest.read_bytes()).hexdigest())
if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("destination");ap.add_argument("--update-manifest",action="store_true");a=ap.parse_args();build(a.destination,a.update_manifest)
