"""Verify a source-only KomaOrbit package without installing or downloading anything."""
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
ALLOWED_SUFFIXES = {'.py', '.js', '.mjs', '.json', '.md', '.txt', '.html', '.css', '.ini', '.mmpose', '.mmdetection', ''}
IGNORED = {'__pycache__', '.pytest_cache', '.git'}


def payload_files(root=ROOT):
    return sorted(p for p in root.rglob('*') if p.is_file()
                  and not IGNORED.intersection(p.relative_to(root).parts)
                  and p.suffix != '.pyc' and p.name != 'SHA256SUMS.json')


def check_source(root=ROOT):
    root = Path(root)
    files = payload_files(root)
    for path in files:
        rel = path.relative_to(root).as_posix()
        assert not path.is_symlink(), f'Symlink in source: {rel}'
        assert path.suffix in ALLOWED_SUFFIXES, f'Non-source file: {rel}'
        assert not any(s in path.parts for s in ('worldmirror', 'renderer', 'media', 'input', 'output')), f'Runtime or media directory: {rel}'
        data = path.read_bytes()
        text = data.decode('utf-8-sig')
        credential_pattern = r'(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{30,}|-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----)'
        assert not re.search(credential_pattern, text), f'Credential-like data: {rel}'
        assert '/home/'+'furu/' not in text and 'sx7170'+'c@' not in text and 'C:/Users/'+'furu' not in text, f'Private path/contact: {rel}'
        if path.suffix == '.py':
            ast.parse(text, filename=rel)
    graph = json.loads((root/'KomaOrbit.json').read_text())
    nodes = {n['id']: n for n in graph['nodes']}
    links = {l[0]: l for l in graph['links']}
    assert len(nodes) == len(graph['nodes']) == 78, 'Missing/duplicate workflow nodes'
    assert len(links) == len(graph['links']) == 161, 'Missing/duplicate workflow links'
    for n in nodes.values():
        assert not any((k.startswith('h3_') and k != 'h3_rigid_preview') or k == 'Last Time Model File' for k in n.get('properties', {})), 'Cached output state in workflow'
        for slot, inp in enumerate(n.get('inputs', [])):
            lid = inp.get('link')
            if lid is not None:
                link = links[lid]
                assert link[3:5] == [n['id'], slot], f'Invalid target socket: {lid}'
                assert link[1] in nodes and link[2] < len(nodes[link[1]].get('outputs', [])), f'Invalid source: {lid}'
                assert lid in nodes[link[1]]['outputs'][link[2]].get('links', []), f'Missing reciprocal link: {lid}'
        if n['type'] == 'LoadImage':
            assert n['widgets_values'][0] == '', 'Input image included'
    assert nodes[54]['properties'].get('h3_rigid_preview') is True, 'Final viewer controls disabled'
    assert all(n['id'] == 54 or 'h3_rigid_preview' not in n.get('properties', {}) for n in nodes.values()), 'Unexpected viewer marker'
    assert nodes[6].get('widgets_values_named', {}).get('image', '') == '', 'Cached named input image'
    assert 'single_image_only' not in nodes[9].get('widgets_values_named', {}), 'Private core input remains'
    assert nodes[54].get('widgets_values_named', {}).get('viewport_state', '') == '', 'Cached named viewport'
    viewer_names = ('filename_prefix', 'viewport_state', 'width', 'height', 'h3_scene_save')
    assert nodes[54].get('widgets_values_named') == dict(zip(viewer_names, nodes[54]['widgets_values'])), 'Conflicting final viewer widget values'
    assert nodes[54]['properties']['Scene Config'].get('models') == [], 'Cached scene models'
    assert nodes[54]['properties']['Scene Config'].get('backgroundImage') == '', 'Cached scene background'
    assert nodes[110]['type'] == 'H3OrbitSubjectDetail', 'Face recovery node missing'
    for name, expected in [('original_image', 6), ('reference_image', 42), ('scene_prefix', 109)]:
        socket = next(i for i in nodes[110]['inputs'] if i['name'] == name)
        assert links[socket['link']][1] == expected, f'Incorrect face connection: {name}'
    assert nodes[17]['widgets_values'][0] == '上下3段階'
    assert nodes[17]['widgets_values'][2] == '固定3Dを新規生成'
    assert nodes[36]['widgets_values'][0] == '背景用 FL2VA（73枚）'
    assert nodes[48]['widgets_values'][2] == 50
    assert nodes[29]['widgets_values'][2] == 4
    assert nodes[92]['widgets_values'][2] == 25
    note = [n for n in nodes.values() if n['type'] == 'MarkdownNote']
    assert len(note) == 1 and note[0]['pos'][0] < min(n['pos'][0] for n in nodes.values() if n['id'] != note[0]['id'])
    for label in ('左ボタン', '右ボタン', 'ホイール', 'README.md'):
        assert label in note[0]['widgets_values'][0]
    assert note[0].get('widgets_values_named', {}).get('text', note[0]['widgets_values'][0]) == note[0]['widgets_values'][0], 'Conflicting note text'
    models = json.loads((root/'models.json').read_text())
    model_map = {m['name']: m for m in models}
    loader_types = {'UNETLoader', 'LoraLoaderModelOnly', 'CLIPLoader', 'VAELoader', 'CLIPVisionLoader', 'LoadBackgroundRemovalModel', 'QwenImage21UnionLoader', 'UpscaleModelLoader', 'H3OrbitSubjectDetail'}
    for n in nodes.values():
        if n['type'] in loader_types:
            model = model_map[n['widgets_values'][0]]
            assert n['properties'].get('models') == [model], f'Missing/wrong download metadata: {n["id"]}'
            assert model['url'].startswith('https://huggingface.co/')
    expected = {'ComfyUI-H3-Orbit', 'Comfyui-Qwen-Image-2.1-Fun-Controlnet-Union-T8', 'ComfyUI-Idol'}
    assert {p.name for p in (root/'custom_nodes').iterdir() if p.is_dir()} == expected
    for required in ('LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'VALIDATION.md',
                     'custom_nodes/ComfyUI-Idol/LICENSE', 'custom_nodes/ComfyUI-Idol/NOTICE',
                     'custom_nodes/ComfyUI-Idol/LICENSE-APACHE-2.0.txt',
                     'custom_nodes/ComfyUI-H3-Orbit/LICENSE',
                     'custom_nodes/Comfyui-Qwen-Image-2.1-Fun-Controlnet-Union-T8/LICENSE',
                     'custom_nodes/Comfyui-Qwen-Image-2.1-Fun-Controlnet-Union-T8/LICENSE-GPL-3.0.txt',
                     'custom_nodes/ComfyUI-H3-Orbit/vendor/anime_face_detector_runtime/LICENSE',
                     'custom_nodes/ComfyUI-H3-Orbit/vendor/anime_face_detector_runtime/THIRD_PARTY_LICENSES.md',
                     'custom_nodes/ComfyUI-H3-Orbit/vendor/anime_face_detector_runtime/anime_face_detector/_vendor/LICENSE.mmpose',
                     'custom_nodes/ComfyUI-H3-Orbit/vendor/anime_face_detector_runtime/anime_face_detector/_vendor/LICENSE.mmdetection'):
        assert (root/required).is_file(), f'Missing notice/document: {required}'
    gpl = (root/'custom_nodes/Comfyui-Qwen-Image-2.1-Fun-Controlnet-Union-T8/LICENSE-GPL-3.0.txt').read_text()
    assert 'GNU GENERAL PUBLIC LICENSE' in gpl and len(gpl) > 30000
    assert '<!-- bilingual-readme: paired english-japanese -->' in (root/'README.md').read_text()
    return files


def digest_map(root=ROOT):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in payload_files(root)}


def verify(root=ROOT):
    root = Path(root)
    files = check_source(root)
    expected = json.loads((root/'SHA256SUMS.json').read_text())
    actual = digest_map(root)
    assert expected == actual, f'Manifest mismatch: missing={sorted(set(expected)-set(actual))}, extra={sorted(set(actual)-set(expected))}, changed={[n for n in set(actual)&set(expected) if actual[n]!=expected[n]]}'
    print(f'PASS: {len(files)} source files, 78 nodes, 161 links, model metadata, notices and all SHA-256 values')


if __name__ == '__main__':
    verify()
