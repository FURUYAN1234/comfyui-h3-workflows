import pathlib,subprocess,json,hashlib,zipfile,sys,datetime
root=pathlib.Path(__file__).resolve().parent
excluded_root={'.gitattributes','.gitignore','CHANGELOG.md','LICENSE','README.md','VERSION','build_package.py','build_release.py','docs','tests','patches','dist','dependencies.lock.json','verify_dependencies.py','THIRD_PARTY_NOTICES.md'}
tracked=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
files={p:(root/p).read_bytes() for p in tracked if p and p.split('/')[0] not in excluded_root and p!='SHA256SUMS.json'}
manifest={p:hashlib.sha256(b).hexdigest() for p,b in sorted(files.items())}
files['SHA256SUMS.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
v=json.loads(files['VERSION.json']);dt=datetime.datetime.strptime(v['built_at_jst'],'%Y%m%d%H%M%S')
with zipfile.ZipFile(sys.argv[1],'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for name,data in sorted(files.items()):
  info=zipfile.ZipInfo('ComfyUI_H3_Workflows/'+name,dt.timetuple()[:6])
  # ZIPs built on Windows and Unix must carry the same creator metadata.
  info.create_system=3
  info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;z.writestr(info,data)
print(sys.argv[1],len(files))
