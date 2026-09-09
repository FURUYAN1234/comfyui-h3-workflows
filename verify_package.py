"""Verify an extracted package without installing nodes or downloading models."""
import ast,hashlib,json,pathlib,re,sys
ROOT=pathlib.Path(__file__).resolve().parent
def require(value,message):
    if not value:raise AssertionError(message)
def verify(root=ROOT):
    paths=list((root/'workflows').glob('*.json'))
    require(len(paths)==3,'Exactly three sample workflows are required')
    models=json.loads((root/'models.json').read_text(encoding='utf-8'))
    required={'comfyui-h3-standard-prompt','ComfyUI-MiniMax-H3-Long-Video','ComfyUI-H3-AudioRefine','ComfyUI-PlagueKind-Nodes','ComfyUI-Custom-Scripts'}
    require({p.name for p in (root/'custom_nodes').iterdir() if p.is_dir()}==required,'Custom-node package list differs')
    for f in paths:
        require('＿２' not in f.name,'Personal variant included')
        d=json.loads(f.read_text(encoding='utf-8')); ns={n['id']:n for n in d['nodes']};links={l[0]:l for l in d['links']}
        kind=f.name.split('_')[0];expected={'T2V':'T2VA','I2V':'I2VA','Ref2V':'Ref2VA (R2V)'}[kind]
        require(ns[138]['widgets_values'][1]==expected,'Wrong prompt mode')
        require(ns[138]['widgets_values'][5]==0.7,'LM sampling differs')
        require(ns[138]['widgets_values'][8] is False,'Raw fallback enabled')
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
    for required_file in ('quality_guard.py','standard.py','lm_client.py','input_schema.json'):
        require((root/'custom_nodes/comfyui-h3-standard-prompt'/required_file).is_file(),'Missing portable dependency: '+required_file)
    for p in (root/'custom_nodes').rglob('*.py'):ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
    for name in required-{'comfyui-h3-standard-prompt'}:require((root/'custom_nodes'/name/'LICENSE').is_file(),'Missing third-party license')
    for p in root.rglob('*'):
        if not p.is_file():continue
        require('__pycache__' not in p.parts and p.suffix!='.pyc','Runtime cache included')
        require(p.suffix.lower() not in ('.safetensors','.gguf','.mp4','.webm','.wav','.png','.jpg','.jpeg','.pth','.pt','.zip'), 'Unexpected model/media/archive: '+str(p.relative_to(root)))
        require('.git' not in p.parts and '.env'!=p.name,'Unexpected private file')
    manifest=root/'SHA256SUMS.json'
    require(manifest.is_file(),'Missing SHA256SUMS.json')
    version=json.loads((root/'VERSION.json').read_text(encoding='utf-8'))
    require(set(version['workflow_files'])=={p.name for p in paths},'Version workflow inventory differs')
    require(all('_v'+version['version']+'.json' in p.name for p in paths),'Workflow version differs')
    if manifest.exists():
        require(set(json.loads(manifest.read_text(encoding='utf-8')))=={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p!=manifest},'Unlisted or missing package files')
        for rel,digest in json.loads(manifest.read_text(encoding='utf-8')).items():
            require(hashlib.sha256((root/rel).read_bytes()).hexdigest()==digest,'Checksum mismatch: '+rel)
    print('Package checks passed.')
if __name__=='__main__':verify(pathlib.Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT)
