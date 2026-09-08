"""Verify an extracted package without installing nodes or downloading models."""
import ast,hashlib,json,pathlib,re,sys
ROOT=pathlib.Path(__file__).resolve().parent
def require(value,message):
    if not value:raise AssertionError(message)
def verify(root=ROOT):
    paths=list((root/'workflows').glob('*.json'))
    require(len(paths)==3,'Exactly three sample workflows are required')
    models=json.loads((root/'models.json').read_text(encoding='utf-8'))
    required={'comfyui-h3-standard-prompt'}
    require({p.name for p in (root/'custom_nodes').iterdir() if p.is_dir()}==required,'Custom-node package list differs')
    for f in paths:
        require(not re.search(r'(?:_v?2|＿２)',f.name,re.I),'Personal variant included')
        d=json.loads(f.read_text(encoding='utf-8')); ns={n['id']:n for n in d['nodes']};links={l[0]:l for l in d['links']}
        kind=f.name.split('_')[0];expected={'T2V':'T2VA','I2V':'I2VA','Ref2V':'Ref2VA (R2V)'}[kind]
        require(ns[138]['widgets_values'][1]==expected,'Wrong prompt mode')
        require(bool(ns[138]['widgets_values'][0].strip()) and ns[138]['widgets_values'][10]=='','Sample input route differs')
        require(ns[123]['widgets_values']==['res_multistep'],'Sampler changed')
        require(ns[124]['widgets_values']==['simple',4,1],'Video steps changed')
        require(ns[161]['widgets_values']==[0.9,'64',8192,0,True,True],'SLA configuration changed')
        require(ns[150]['widgets_values'][13:]==[2,0.5,False,False,True],'Audio steps or prompt/duration policy changed')
        require(ns[115]['widgets_values']==['16:9 (Widescreen)',0.4,32],'Resolution default changed')
        require(ns[132]['widgets_values']==[15.0],'Default seconds changed')
        for n in ns.values():
            require('widgets_values_named' not in n,'Stale named-widget state remains')
            for slot,i in enumerate(n.get('inputs',[])):
                lid=i.get('link')
                if lid is not None:
                    require(lid in links,'Missing link record')
                    l=links[lid];require(l[3]==n['id'] and l[4]==slot,'Input socket/link mismatch')
                    require(l[1] in ns and l[2]<len(ns[l[1]].get('outputs',[])),'Source socket missing')
            if n['type'] in ('UNETLoader','CLIPLoader','VAELoader'):
                m=next(m for m in models if m['name']==n['widgets_values'][0])
                require(n['properties']['models']==[{k:m[k] for k in ('name','directory','url')}],'Model download metadata mismatch')
            if n['type']=='LoadImage':require(n['widgets_values'][0]=='','Image or private path remains')
            if n['type']=='ShowText|pysssss':require(n.get('widgets_values')==[''],'Generated text remains')
        def linked(name):return next((i.get('link') for i in ns[150]['inputs'] if i['name']==name),None)
        require(linked('prompt_plan') is not None and linked('audio_refine_model') is not None,'Required generation connection missing')
        if kind=='I2V':require(linked('first_frame') is not None,'I2V does not use first frame')
        if kind=='T2V':require(not any(n['type']=='LoadImage' for n in ns.values()),'T2V unexpectedly requires an image')
        if kind=='Ref2V':
            ims=[n for n in ns.values() if n['type']=='LoadImage'];require(len(ims)==5,'Ref2V must have five image slots')
            require(sum(n.get('mode',0)==0 for n in ims)==1,'Only the first reference is enabled by default')
        notes='\n'.join(n['widgets_values'][0] for n in ns.values() if n['type']=='MarkdownNote')
        for text in ('LM Studio','可変尺','BGM','diffusion_models/','text_encoders/','vae/','変える場所','Duration: 15 seconds'):
            require(text in notes,'Missing inline instructions: '+text)
        print('Workflow OK:',kind)
    for p in (root/'custom_nodes').rglob('*.py'):ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
    dependencies=json.loads((root/'dependencies.lock.json').read_text(encoding='utf-8'))
    require(len(dependencies)==4,'Exactly four external dependencies required')
    for dep in dependencies:
        require(bool(re.fullmatch('[0-9a-f]{40}',dep['commit'])),'Unpinned dependency')
        require((root/dep['patch']).is_file(),'Missing compatibility patch')
        require((root/'licenses'/(dep['name']+'.txt')).is_file(),'Missing third-party license')
    def files():
        return [p for p in root.rglob('*') if p.is_file() and not set(p.relative_to(root).parts)&{'.git','dist','__pycache__'}]
    for p in files():
        require('__pycache__' not in p.parts and p.suffix!='.pyc','Runtime cache included')
        require(p.suffix.lower() not in ('.safetensors','.gguf','.mp4','.webm','.wav','.png','.jpg','.jpeg','.pth','.pt','.zip'), 'Unexpected model/media/archive: '+str(p.relative_to(root)))
        require('.git' not in p.parts and '.env'!=p.name,'Unexpected private file')
    manifest=root/'SHA256SUMS.json'
    if manifest.exists():
        require(set(json.loads(manifest.read_text(encoding='utf-8')))=={p.relative_to(root).as_posix() for p in files() if p!=manifest},'Unlisted or missing package files')
        for rel,digest in json.loads(manifest.read_text(encoding='utf-8')).items():
            require(hashlib.sha256((root/rel).read_bytes()).hexdigest()==digest,'Checksum mismatch: '+rel)
    print('Package checks passed.')
if __name__=='__main__':verify(pathlib.Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT)
