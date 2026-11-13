"""Portable LM Studio client; explicit manual server startup, no PC-specific launcher."""
import base64,io,json,os,re,socket,struct,time,urllib.error,urllib.request
from pathlib import Path
import numpy as np
from PIL import Image
def _clean_api_base(api_base):
    value = (api_base or "").strip().rstrip("/")
    if value.endswith("/v1"):
        return value
    return value + "/v1"

def _windows_host_from_resolv_conf():
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8") as handle:
            for line in handle:
                match = re.match(r"\s*nameserver\s+([^\s]+)", line)
                if match:
                    return match.group(1)
    except OSError:
        return None
    return None

def _windows_host_from_default_route():
    """Return the WSL host-side gateway without spawning a shell command."""
    try:
        with open("/proc/net/route", "r", encoding="ascii") as handle:
            next(handle, None)
            for line in handle:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except (OSError, ValueError, struct.error):
        return None
    return None

def _candidate_api_bases(api_base):
    candidates = []
    route_host = _windows_host_from_default_route()
    route_url = f"http://{route_host}:1234/v1" if route_host else None
    for value in (api_base, route_url, "http://127.0.0.1:1234/v1", "http://localhost:1234/v1"):
        if value:
            value = _clean_api_base(value)
            if value not in candidates:
                candidates.append(value)

    # Slower fallbacks are last so a stale DNS address cannot delay the normal WSL route.
    for value in ("http://host.docker.internal:1234/v1",):
        value = _clean_api_base(value)
        if value not in candidates:
            candidates.append(value)
    host = _windows_host_from_resolv_conf()
    if host:
        value = f"http://{host}:1234/v1"
        if value not in candidates:
            candidates.append(value)
    return candidates

def _post_h3_structured(url, payload, timeout, fields):
    import time
    import folder_paths
    limits = {'subject_definitions':700,'summary':300,'retention_analysis':500,'detailed_description':2400,
              'integrated_multimodal_description':2600,'overall_soundscape':350,'non_diegetic_music':200}
    duration_match=re.search(r'lasting ([0-9.]+) seconds',payload['system_prompt'])
    duration=float(duration_match.group(1)) if duration_match else 15
    schema = {'type':'object','properties':{f:{'type':'string','minLength':min(1000,max(160,int(duration*40))) if f in ('detailed_description','integrated_multimodal_description') else 1,'maxLength':limits[f]} for f in fields},
              'required':list(fields),'additionalProperties':False}
    content = payload['input']
    if isinstance(content,list):
        content = [{'type':'text','text':x['content']} if x['type']=='text' else {'type':'image_url','image_url':{'url':x['data_url']}} for x in content]
    body = {'model':payload['model'],'messages':[{'role':'system','content':payload['system_prompt']},{'role':'user','content':content}],
            'temperature':payload['temperature'],'top_p':0.8,'top_k':20,'min_p':0.0,'presence_penalty':1.5,'repeat_penalty':1.0,
            'max_tokens':payload['max_output_tokens'],'stream':True,'reasoning_effort':'none',
            'chat_template_kwargs':{'enable_thinking':False},
            'response_format':{'type':'json_schema','json_schema':{'name':'h3_prompt','strict':True,'schema':schema}}}
    endpoint = url.split('/api/v1/chat')[0]+'/v1/chat/completions'
    req=urllib.request.Request(endpoint,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer lm-studio'})
    parts=[]; ended=False; finish=None; started=last=time.monotonic(); reasoning_chars=0
    repeat_request=re.sub(r'繰り返さ(?:ない|ず)|繰り返し(?:なし|無し)|(?:do not|never|don.t|no)\s+repeat\w*', '', str(payload['input']), flags=re.I)
    allow_repeat=bool(re.search(r'繰り返|繰返|何度|\brepeat\b',repeat_request,re.I))
    last_checked=0
    with urllib.request.urlopen(req,timeout=timeout) as response:
        for raw in response:
            if time.monotonic()-started > max(900,min(1800,int(timeout)*3)): raise RuntimeError('H3 structured conversion total time limit exceeded')
            line=raw.decode().strip()
            if not line.startswith('data:'): continue
            data=line[5:].strip()
            if data=='[DONE]': ended=True; break
            event=json.loads(data)
            if 'error' in event: raise RuntimeError(str(event['error']))
            for choice in event.get('choices',[]):
                delta=choice.get('delta',{}); parts.append(delta.get('content') or '')
                reasoning_chars+=len(delta.get('reasoning_content') or delta.get('reasoning') or '')
                if choice.get('finish_reason'): finish=choice['finish_reason']
            joined=''.join(parts)
            if not allow_repeat and len(joined)-last_checked>=120:
                last_checked=len(joined)
                compact=re.sub(r'\s+',' ',joined[-3000:])
                if re.search(r'(.{40,200}?)\1{3}',compact,re.S):
                    print('[H3 structured] Repeated long passage detected; discard and request rewrite.')
                    ended=True; finish='repetition'; break
            if time.monotonic()-last>=30:
                print(f'[H3 structured] elapsed={time.monotonic()-started:.0f}s chars={sum(map(len,parts))} reasoning_chars={reasoning_chars}')
                last=time.monotonic()
    if not ended: raise RuntimeError('H3 structured stream ended prematurely')
    raw_text=''.join(parts)
    audit=Path(folder_paths.get_temp_directory())/'h3_lm_validation'; audit.mkdir(parents=True,exist_ok=True)
    (audit/f'structured-{time.time_ns()}.json').write_text(json.dumps({'text':raw_text,'finish':finish,'reasoning_chars':reasoning_chars},ensure_ascii=False),encoding='utf-8')
    try:
        parsed=json.loads(raw_text)
        if finish!='stop' or set(parsed)!=set(fields) or any(not isinstance(parsed[f],str) or not 0<len(parsed[f])<=limits[f] for f in fields):
            raise ValueError('incomplete or oversized fields')
        prompt='\n\n'.join(f+': '+parsed[f] for f in fields)
    except (ValueError,TypeError):
        prompt='INVALID_STRUCTURED_OUTPUT: Rewrite within all field limits. '+raw_text[:2000]
    print(f'[H3 structured] completed in {time.monotonic()-started:.1f}s finish={finish} reasoning_chars={reasoning_chars}')
    return {'output':[{'type':'message','content':prompt}]}

def _post_json_stream(url, payload, timeout):
    """LM native SSE: timeout means inactivity; only accept a final chat.end."""
    import time
    request = urllib.request.Request(url, data=json.dumps({**payload, 'stream': True}, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type':'application/json','Authorization':'Bearer lm-studio'}, method='POST')
    started = last_report = time.monotonic()
    limit = max(900, min(1800, int(timeout)*3))
    message_chars = reasoning_chars = 0
    data_lines = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            now = time.monotonic()
            if now-started > limit:
                raise RuntimeError(f'LM Studio stream exceeded total limit {limit}s; incomplete output rejected')
            line = raw.decode('utf-8').rstrip('\r\n')
            if line.startswith('data:'):
                data_lines.append(line[5:].lstrip())
                continue
            if line or not data_lines:
                continue
            event = json.loads('\n'.join(data_lines)); data_lines = []
            kind = event.get('type','')
            if kind == 'error':
                raise RuntimeError('LM Studio stream error: '+str(event.get('error')))
            if kind == 'message.delta': message_chars += len(event.get('content',''))
            if kind == 'reasoning.delta': reasoning_chars += len(event.get('content',''))
            if now-last_report >= 30:
                print(f'[H3 LM stream] elapsed={now-started:.0f}s message_chars={message_chars} reasoning_chars={reasoning_chars}; event={kind}')
                last_report = now
            if kind == 'chat.end':
                result = event.get('result')
                if not isinstance(result,dict): raise RuntimeError('Missing final LM Studio result')
                import folder_paths
                audit_dir = Path(folder_paths.get_temp_directory()) / 'h3_lm_validation'
                audit_dir.mkdir(parents=True, exist_ok=True)
                audit_file = audit_dir / f'response-{time.time_ns()}.json'
                audit_file.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
                print(f'[H3 LM stream] completed in {now-started:.1f}s; stats={result.get("stats",{})}; audit={audit_file}')
                return result
    raise RuntimeError('LM Studio stream ended without chat.end; incomplete output rejected')

def _post_json(url, payload, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer lm-studio"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def _tensor_image_to_pil(image):
    pixels = image[0].detach().cpu().numpy()
    pixels = np.clip(pixels[:, :, :3] * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels, mode="RGB")

def _ready_lm_studio_bases(api_base="http://127.0.0.1:1234/v1", timeout=2):
    """Return reachable LM Studio API bases without spending inference time."""
    ready = []
    for base in _candidate_api_bases(api_base):
        try:
            root = _clean_api_base(base)[: -len("/v1")]
            request = urllib.request.Request(
                root + "/api/v1/models",
                headers={"Authorization": "Bearer lm-studio"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                json.loads(response.read().decode("utf-8"))
            ready.append(base)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.URLError,
            socket.timeout,
        ):
            continue
    if os.name != "nt":
        route_host = _windows_host_from_default_route()
        route_base = (
            _clean_api_base("http://{}:1234/v1".format(route_host))
            if route_host else None
        )
        if route_base in ready:
            ready.remove(route_base)
            ready.insert(0, route_base)
    return ready

def _native_chat_url(api_base):
    base = _clean_api_base(api_base)
    return base[: -len("/v1")] + "/api/v1/chat"

def _auto_start_lm_studio(timeout_seconds):
    return False, 'LM StudioのDeveloper画面でサーバーと画像対応モデルを起動し、モデル名と接続先を確認してください。配布版は自動起動やPC設定変更を行いません。'

class LocalLMHelper:
    SUPPORTS_JSON_SCHEMA = True
    SYSTEM_PROMPT = ''
    def convert(
        self,
        japanese_instruction,
        model,
        api_base,
        temperature,
        max_tokens,
        timeout_seconds,
        fallback_to_japanese,
        extra_rules="",
        reference_image_1=None,
        reference_image_2=None,
        reference_image_3=None,
        reference_image_4=None,
        input_images=None,
        reasoning="off",
    ):
        instruction = (japanese_instruction or "").strip()
        if not instruction:
            return ("", "入力が空です")

        system_prompt = self.SYSTEM_PROMPT
        if extra_rules and extra_rules.strip():
            system_prompt += "\nAdditional user rules:\n" + extra_rules.strip()

        schema_images = [
            image for image in (
                reference_image_1,
                reference_image_2,
                reference_image_3,
                reference_image_4,
            ) if image is not None
        ]
        if input_images is None:
            input_images = schema_images
        elif schema_images:
            input_images = schema_images + list(input_images)

        if input_images and "CONNECTED_VISUAL_ATTACHMENTS:" not in instruction:
            labels = ", ".join(
                "Picture {}".format(index) for index in range(1, len(input_images) + 1)
            )
            instruction = (
                instruction
                + "\n\nCONNECTED_VISUAL_ATTACHMENTS: "
                + labels
                + ". Inspect these images directly and do not invent unchanged visual attributes."
            )

        request_input = instruction
        if input_images:
            request_input = [{"type": "text", "content": instruction}]
            for image in input_images:
                if image is None:
                    continue
                picture = _tensor_image_to_pil(image)
                picture.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                picture.convert("RGB").save(buffer, format="JPEG", quality=92, optimize=True)
                request_input.append({
                    "type": "image",
                    "data_url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
                })

        payload = {
            "model": (model or "qwen-prompt-ja").strip(),
            "system_prompt": system_prompt,
            "input": request_input,
            "temperature": float(temperature),
            "max_output_tokens": int(max_tokens),
            "reasoning": reasoning if reasoning in ("off", "on", "low", "medium", "xhigh") else "off",
            "stream": False,
            "store": False,
        }

        if getattr(self, 'H3_SAMPLING', False):
            payload.update(top_p=0.8, top_k=20, min_p=0.0, repeat_penalty=1.1)

        def request_prompt(request_timeout, bases=None):
            request_errors = []
            for base in (bases or _candidate_api_bases(api_base)):
                try:
                    if getattr(self, 'JSON_SCHEMA', None):
                        try:
                            data = _post_json_schema(_native_chat_url(base), payload, int(request_timeout), self.JSON_SCHEMA)
                        except Exception as exc:
                            if getattr(exc, 'schema_evidence', None):
                                self.JSON_SCHEMA_EVIDENCE = getattr(self, 'JSON_SCHEMA_EVIDENCE', []) + [exc.schema_evidence]
                            raise
                        self.JSON_SCHEMA_EVIDENCE = getattr(self, 'JSON_SCHEMA_EVIDENCE', []) + [data['schema_evidence']]
                    else:
                        data = _post_h3_structured(_native_chat_url(base), payload, int(request_timeout), self.H3_FIELDS) if getattr(self, 'H3_FIELDS', None) else (_post_json_stream if getattr(self, 'STREAM_RESPONSE', False) else _post_json)(_native_chat_url(base), payload, int(request_timeout))
                    prompt = "\n".join(
                        item.get("content", "").strip()
                        for item in data.get("output", [])
                        if item.get("type") == "message" and item.get("content", "").strip()
                    ).strip()
                    prompt = re.sub(r"^```(?:text)?\s*|\s*```$", "", prompt, flags=re.IGNORECASE).strip()
                    if not prompt:
                        raise ValueError("LM Studio returned an empty prompt")
                    return prompt, base, request_errors
                except (
                    OSError,
                    KeyError,
                    IndexError,
                    ValueError,
                    json.JSONDecodeError,
                    urllib.error.URLError,
                    socket.timeout,
                ) as exc:
                    request_errors.append(f"{base}: {exc}")
            return None, None, request_errors

        # Detect availability with the lightweight models endpoint. A healthy local
        # model can legitimately need far more than three seconds for a vision or
        # long-prompt response, so never use an inference timeout as a health check.
        ready_bases = _ready_lm_studio_bases(api_base)
        errors = []
        if ready_bases:
            prompt, base, errors = request_prompt(
                int(timeout_seconds), ready_bases[:1]
            )
            if prompt:
                return (prompt, f"OK: {base} / {payload['model']}")

        started, start_status = _auto_start_lm_studio(timeout_seconds)
        if started:
            retry_bases = _ready_lm_studio_bases(api_base)
            prompt, base, retry_errors = request_prompt(
                int(timeout_seconds), retry_bases[:1] or None
            )
            if prompt:
                return (prompt, f"OK（自動起動）: {base} / {payload['model']}")
            errors.extend(retry_errors)
        else:
            errors.append(start_status)

        message = "LM Studioへ接続できませんでした: " + " | ".join(errors)
        if fallback_to_japanese:
            return (instruction, message + " / 日本語原文をそのまま使用")
        raise RuntimeError(message)


def _unique_json_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:
            raise ValueError('duplicate typed JSON field: '+key)
        value[key]=item
    return value

def _post_json_schema(url, payload, timeout, schema):
    """LM Studio JSON grammar; no fallback to an external model or partial JSON.

    Protocol: https://lmstudio.ai/docs/developer/openai-compat/structured-output
    """
    import folder_paths
    content = payload['input']
    if isinstance(content, list):
        content = [{'type':'text', 'text':x['content']} if x['type']=='text'
                   else {'type':'image_url', 'image_url':{'url':x['data_url']}} for x in content]
    body = {'model':payload['model'], 'temperature':payload['temperature'],
            'messages':[{'role':'system','content':payload['system_prompt']},
                        {'role':'user','content':content}],
            'max_tokens':payload['max_output_tokens'], 'stream':True,
            'reasoning_effort':'none', 'chat_template_kwargs':{'enable_thinking':False},
            'response_format':{'type':'json_schema','json_schema':{
                'name':'h3_typed_analysis','strict':True,'schema':schema}}}
    endpoint = url.split('/api/v1/chat')[0]+'/v1/chat/completions'
    request = urllib.request.Request(endpoint, data=json.dumps(body,ensure_ascii=False).encode(),
                 headers={'Content-Type':'application/json','Authorization':'Bearer lm-studio'})
    import hashlib
    identity = hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    output = Path(folder_paths.get_output_directory())/'h3-plan-audits'
    output.mkdir(parents=True,exist_ok=True)
    receipt_path = output/f'{identity}_{os.getpid()}_{time.time_ns()}.json'
    started = last_report = time.monotonic()
    parts=[]; finish=None; ended=False; failure=None
    record={'schema':'h3.typed_lm_response.v1','request_sha256':identity,
            'model':payload['model'],'max_tokens':payload['max_output_tokens'],'timeout_seconds':timeout,
            'response_schema':schema,'status':'started','system_prompt':payload['system_prompt'],
            'input':payload['input'] if isinstance(payload['input'],str) else [
                item if item['type']=='text' else {'type':'image','sha256':hashlib.sha256(item['data_url'].encode()).hexdigest()}
                for item in payload['input']]}
    try:
        idle, total = structured_stream_budget(payload, float(timeout), float(timeout))
        for raw in stream_lines(request, idle, total):
            elapsed=time.monotonic()-started
            line=raw.decode('utf-8').strip()
            if not line.startswith('data:'):
                continue
            data=line[5:].strip()
            if data=='[DONE]':
                ended=True;break
            event=json.loads(data)
            if 'error' in event:
                raise RuntimeError(str(event['error']))
            for choice in event.get('choices',[]):
                if choice.get('index',0)!=0:
                    raise ValueError('unexpected additional typed response')
                delta=choice.get('delta',{});parts.append(delta.get('content') or '')
                if choice.get('finish_reason'):
                    finish=choice['finish_reason']
            if time.monotonic()-last_report>=30:
                print(f'[H3 typed analysis] elapsed={elapsed:.0f}s chars={sum(map(len,parts))}')
                last_report=time.monotonic()
        text=''.join(parts).strip()
        if not ended or finish!='stop':
            raise ValueError(f'incomplete typed JSON response: done={ended}, finish={finish}')
        parsed=json.loads(text,object_pairs_hook=_unique_json_object,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON constant')))
        from jsonschema import Draft202012Validator, ValidationError
        Draft202012Validator.check_schema(schema)
        try:
            Draft202012Validator(schema).validate(parsed)
        except ValidationError as exc:
            raise ValueError('typed response violates its schema: '+exc.message) from exc
        record['schema_validation']='passed'
        record['status']='returned'
    except Exception as exc:
        failure=exc
        record.update(status='error',exception_type=type(exc).__name__,reason=str(exc))
        if isinstance(exc, urllib.error.HTTPError):
            record['http_status'] = exc.code
            try:
                error_body = exc.read(2049)
                record['http_error_body'] = error_body[:2048].decode('utf-8', 'replace')
                record['http_error_body_truncated'] = len(error_body) > 2048
            except (OSError, ValueError):
                record['http_error_body_unavailable'] = True
        raise
    finally:
        record.update(raw_response=''.join(parts),finish_reason=finish,stream_done=ended,
                      elapsed_seconds=time.monotonic()-started)
        temporary=receipt_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,receipt_path)
        if failure is not None:
            failure.schema_evidence={'ref':str(receipt_path),'sha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest()}
        print(f"[H3 typed analysis] {record['status']} elapsed={record['elapsed_seconds']:.1f}s receipt={receipt_path}")
    return {'output':[{'type':'message','content':text}],
            'schema_evidence':{'ref':str(receipt_path),'sha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest()}}


def ensure_lm_studio_model_ready(model, api_base, timeout_seconds):
    for base in _ready_lm_studio_bases(api_base):
        try:
            root = _clean_api_base(base)[:-len('/v1')]
            data = _post_model_list(root + '/api/v1/models')
            for item in data.get('models', []):
                identifiers = {item.get('key'), item.get('id'), item.get('identifier')}
                identifiers.update(x.get('id') for x in item.get('loaded_instances', []))
                if model in identifiers and item.get('loaded_instances'):
                    return True, 'Configured model is loaded'
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return False, 'Start LM Studio and load the configured model manually: ' + model

def _post_model_list(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Authorization':'Bearer lm-studio'}), timeout=2) as response:
        return json.loads(response.read().decode('utf-8'))

def structured_stream_budget(payload, idle, total):
    return min(float(idle), float(total)), float(total)

def stream_lines(request, idle, total):
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=idle) as response:
        for raw in response:
            if time.monotonic() - started > total:
                raise RuntimeError('LM Studio structured stream exceeded total time budget')
            yield raw
