"""Verify an extracted package without installing nodes or downloading models."""
import ast,hashlib,json,pathlib,re,sys
ROOT=pathlib.Path(__file__).resolve().parent
def require(value,message):
    if not value:raise AssertionError(message)
def verify_bilingual_readme_text(readme):
    marker='<!-- bilingual-readme: paired english-japanese -->'
    require(readme.count(marker)==1,'README paired bilingual marker is missing or duplicated')
    require('<!-- bilingual-readme: start english -->' not in readme and '<!-- bilingual-readme: start japanese -->' not in readme,'README uses obsolete split language sections')
    require('[English](#english) | [日本語](#日本語)' not in readme,'README uses obsolete split-language navigation')
    require(len(readme)>=24000,'README bilingual content is unexpectedly short')
    paired_headings=(
        '## What you can do / できること',
        '## Changes in v1.2.0 / v1.2.0の変更点',
        '## Package contents / 配布内容',
        '## Requirements / 必要な環境',
        '## Installation / インストール',
        '## Install the four H3 model files / H3モデル4ファイルを配置する',
        '## Configure LM Studio / LM Studioを設定する',
        '## Configure Whisper for Japanese speech review / 日本語音声検査用Whisperを設定する',
        '## Generate the first video / 最初の動画を生成する',
        '## Create a music video from YuE2 or another singing system / YuE2等の曲からMVを作る',
        '## Choose the correct prompt field / プロンプト入力欄の使い分け',
        '## Default duration, resolution, and audio settings / 秒数・解像度・音の既定値',
        '## Output locations / 保存先',
        '## Resume or regenerate from a segment / 途中から再開・区間を再生成する',
        '## Troubleshooting / よくあるトラブル',
        '## Validated scope and limitations / 検証済みの範囲と制限',
        '## Privacy and network access / プライバシーと通信',
        '## Licenses / ライセンス',
        '## Related documentation / 関連ドキュメント',
    )
    for heading in paired_headings:
        require(readme.count(heading)==1,'Missing or duplicated bilingual README section: '+heading)
    for token in ('T2V','I2V','Ref2V','MV_H3_YuE2-LMStudio_v1.2.0.json','requirements.txt','models.json','LM Studio','Whisper','verify_package.py','VALIDATION.md','LICENSES_AND_NOTICES.md','ComfyUI-YuE2-Japanese'):
        require(token in readme,'README content token missing: '+token)
    require(readme.count(' / ')>=50,'README does not contain enough paired English/Japanese units')
def verify_bilingual_readme(root):
    verify_bilingual_readme_text((root/'README.md').read_text(encoding='utf-8'))
def verify(root=ROOT):
    verify_bilingual_readme(root)
    paths=list((root/'workflows').glob('*.json'))
    require(len(paths)==4,'Exactly four T2V/I2V/Ref2V/MV workflows are required')
    version=json.loads((root/'VERSION.json').read_text(encoding='utf-8'))
    require(version['product_id']=='h3-t2v-i2v-ref2v-mv','Wrong product')
    require(version['archive_prefix']=='H3_T2V-I2V-Ref2V-MV','Ambiguous archive name')
    require(re.fullmatch(r'\d{14}',version['built_at_jst']),'Timestamp must include seconds')
    require((root/'VERSION').read_text().strip()==version['version'],'VERSION mismatch')
    models=json.loads((root/'models.json').read_text(encoding='utf-8'))
    required={'comfyui-h3-standard-prompt','ComfyUI-MiniMax-H3-Long-Video','ComfyUI-H3-AudioRefine','ComfyUI-PlagueKind-Nodes','ComfyUI-Custom-Scripts','comfyui-mv-workflow'}
    require({p.name for p in (root/'custom_nodes').iterdir() if p.is_dir()}==required,'Custom-node package list differs')
    for f in paths:
        require('＿２' not in f.name,'Personal variant included')
        d=json.loads(f.read_text(encoding='utf-8')); ns={n['id']:n for n in d['nodes']};links={l[0]:l for l in d['links']}
        kind=f.name.split('_')[0]
        for n in ns.values():
            if kind!='MV':require('widgets_values_named' not in n,'Stale named-widget state remains')
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
        if kind=='MV':
            require(f.name=='MV_H3_YuE2-LMStudio_v1.2.0.json','MV workflow name differs')
            require(ns[200]['type']=='MVAssetBundlePrepare' and ns[201]['type']=='MVFinalize','MV bridge/finalizer missing')
            require(ns[204]['type']=='LoadImage','Standard ComfyUI image input missing')
            require(ns[200]['widgets_values'][:2]==['mv-assets/YOUR_BUNDLE_FOLDER',''],'Private or ambiguous MV input remains')
            require(ns[200]['widgets_values'][3:7]==['任意秒数',0.0,20.0,1.0],'MV test duration/fade defaults differ')
            require(ns[200]['widgets_values'][7:10]==['音声解析で整列（推奨）','models/whisper/whisper-large-v3-turbo','参照画像に自動追従'],'Subtitle/Whisper/style defaults differ')
            require(ns[138]['widgets_values'][0]=='MVの雰囲気や演出は全部おまかせ','MV Japanese direction default differs')
            require(ns[138]['widgets_values'][10]=='','Raw fallback prompt must stay empty')
            require(ns[138]['widgets_values'][11:]==['39',True],'MV context or GPU handoff differs')
            require(ns[124]['widgets_values']==['simple',4,1] and ns[161]['widgets_values']==[0.9,'64',8192,0,True,True],'MV Fused4/SLA settings differ')
            require(ns[201]['widgets_values'][-1]=='video/MV','MV output folder differs')
            notes='\n'.join(n['widgets_values'][0] for n in ns.values() if n['type']=='MarkdownNote')
            for text in ('YuE2','音源末尾まで（可変尺）','字幕','リップシンク','2D','3D','実写','LM Studio','プレビュー','ダウンロード'):
                require(text in notes,'Missing MV inline instructions: '+text)
            print('Workflow OK:',kind)
            continue
        expected={'T2V':'T2VA','I2V':'I2VA','Ref2V':'Ref2VA (R2V)'}[kind]
        require(ns[138]['widgets_values'][1]==expected,'Wrong prompt mode')
        require(ns[138]['widgets_values'][11:]==['39',True],'Context or GPU handoff setting differs')
        require('H3_T2V-I2V-Ref2V' in f.name and version['built_at_jst'] in f.name,'Workflow name ambiguous')
        require(ns[138]['widgets_values'][5]==0.7,'LM sampling differs')
        require(ns[138]['widgets_values'][8] is False,'Raw fallback enabled')
        require(bool(ns[138]['widgets_values'][0].strip()) and ns[138]['widgets_values'][10]=='','Sample input route differs')
        require(ns[123]['widgets_values']==['res_multistep'],'Sampler changed')
        require(ns[124]['widgets_values']==['simple',4,1],'Video steps changed')
        require(ns[161]['widgets_values']==[0.9,'64',8192,0,True,True],'SLA configuration changed')
        require(ns[150]['widgets_values'][13:]==[2,0.5,False,True,True,5,'',-1],'Audio steps or prompt/duration policy changed')
        require(ns[115]['widgets_values']==['16:9 (Widescreen)',0.4,32],'Resolution default changed')
        require(ns[132]['widgets_values']==[15.0],'Default seconds changed')
        for n in ns.values():
            if n['type']=='ShowText|pysssss':require(n.get('widgets_values')==[''],'Generated text remains')
        def linked(name):return next((i.get('link') for i in ns[150]['inputs'] if i['name']==name),None)
        require(linked('prompt_plan') is not None and linked('audio_refine_model') is not None,'Required generation connection missing')
        def origin(node_id,name):
            item=next(i for i in ns[node_id]['inputs'] if i['name']==name)
            return links[item['link']][1:3] if item.get('link') is not None else None
        require(origin(150,'prompt')==[138,0] and origin(150,'prompt_plan')==[138,4],'Final prompt/plan route differs')
        require(origin(150,'length')==[138,2] and origin(150,'max_raw_frames')==[138,3],'Duration route differs')
        require(origin(150,'model')==[161,0] and origin(150,'audio_refine_model')==[127,0],'Model paths differ')
        if kind=='T2V':require(not linked('first_frame') and not any(i.get('link') for i in ns[150]['inputs'] if i['name'].startswith('ref_images.')),'T2V conditioning differs')
        if kind=='I2V':require(origin(150,'first_frame')==[137,0],'Start-frame path differs')
        if kind=='Ref2V':
            for idx,node_id in enumerate([137,155,156,157,158]):require(origin(150,'ref_images.ref_image_'+str(idx))==[node_id,0],'Reference source differs')
        if kind=='I2V':require(linked('first_frame') is not None,'I2V does not use first frame')
        if kind=='T2V':require(not any(n['type']=='LoadImage' for n in ns.values()),'T2V unexpectedly requires an image')
        if kind=='Ref2V':
            ims=[n for n in ns.values() if n['type']=='LoadImage'];require(len(ims)==5,'Ref2V must have five image slots')
            require(sum(n.get('mode',0)==0 for n in ims)==1,'Only the first reference is enabled by default')
        notes='\n'.join(n['widgets_values'][0] for n in ns.values() if n['type']=='MarkdownNote')
        for text in ('LM Studio','可変尺','BGM','diffusion_models/','text_encoders/','vae/','変える場所','Duration: 15 seconds'):
            require(text in notes,'Missing inline instructions: '+text)
        print('Workflow OK:',kind)
    for required_file in ('web/lm_studio_execution_status.js','quality_guard.py','standard.py','lm_client.py','input_schema.json','lm_device.py'):
        require((root/'custom_nodes/comfyui-h3-standard-prompt'/required_file).is_file(),'Missing portable dependency: '+required_file)
    for p in (root/'custom_nodes').rglob('*.py'):ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
    for name in required-{'comfyui-h3-standard-prompt'}:require((root/'custom_nodes'/name/'LICENSE').is_file(),'Missing third-party license')
    require(not (root/'custom_nodes/ComfyUI-Custom-Scripts/pysssss.json').exists(),'Personal UI configuration included')
    cfg=json.loads((root/'custom_nodes/ComfyUI-MiniMax-H3-Long-Video/minimax_h3_long_video/local_audio_audit.json').read_text())
    require(cfg=={'enabled':True,'model_path':''},'Private/disabled speech configuration')
    for n in ['continuity_audit.py','local_audio_audit.py']:
        require((root/'custom_nodes/ComfyUI-MiniMax-H3-Long-Video/minimax_h3_long_video'/n).is_file(),'Missing speech/visual guard')
    require((root/'configure_audio_audit.py').is_file(),'Missing speech setup helper')
    for p in root.rglob('*'):
        if not p.is_file():continue
        require('__pycache__' not in p.parts and p.suffix!='.pyc','Runtime cache included')
        rel=p.relative_to(root)
        media=p.suffix.lower() in ('.png','.jpg','.jpeg')
        require(p.suffix.lower() not in ('.safetensors','.gguf','.mp4','.webm','.wav','.pth','.pt','.zip') and (not media or rel.parts[:2]==('docs','assets')), 'Unexpected model/media/archive: '+str(rel))
        require('.git' not in p.parts and '.env'!=p.name and not p.name.endswith('.bak'),'Unexpected private file')
        if p.suffix in ('.py','.json','.md','.txt','.js'):
            text=p.read_text(encoding='utf-8-sig')
            require(not re.search(r'sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{20,}|/home/[A-Za-z0-9_-]+/|[A-Za-z]:[/\\]+Users[/\\]+[A-Za-z0-9_-]+[/\\]',text),'Private data in '+str(p.relative_to(root)))
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
