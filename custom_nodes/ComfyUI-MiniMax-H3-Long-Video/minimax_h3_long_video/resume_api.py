"""Replay the recorded execution inputs, including its seed and resolved folder."""
import copy
import hashlib
import json
from pathlib import Path
import uuid


def resume_request(graph, node_id, relative_folder, workflow=None):
    graph = copy.deepcopy(graph)
    node_id = str(node_id)
    if graph[node_id]['class_type'] != 'MiniMaxH3LongReferenceSampler':
        raise ValueError('Invalid resume sampler')
    # Credentials are memory-only in this workflow. Do not persist inline keys
    # from an unrelated custom node if it has been added to the same graph.
    for node in graph.values():
        for name, value in node.get('inputs', {}).items():
            if any(word in name.lower() for word in ('api_key', 'password', 'access_token', 'secret')) and value:
                raise ValueError('Resume snapshot cannot contain inline credentials')
    graph[node_id]['inputs'].update(cache_name=relative_folder, resume=True,
                                    reroll_from_segment=-1, stop_after_segment=-1,
                                    resume_execution_token=uuid.uuid4().hex)
    # A saved resume snapshot must feed the reconstructed master video into
    # GetVideoComponents. Older snapshots could retain a missing/stale link,
    # causing the UI to report the required `video` input as absent.
    sampler_id = node_id
    for candidate_id, candidate in graph.items():
        if candidate.get('class_type') == 'GetVideoComponents':
            inputs = candidate.setdefault('inputs', {})
            link = inputs.get('video')
            if not (isinstance(link, list) and len(link) == 2 and str(link[0]) in graph):
                inputs['video'] = [sampler_id, 0]
            break
    return {'schema': 1, 'node_id': node_id, 'prompt': graph,
            'workflow': copy.deepcopy(workflow)}


def _resume_identity(data):
    """Identify one saved graph while ignoring only replay bookkeeping."""
    graph = copy.deepcopy(data['prompt'])
    node = graph[str(data['node_id'])]['inputs']
    for name in ('cache_name', 'resume', 'reroll_from_segment', 'reroll_feedback',
                 'stop_after_segment', 'resume_execution_token', 'video-preview'):
        node.pop(name, None)
    for item in graph.values():
        inputs = item.get('inputs') or {}
        # The frontend alternates between an absent and empty preview input.
        if inputs.get('video-preview') in (None, ''):
            inputs.pop('video-preview', None)
    payload = json.dumps(graph, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _checkpoint_state(project):
    manifest_path = project / 'manifest.json'
    context = project / 'recovery_inputs' / 'context.json'
    review = project / 'recovery_inputs' / 'review.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        entries = manifest.get('segments') or []
        count = sum(isinstance(row, dict)
                    and (project / 'latents' / str(row.get('file', ''))).is_file()
                    for row in entries)
        ready = (manifest.get('status') in ('complete', 'complete_with_audit_failure')
                 and bool(entries) and count == len(entries)
                 and context.is_file() and review.is_file())
        return count, ready
    except (OSError, ValueError, TypeError):
        return 0, False


def _completed_fallback(project, data):
    """Recover from a failed replay folder that never saved any segment."""
    identity = _resume_identity(data)
    candidates = sorted(
        (item for item in project.parent.iterdir()
         if item.is_dir() and item != project),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if not _checkpoint_state(candidate)[1]:
            continue
        try:
            saved = json.loads((candidate / 'resume_prompt.json').read_text(encoding='utf-8'))
            if saved.get('schema') == 1 and _resume_identity(saved) == identity:
                return candidate, saved
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return project, data


def read_resume_request(output_root, project_name):
    root = Path(output_root).resolve()
    if not isinstance(project_name, str) or not project_name:
        raise ValueError('Resume project is missing')
    project = (root / project_name).resolve()
    if not project.is_relative_to(root):
        raise ValueError('Resume project outside output')
    data = json.loads((project / 'resume_prompt.json').read_text(encoding='utf-8'))
    if data.get('schema') != 1:
        raise ValueError('Unsupported resume snapshot')
    checkpoint_count, _ = _checkpoint_state(project)
    if checkpoint_count == 0:
        project, data = _completed_fallback(project, data)
    # Validate again at the read boundary and bind the replay to this folder.
    result = resume_request(data['prompt'], data['node_id'], str(project.relative_to(root)), data.get('workflow'))
    # A complete checkpoint bundle can restore its reference, confirmed
    # reading, seed and segment latents locally.  Let that replay reach the
    # sampler after a server restart; if a new cloud inspection or generation
    # is actually needed, the backend still stops with the credential error.
    _, local_ready = _checkpoint_state(project)
    result['credential_required'] = not local_ready
    result['project'] = str(project.relative_to(root))
    return result


def register_routes():
    from aiohttp import web
    import folder_paths
    from server import PromptServer

    @PromptServer.instance.routes.post('/h3_long_video/resume_inputs')
    async def resume_inputs(request):
        try:
            data = read_resume_request(folder_paths.get_output_directory(),
                                       (await request.json()).get('project'))
        except (ValueError, KeyError, OSError):
            return web.json_response({'error': '保存済みの再開情報を読み込めません。保存先と生成履歴を確認してください。'}, status=400)
        return web.json_response(data)
