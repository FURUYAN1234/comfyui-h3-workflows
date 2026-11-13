"""Visual-only continuity checks, including actual delivered frame selection on CPU."""
import ast
import importlib.util
import json
import math
from pathlib import Path
import re
import tempfile
import types
import unittest
import torch
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('three_mode_audit',ROOT/'minimax_h3_long_video/continuity_audit.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
motion_spec=importlib.util.spec_from_file_location('motion_witness_test',ROOT/'minimax_h3_long_video/motion_witness.py')
motion=importlib.util.module_from_spec(motion_spec);motion_spec.loader.exec_module(motion)

_AUDIT_HELPER_NAMES = {
    '_audit_contact_pages', '_require_combined_appearance',
    '_visual_cutaway_mode', '_discard_unscheduled_cutaway_issues',
    '_reconcile_focused_visibility',
    '_local_visual_audit_bases',
}
_AUDIT_HELPERS = [node for node in ast.parse(
    (ROOT / 'minimax_h3_long_video/nodes.py').read_text(encoding='utf-8')).body
    if isinstance(node, ast.FunctionDef) and node.name in _AUDIT_HELPER_NAMES]

class AuditInterrupted(Exception):
    pass

def _audit_test_namespace(ns):
    ns.setdefault("comfy",types.SimpleNamespace(model_management=types.SimpleNamespace(
        throw_exception_if_processing_interrupted=lambda:None, InterruptProcessingException=AuditInterrupted)))
    ns.update(ImageFont=ImageFont, re=re, math=math, torch=torch, Image=Image, ImageDraw=ImageDraw)
    ns.setdefault('people_free_sample_indices', m.people_free_sample_indices)
    ns.setdefault('duplicate_cast_audit_instruction', m.duplicate_cast_audit_instruction)
    ns.setdefault('audit_instruction', m.audit_instruction)
    ns.setdefault('appearance_instruction', m.appearance_instruction)
    ns.setdefault('parse_verdict', m.parse_verdict)
    ns.setdefault('composition_observation_schema', m.composition_observation_schema)
    ns.setdefault('composition_observation_instruction', m.composition_observation_instruction)
    ns.setdefault('parse_composition_observations', m.parse_composition_observations)
    ns.setdefault('visual_verdict_schema', m.visual_verdict_schema)
    ns.setdefault('duplicate_witness_schema', m.duplicate_witness_schema)
    ns.setdefault('duplicate_witness_instruction', m.duplicate_witness_instruction)
    ns.setdefault('parse_duplicate_witness', m.parse_duplicate_witness)
    ns.setdefault('opening_plan_match_instruction',m.opening_plan_match_instruction)
    ns.setdefault('reconcile_opening_space',m.reconcile_opening_space)
    for name in ('motion_witness_schema','motion_witness_instruction','parse_motion_witness'):
        ns.setdefault(name,getattr(motion,name))
    exec(compile(ast.Module(body=_AUDIT_HELPERS, type_ignores=[]), 'audit-helpers', 'exec'), ns)
    return ns

class FocusedScopeRegressionTests(unittest.TestCase):
    def test_clean_opening_preserves_later_scene_reset(self):
        issue={'kind':'scene_reset','frame':7,'evidence':'Unplanned room replacement at frame 7'}
        ns=_audit_test_namespace({})
        result=ns['_reconcile_focused_visibility']({'pass':False,'issues':[issue]},'scene_reset',(0,1),True,{'issues':[]})
        self.assertFalse(result['pass']);self.assertEqual(result['issues'],[issue])
    def test_clean_opening_clears_only_inspected_claim(self):
        issue={'kind':'scene_reset','frame':1,'evidence':'Suspected opening reset'}
        ns=_audit_test_namespace({})
        result=ns['_reconcile_focused_visibility']({'pass':False,'issues':[issue]},'scene_reset',(0,1),True,{'issues':[]})
        self.assertTrue(result['pass']);self.assertEqual(result['issues'],[])

class UntimedOpeningCompatibilityTests(unittest.TestCase):
    def test_untimed_shot_uses_first_visual_scene(self):
        prompt='integrated_multimodal_description: [Shot 1] A woman walks through a park. [Shot 2] A different interior.\noverall_soundscape: Birdsong.\nnon_diegetic_music: N/A'
        question=m.opening_plan_match_instruction(prompt)
        self.assertIn('A woman walks through a park.',question)
        self.assertNotIn('A different interior.',question)
    def test_plain_scene_without_clocks_is_inspected(self):
        question=m.opening_plan_match_instruction('integrated_multimodal_description: A robot waves in a room.\noverall_soundscape: Silence.')
        self.assertIn('A robot waves in a room.',question)
    def test_missing_visual_scene_remains_unavailable(self):
        with self.assertRaisesRegex(ValueError,'no opening visual phase'):
            m.opening_plan_match_instruction('overall_soundscape: Birdsong.')

class AuditPolicyTests(unittest.TestCase):
    def test_duplicate_confirmation_receives_same_frame_locations_as_untrusted_hypotheses(self):
        text=m.duplicate_witness_instruction('<Subject 1> red bob.\nsummary: scene.',1,1,
            [{'frame':1,'evidence':'Possible matching bodies at near left and distant center.'}])
        self.assertIn('near left and distant center',text)
        self.assertIn('foreground, middle distance and background',text)
        self.assertIn('independently verify or reject',text)
        self.assertIn('Never invent an instance just to agree',text)
    def test_duplicate_hypothesis_filter_precedes_size_limit(self):
        claims=[{'frame':8,'evidence':'Other frame'} for _ in range(4)]
        claims.append({'frame':1,'evidence':'SAME_FRAME_RELEVANT_LOCATIONS'})
        text=m.duplicate_witness_instruction('cast',1,1,claims)
        self.assertIn('SAME_FRAME_RELEVANT_LOCATIONS',text)

    def test_duplicate_hypotheses_are_bounded_and_do_not_mix_other_frames(self):
        text=m.duplicate_witness_instruction('cast',1,1,[{'frame':2,'evidence':'OTHER_FRAME_ONLY'},
             {'frame':1,'evidence':'x'*500},{'frame':True,'evidence':'BOOLEAN_FRAME_ONLY'}])
        self.assertNotIn('OTHER_FRAME_ONLY',text);self.assertNotIn('BOOLEAN_FRAME_ONLY',text)
        self.assertIn('x'*280,text);self.assertNotIn('x'*281,text)

    def test_unknown_opening_crop_is_not_a_confirmed_scene_reset_or_a_pass(self):
        value={'pass':False,'frames_checked':2,'planned_space':'interior','frame0_space':'indeterminate','frame1_space':'interior','frame0_cast':'not_visible','frame1_cast':'indeterminate','issues':[{'kind':'scene_reset','frame':0,'evidence':'Desk close-up hides room and face.'}]}
        result=m.reconcile_opening_space(value)
        self.assertEqual(result['status'],'unavailable');self.assertFalse(result['pass'])
        # A directly visible wrong cast, or a known opposite setting, remains a defect.
        for changed in [{'frame1_cast':'visible_mismatch'},{'frame0_space':'exterior'}]:
            result=m.reconcile_opening_space({**value,**changed});self.assertFalse(result['pass']);self.assertNotIn('status',result)

    def test_opening_schema_requires_visible_cast_observations_without_inventing_identity(self):
        schema=m.visual_verdict_schema(2,kinds={'scene_reset'},opening=True,max_issues=2)
        self.assertIn('frame0_cast',schema['required']);self.assertIn('not_visible',schema['properties']['frame1_cast']['enum'])
        text=m.opening_plan_match_instruction('00:00-00:04\nThe girl sits on the interior sofa.')
        self.assertIn('An indeterminate space is missing evidence',text)
        self.assertIn('Cropped/unreadable faces do not establish a different identity',text)

    def test_explicit_voice_actor_bindings_do_not_conflate_subject_two_with_voice_one(self):
        text = m.audit_instruction('[Lyric L009] <Subject 2> (S1) sings.', 1, 0, [0., 1.])
        self.assertIn('Subject 2 uses S1', text)
        self.assertNotIn('Subject 1 uses S1', text)
        self.assertIn('not claim to hear audio', text)

    def test_voice_only_or_off_lyric_never_invents_a_visible_singer(self):
        for prompt in ['[Lyric L009] (S2) sings.', '[Lyric L009] The friend opens a book.']:
            text = m.audit_instruction(prompt, 1, 0, [0., 1.])
            self.assertNotIn('explicitly bind visible performer IDs', text)
            self.assertNotIn('Subject 2 uses S2', text)

    def test_explicit_duet_keeps_each_actor_voice_pair(self):
        text = m.audit_instruction('[Lyric L001] <Subject 2> (S1) sings. [Lyric L002] <Subject 1> (S2) sings.', 1, 0, [0., 1.])
        self.assertIn('Subject 2 uses S1', text)
        self.assertIn('Subject 1 uses S2', text)

    def test_reference_prompt_requires_actual_image_pixels(self):
        source=(ROOT/'minimax_h3_long_video/nodes.py').read_text(encoding='utf-8')
        wanted={'_flatten_image_tensors','_require_reference_images'}
        body=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in wanted]
        ns={'torch':torch,'re':re}
        exec(compile(ast.Module(body=body,type_ignores=[]),'reference-guard','exec'),ns)
        prompt='CHARACTER_IDENTITY_LOCK=STRICT. <Subject 1> comes from <Picture 1>.'
        with self.assertRaisesRegex(ValueError,'参照人物を固定するH3プロンプト'):
            ns['_require_reference_images'](prompt,None)
        ns['_require_reference_images'](prompt,{'ref_image_0':torch.zeros(1,8,8,3)})

    def test_disconnected_provider_cannot_supply_audit_credentials(self):
        graph={'150':{'inputs':{'prompt_plan':['138',4]}},
               '138':{'class_type':'H3StandardPrompt','inputs':{'model':'my-local','api_base':'http://127.0.0.1:1234/v1'}},
               '999':{'class_type':'NanoBananaH3Transform','inputs':{'provider':'OpenAI API'}}}
        active=m.upstream_graph(graph,'150')
        self.assertEqual(set(active),{'150','138'})
        self.assertEqual(m.standard_audit_settings(active),('my-local','http://127.0.0.1:1234/v1',360))
        self.assertEqual(m.upstream_graph(graph,'missing'),{})

    def test_pass_and_observed_failure(self):
        self.assertTrue(m.parse_verdict('{"pass":true,"frames_checked":6,"issues":[]}',6)[0])
        bad={'pass':False,'frames_checked':6,'issues':[{'kind':'scene_reset','frame':0,'evidence':'Current frame shows a new room whereas prior frames are outdoors.'}]}
        self.assertFalse(m.parse_verdict(json.dumps(bad),6)[0])

    def test_unsupported_or_unobserved_failure_is_unavailable(self):
        for value in ({'pass':False,'frames_checked':6,'issues':[]},
                      {'pass':True,'frames_checked':1,'issues':[]},
                      {'pass':False,'frames_checked':6,'issues':[{'kind':'wrong_audio','frame':0,'evidence':'heard'}]},
                      {'pass':False,'frames_checked':6,'issues':[{'kind':'scene_reset','frame':8,'evidence':'guess'}]}):
            with self.assertRaises(ValueError):m.parse_verdict(json.dumps(value),6)

    def test_t2v_needs_no_reference_image_and_does_not_claim_audio(self):
        text=m.audit_instruction('A boat sails.',0,2,(0,.25,2,2.25,4.75,5))
        self.assertIn('first 0 attached images',text)
        self.assertIn('visual-only',text)
        self.assertIn('do not claim to hear speech',text)
        self.assertIn('explicitly scheduled',text)

    def test_inspection_samples_delivered_tail_and_head_not_context_or_padding(self):
        source=(ROOT/'minimax_h3_long_video/nodes.py').read_text(encoding='utf-8')
        fns=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in ('_audit_three_mode_segment','_labeled_audit_image')]
        seen={}
        class VAE:
            def decode(self,value):return value
        class Helper:
            def convert(self,*args,**kwargs):
                seen.update(kwargs)
                return '{"pass":true,"frames_checked":6,"issues":[]}','mock'
        ns={'FPS':24,'torch':torch,'math':math,'Image':Image,'ImageDraw':ImageDraw,'json':json,
            '_streams':lambda x:(x,None),'_decode_video_frames':lambda vae, x:vae.decode(x),'_flatten_image_tensors':lambda x:[] if not x else list(x.values()),
            '_load_qwen_audit_module':lambda:types.SimpleNamespace(
                ensure_lm_studio_model_ready=lambda model, base, timeout:(True, ''),
                _ready_lm_studio_bases=lambda base:[base]),
            'comfy_nodes':types.SimpleNamespace(NODE_CLASS_MAPPINGS={'H3StandardPrompt':types.SimpleNamespace(local_lm_helper=Helper)}),
            'audit_instruction':m.audit_instruction,'parse_verdict':m.parse_verdict,
            '_atomic_json':lambda p,d:p.write_text(json.dumps(d),encoding='utf-8')}
        exec(compile(ast.Module(body=fns,type_ignores=[]),'audit-function','exec'),ns)
        _audit_test_namespace(ns)
        previous=torch.arange(40,dtype=torch.float32).view(40,1,1,1).expand(40,8,8,3)/100
        candidate=torch.arange(45,dtype=torch.float32).view(45,1,1,1).expand(45,8,8,3)/100
        old=types.SimpleNamespace(context_frames=3,output_frames=24)
        seg=types.SimpleNamespace(index=1,context_frames=5,output_frames=24)
        with tempfile.TemporaryDirectory() as directory:
            result=ns['_audit_three_mode_segment'](candidate,previous,old,seg,VAE(),'A boat.',None,
                                                   ('local','http://localhost:1234/v1',20),Path(directory)/'evidence.png')
            self.assertEqual(result,([],'pass'))
            receipt=json.loads((Path(directory)/'evidence.request-01.json').read_text())
            self.assertEqual(receipt['max_tokens'],3072)
            self.assertEqual(receipt['status'],'returned')
            self.assertEqual(json.loads(receipt['raw_response'])['frames_checked'],6)
            self.assertGreaterEqual(receipt['elapsed_seconds'],0)
            inputs=seen['input_images']
            self.assertEqual(len(inputs), 2)  # Eight ordered frames packed into two contact sheets.
            self.assertAlmostEqual(inputs[0][0,1600,200,0].item(),51/255,places=6)
            self.assertAlmostEqual(inputs[0][0,1600,600,0].item(),66/255,places=6)
            self.assertAlmostEqual(inputs[0][0,1600,1000,0].item(),12/255,places=6)
            self.assertAlmostEqual(inputs[-1][0,1600,600,0].item(),71/255,places=6)
            report=json.loads((Path(directory)/'evidence.json').read_text(encoding="utf-8"))
            self.assertEqual(report['scope'],'visual_continuity_only')


class FourPanelCameraTests(unittest.TestCase):
    def test_real_non_lm_audit_passes_delivered_boundary_and_rejects_same_camera(self):
        tree=ast.parse((ROOT/'minimax_h3_long_video/nodes.py').read_text(encoding='utf-8'))
        fns=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in
             ('_audit_segment_latent','_labeled_audit_image',
              '_audio_result_speech_end','_tail_freeze_seconds')]
        seen=[]; distinct=[False]
        def camera(provider,current,prompt,previous):
            seen.append((current,previous,prompt))
            return distinct[0],{'pass':distinct[0],'issues':[] if distinct[0] else [
                {'kind':'camera_mismatch','detail':'Same vanishing lines; only a crop.'}]}
        ns=dict(torch=torch,Image=Image,ImageDraw=ImageDraw,json=json,FPS=24,
                _streams=lambda value:(value,None),_decode_video_frames=lambda vae, value:vae.decode(value),_speaker_probe_offsets=lambda duration, speech_end=None:(0,.25,.5,.6,.7,.9),
                _audio_result_status=lambda result:'pass',
                _audit_closed_cast_video=lambda *a:([],'pass'),
                _atomic_json=lambda p,d:p.write_text(json.dumps(d),encoding='utf-8'),
                comfy_nodes=types.SimpleNamespace(NODE_CLASS_MAPPINGS={
                    'NanoBananaH3Transform':types.SimpleNamespace(audit_camera_view=camera)}))
        exec(compile(ast.Module(body=fns,type_ignores=[]),'nonlm-camera-audit','exec'),ns)
        previous=torch.arange(40,dtype=torch.float32).view(40,1,1,1).expand(40,8,8,3)/100
        candidate=torch.arange(30,dtype=torch.float32).view(30,1,1,1).expand(30,8,8,3)/100
        segment=types.SimpleNamespace(index=1,context_frames=0,output_frames=24,raw_frames=24)
        old=types.SimpleNamespace(context_frames=3,output_frames=24)
        vae=types.SimpleNamespace(decode=lambda value:value)
        with tempfile.TemporaryDirectory() as directory:
            args=(candidate,segment,vae,None,'Opening camera setup: side view.',None,
                  {'backend':'nanobanana','provider':'test'},Path(directory)/'frames.png')
            result=ns['_audit_segment_latent'](*args,audio_result=(True,'pass'),previous=previous,previous_segment=old)
            self.assertTrue(result[0]);self.assertIn('camera_mismatch',result[1])
            self.assertAlmostEqual(seen[0][0][0][0,0,0,0].item(),0)
            self.assertAlmostEqual(seen[0][1][-1][0,0,0,0].item(),.26)
            self.assertTrue((Path(directory)/'frames.camera.png').exists())
            distinct[0]=True
            self.assertEqual(ns['_audit_segment_latent'](*args,audio_result=(True,'pass'),
                previous=previous,previous_segment=old),([],'pass'))
            repaired='Opening camera setup: eye-level over-the-shoulder view.'
            self.assertEqual(ns['_audit_segment_latent'](*args,audio_result=(True,'pass'),
                previous=previous,previous_segment=old,visual_prompt=repaired),([],'pass'))
            self.assertEqual(seen[-1][2],repaired)
            self.assertEqual(seen[0][2],'Opening camera setup: side view.')

    def test_long_post_speech_frozen_tail_is_a_quality_failure(self):
        tree=ast.parse((ROOT/'minimax_h3_long_video/nodes.py').read_text(encoding='utf-8'))
        names={'_audit_segment_latent','_labeled_audit_image',
               '_audio_result_speech_end','_tail_freeze_seconds'}
        fns=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        ns=dict(torch=torch,Image=Image,ImageDraw=ImageDraw,json=json,math=math,FPS=24,
                _streams=lambda value:(value,None),_decode_video_frames=lambda vae, value:vae.decode(value),
                _speaker_probe_offsets=lambda duration, speech_end=None:(0.5,duration/2,duration-0.5),
                _audio_result_status=lambda result:'pass',
                _audit_closed_cast_video=lambda *a:([],'pass'),
                comfy_nodes=types.SimpleNamespace(NODE_CLASS_MAPPINGS={}))
        exec(compile(ast.Module(body=fns,type_ignores=[]),'frozen-tail-audit','exec'),ns)
        candidate=torch.zeros(120,8,8,3)
        segment=types.SimpleNamespace(index=5,context_frames=0,output_frames=120,raw_frames=120)
        vae=types.SimpleNamespace(decode=lambda value:value)
        audio=(True,json.dumps({'speech_end_seconds':1.0,'delivered_duration_seconds':5.0}))
        failed,status=ns['_audit_segment_latent'](
            candidate,segment,vae,None,'<d>[Japanese] 行こう。</d>',None,None,
            audio_result=audio)
        self.assertEqual(failed,[5])
        self.assertIn('post-speech frozen tail',status)


class AppearanceTests(unittest.TestCase):
    def test_wardrobe_failure_is_supported_and_must_be_observable(self):
        report={'pass':False,'frames_checked':6,'issues':[{'kind':'wardrobe_mismatch','frame':5,'evidence':'The opening has a plain coat and white blouse; the final frame has a sailor collar and large red neck ribbon.'}]}
        self.assertFalse(m.parse_verdict(json.dumps(report),6)[0])
        text=m.audit_instruction('A woman turns around.',2,2,(0,.25,2,2.25,3.7,3.9))
        self.assertIn('previously selected opening',text)
        self.assertIn('back or an occluded view',text)
        self.assertIn('major source-defined wardrobe replacement',text)
        self.assertIn('minor cosmetic',text)

    def test_opening_appearance_uses_delivered_frames_and_detaches_storage(self):
        source=(ROOT/'minimax_h3_long_video/nodes.py').read_text(encoding="utf-8")
        fn=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='_opening_audit_images')
        ns={'_streams':lambda x:(x,None),'_decode_video_frames':lambda vae, x:vae.decode(x)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'opening-appearance','exec'),ns)
        class VAE:
            def decode(self,value):return value
        video=torch.arange(40,dtype=torch.float32).view(40,1,1,1).expand(40,8,8,3).clone()
        references=ns['_opening_audit_images'](video,types.SimpleNamespace(context_frames=3,output_frames=24),VAE())
        self.assertEqual([x[0,0,0,0].item() for x in references.values()],[3,14])
        video.fill_(999)
        self.assertEqual([x[0,0,0,0].item() for x in references.values()],[3,14])


class FocusedAppearanceExecutionTests(unittest.TestCase):
    def test_one_request_checks_continuity_identity_and_clothing_across_frames(self):
        source=(ROOT/'minimax_h3_long_video/nodes.py').read_text(encoding="utf-8")
        body=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in ('_audit_three_mode_segment','_labeled_audit_image')]
        for issue_kind in (None, 'wardrobe_mismatch', 'identity_mismatch'):
            calls=[]
            class VAE:
                def decode(self,value):return value
            typed_schemas=[]
            class Helper:
                SUPPORTS_JSON_SCHEMA=True
                def convert(self,instruction,*args,**kwargs):
                    calls.append(kwargs['input_images'])
                    typed_schemas.append(self.JSON_SCHEMA)
                    evidence = ('A different collar and a large red neck ribbon.'
                                if issue_kind == 'wardrobe_mismatch'
                                else 'The eye spacing, jawline and mouth placement visibly differ from the reference face.')
                    issues=([{'kind':issue_kind,'frame':2,'evidence':evidence}]
                            if issue_kind else [])
                    appearance={'reference_identity':'reference subject','current_identity':'current subject',
                                'reference_clothing':'reference outfit','current_clothing':'current outfit',
                                'pass':not issue_kind,'frames_checked':6,'issues':issues}
                    return json.dumps({'pass':not issue_kind,'frames_checked':6,'issues':issues,
                                       'appearance_comparison':appearance}),'mock'
            ns={'FPS':24,'torch':torch,'math':math,'Image':Image,'ImageDraw':ImageDraw,'json':json,
                '_streams':lambda x:(x,None),'_decode_video_frames':lambda vae, x:vae.decode(x),'_flatten_image_tensors':lambda x:list(x.values()),
                '_load_qwen_audit_module':lambda:types.SimpleNamespace(
                    ensure_lm_studio_model_ready=lambda model, base, timeout:(True, ''),
                    _ready_lm_studio_bases=lambda base:[base]),
                'comfy_nodes':types.SimpleNamespace(NODE_CLASS_MAPPINGS={
                     'H3StandardPrompt':types.SimpleNamespace(local_visual_helper=Helper,local_lm_helper=Helper)}),
                'audit_instruction':m.audit_instruction,'appearance_instruction':m.appearance_instruction,'parse_verdict':m.parse_verdict,
                '_atomic_json':lambda p,d:p.write_text(json.dumps(d), encoding="utf-8")}
            exec(compile(ast.Module(body=body,type_ignores=[]),'focused-appearance-execute','exec'),ns)
            _audit_test_namespace(ns)
            video=torch.zeros(30,8,8,3)
            seg=types.SimpleNamespace(index=2,context_frames=5,output_frames=24)
            with tempfile.TemporaryDirectory() as directory:
                result=ns['_audit_three_mode_segment'](video,None,None,seg,VAE(),'A woman turns.',{'opening':video[:1]},
                         ('local','http://localhost:1234/v1',20),Path(directory)/'test.png')
                report=json.loads((Path(directory)/'test.json').read_text(encoding="utf-8"))
            self.assertEqual(len(calls),1)
            self.assertEqual(typed_schemas[0]["properties"]["frames_checked"]["enum"],[6])
            self.assertIn("appearance_comparison",typed_schemas[0]["required"])
            self.assertEqual(len(calls[0]),2)  # One reference and one labeled contact sheet.
            self.assertEqual(len(report['current_offsets_seconds']), 6)
            self.assertEqual(bool(result[0]),bool(issue_kind))
            if issue_kind:self.assertEqual(report['detail']['issues'][0]['frame'],2)
            else:self.assertEqual(result,([],'pass'))

    def test_reference_audit_rejects_missing_combined_appearance_result(self):
        source=(ROOT/'minimax_h3_long_video/nodes.py').read_text(encoding="utf-8")
        body=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in ('_audit_three_mode_segment','_labeled_audit_image')]
        class VAE:
            def decode(self,value):return value
        class Helper:
            def convert(self,*args,**kwargs):
                return '{"pass":true,"frames_checked":6,"issues":[]}','mock'
        ns={'FPS':24,'torch':torch,'math':math,'Image':Image,'ImageDraw':ImageDraw,'json':json,
            '_streams':lambda x:(x,None),'_decode_video_frames':lambda vae, x:vae.decode(x),'_flatten_image_tensors':lambda x:list(x.values()),
                '_load_qwen_audit_module':lambda:types.SimpleNamespace(
                    ensure_lm_studio_model_ready=lambda model, base, timeout:(True, ''),
                    _ready_lm_studio_bases=lambda base:[base]),
            'comfy_nodes':types.SimpleNamespace(NODE_CLASS_MAPPINGS={'H3StandardPrompt':types.SimpleNamespace(local_lm_helper=Helper)}),
            'audit_instruction':m.audit_instruction,'appearance_instruction':m.appearance_instruction,'parse_verdict':m.parse_verdict,
            '_atomic_json':lambda p,d:p.write_text(json.dumps(d), encoding="utf-8")}
        exec(compile(ast.Module(body=body,type_ignores=[]),'focused-appearance-required','exec'),ns)
        _audit_test_namespace(ns)
        video=torch.zeros(30,8,8,3)
        seg=types.SimpleNamespace(index=2,context_frames=5,output_frames=24)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'omitted appearance comparison'):
                ns['_audit_three_mode_segment'](video,None,None,seg,VAE(),'A woman turns.',{'opening':video[:1]},
                    ('local','http://localhost:1234/v1',20),Path(directory)/'test.png')


class TypedVisualContractTests(unittest.TestCase):
    def test_focused_opening_schema_rejects_the_actual_dictionary_issues_fault(self):
        from jsonschema import Draft202012Validator,ValidationError
        schema=m.visual_verdict_schema(2,kinds={'scene_reset'},opening=True,max_issues=2)
        validator=Draft202012Validator(schema)
        observed={'pass':False,'frames_checked':2,'planned_space':'exterior','frame0_space':'interior',
                  'frame1_space':'exterior','issues':{'frame_0':'An interior classroom abruptly becomes a park.'}}
        with self.assertRaises(ValidationError):validator.validate(observed)
        observed['issues']=[{'kind':'scene_reset','frame':0,'evidence':'An interior classroom abruptly becomes a park.'}]
        observed.update(frame0_cast='visible_match',frame1_cast='visible_match')
        validator.validate(observed)
        self.assertFalse(m.reconcile_opening_space(observed)['pass'])
    def test_typed_observation_arrays_cannot_masquerade_as_summaries(self):
        ns={'json':json};_audit_test_namespace(ns)
        appearance={key:[] for key in ('reference_identity','current_identity','reference_clothing','current_clothing')}
        appearance.update({'pass':True,'frames_checked':6,'issues':[]})
        with self.assertRaisesRegex(ValueError,'typed observations'):
            ns['_require_combined_appearance']({'pass':True,'frames_checked':6,'issues':[],
                                                'appearance_comparison':appearance},6,True)
    def test_comparison_allows_normal_performance_and_minor_drawing_variation(self):
        text=m.audit_instruction('A deliberate performance.',1,0,(0,1,2))
        self.assertIn('allowing normal expression, perspective, pose and minor drawing variation',text)
        self.assertIn('require a clear identity replacement or major stable-trait change',text)

class DuplicateWitnessTests(unittest.TestCase):
    def instance(self,subject,box,kind='physical_character'):
        return {'subject_id':subject,'kind':kind,'box':box,'evidence':'A separately visible character extent.'}
    def inspect(self,instances,visibility='clear',frame=8):
        return m.parse_duplicate_witness(json.dumps({'frame':frame,'visibility':visibility,'instances':instances}),8,['Subject 1','Subject 2'])
    def test_ambiguous_extra_annotation_does_not_erase_an_independent_duplicate_pair(self):
        result=self.inspect([self.instance('Subject 1',[300,100,650,950]),
            self.instance('Subject 1',[350,250,550,600]),
            self.instance('Subject 2',[0,100,250,950]),self.instance('Subject 2',[750,100,1000,950])])
        self.assertEqual(result['status'],'confirmed_duplicate')
        self.assertEqual(len(result['issues']),1)
        self.assertIn('Subject 2',result['issues'][0]['evidence'])
        self.assertTrue(result['ambiguous_additional_annotations'])
    def test_occlusion_without_any_independent_duplicate_pair_remains_unknown(self):
        result=self.inspect([self.instance('Subject 1',[300,100,650,950]),
            self.instance('Subject 1',[350,250,550,600]),self.instance('Subject 2',[750,100,1000,950])])
        self.assertEqual(result['status'],'unknown');self.assertEqual(result['issues'],[])
    def test_two_distinct_subjects_are_a_normal_frame(self):
        result=self.inspect([self.instance('Subject 1',[100,50,430,950]),self.instance('Subject 2',[550,50,900,950])])
        self.assertEqual(result['status'],'confirmed_no_duplicate')
    def test_two_separate_instances_of_one_subject_confirm_a_duplicate(self):
        result=self.inspect([self.instance('Subject 2',[20,200,300,950]),self.instance('Subject 1',[350,50,650,950]),self.instance('Subject 2',[700,200,980,950])])
        self.assertEqual(result['status'],'confirmed_duplicate');self.assertEqual(result['issues'][0]['frame'],8)
    def test_a_face_and_containing_body_box_do_not_prove_two_people(self):
        result=self.inspect([self.instance('Subject 1',[100,100,400,950]),self.instance('Subject 1',[150,100,350,300])])
        self.assertEqual(result['status'],'unknown')
    def test_a_reference_portrait_is_not_an_extra_physical_body(self):
        result=self.inspect([self.instance('Subject 1',[100,100,400,950]),self.instance('Subject 1',[500,100,900,900],'depiction')])
        self.assertEqual(result['status'],'confirmed_no_duplicate')
    def test_uncertain_identity_or_visibility_cannot_clear_a_claim(self):
        self.assertEqual(self.inspect([self.instance('unmatched',[100,100,400,950])])['status'],'unknown')
        self.assertEqual(self.inspect([],visibility='uncertain')['status'],'unknown')
    def test_wrong_frame_and_reversed_or_nonfinite_boxes_are_rejected(self):
        for box in ([400,100,200,950],[0,0,float('nan'),1000]):
            with self.assertRaises(ValueError):self.inspect([self.instance('Subject 1',box)])
        with self.assertRaises(ValueError):self.inspect([],frame=1)


class DuplicateWitnessExecutionTests(unittest.TestCase):
    def run_case(self, *, duplicate=False, initial_claim=False, extra_claim=False, malformed_first=False,
                 duplicate_outage=False, composition=False, composition_outage=False, composition_stasis=False, opening_failure=False, expires_during_decode=False, interrupt_after_first=False, broad_clean=False, motion_case=None, duplicate_frames=(), unknown_frames=(), transient_unknown_frames=()):
        source=(ROOT/'minimax_h3_long_video/nodes.py').read_text()
        functions=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)
                   and n.name in {'_audit_three_mode_segment','_labeled_audit_image'}]
        calls=[];broad_count=0;loaded=True;readiness_checks=[];interrupted=False
        self.execution_calls=calls
        self.witness_frames=[]
        observed_witness_frames=self.witness_frames
        def check_interrupt():
            if interrupted:raise AuditInterrupted("GUI requested stop")
        def ensure_ready(*args):
            nonlocal loaded
            readiness_checks.append(loaded);loaded=True;return True,''
        def decode(vae,video):
            nonlocal loaded
            if expires_during_decode:loaded=False
            return video
        issue={'kind':'duplicate_body','frame':8,'evidence':'Montage appears to show an extra matching body.'}
        class Helper:
            SUPPORTS_JSON_SCHEMA=True
            def convert(self,question,*args,**kwargs):
                nonlocal broad_count,interrupted
                if expires_during_decode and not loaded:raise RuntimeError('HTTP 400: No models loaded after idle TTL')
                fields=self.JSON_SCHEMA['properties']
                if 'instances' in fields:
                    phase='witness'
                    self.subjects=fields['instances']['items']['properties']['subject_id']['enum']
                    current=fields['frame']['enum'][0]
                    observed_witness_frames.append(current)
                    instances=[{'subject_id':'Subject 1','kind':'physical_character','box':[100,100,430,950],'evidence':'Left character.'},
                               {'subject_id':'Subject 2','kind':'physical_character','box':[550,100,900,950],'evidence':'Right character.'}]
                    if duplicate or current in duplicate_frames:
                        instances[0]['subject_id']='Subject 2'
                    unknown=current in unknown_frames or (current in transient_unknown_frames and observed_witness_frames.count(current)==1)
                    response={'frame':current,'visibility':'uncertain' if unknown else 'clear','instances':instances}
                elif 'planned_space' in fields:
                    phase='opening';response={'pass':not opening_failure,'frames_checked':2,
                        'issues':([{'kind':'scene_reset','frame':0,'evidence':'Opening is an unplanned interior before the park.'}] if opening_failure else []),
                        'planned_space':'exterior','frame0_space':('interior' if opening_failure else 'exterior'),'frame1_space':'exterior'}
                elif 'planned_events' in fields:
                    phase='motion'
                    if motion_case=='outage':
                        calls.append((phase,len(kwargs['input_images'])))
                        raise RuntimeError('motion inspector unavailable')
                    sequence=([(1,'ongoing')]*3+[(2,'complete')]*4+[(1,'start'),(1,'ongoing')]
                              if motion_case=='replay' else [(1,'ongoing')]*4+[(2,'complete')]*5)
                    if motion_case=='unknown':sequence[0]=(0,'uncertain')
                    response={'planned_events':[{'id':1,'action':'walk'},{'id':2,'action':'look up'}],
                        'observations':[{'frame':i,'event_id':event,'phase':state,'evidence':'Observed action state'}
                                        for i,(event,state) in enumerate(sequence)]}
                elif 'observations' in fields:
                    phase='composition'
                    if composition_outage:
                        calls.append((phase,len(kwargs['input_images'])))
                        raise RuntimeError('composition inspector unavailable')
                    response={'observations':[{'frame':i,'composition_group':'A','activity_group':'A',
                        'distinct_event':(i==4 and not composition_stasis),'evidence':'Same view with a concrete observed activity.'} for i in range(9)]}
                elif 'appearance_comparison' in fields:
                    phase='combined';issues=([dict(issue)] if initial_claim else [])
                    if extra_claim:issues.append(dict(issue,frame=7))
                    if motion_case:issues.append({'kind':'action_replay','frame':7,'evidence':'Same pose appears again.'})
                    appearance={key:'A concrete unchanged visible trait.' for key in
                                ('reference_identity','current_identity','reference_clothing','current_clothing')}
                    appearance.update({'pass':True,'frames_checked':9,'issues':[]})
                    response={'pass':not issues,'frames_checked':9,'issues':issues,'appearance_comparison':appearance}
                else:
                    phase='duplicate';broad_count+=1
                    calls.append((phase,len(kwargs['input_images'])))
                    if duplicate_outage:raise RuntimeError('duplicate inspector unavailable')
                    if malformed_first and broad_count==1:return '{}','mock malformed response'
                    return json.dumps({'pass':broad_clean,'frames_checked':9,'issues':[] if broad_clean else [issue]}),'mock montage claim'
                calls.append((phase,len(kwargs['input_images'])))
                if interrupt_after_first:interrupted=True
                return json.dumps(response),'mock recorded witness'
        ns={'FPS':24,'torch':torch,'math':math,'Image':Image,'ImageDraw':ImageDraw,'json':json,
            'comfy':types.SimpleNamespace(model_management=types.SimpleNamespace(throw_exception_if_processing_interrupted=check_interrupt,InterruptProcessingException=AuditInterrupted)),
            '_streams':lambda x:(x,None),'_decode_video_frames':decode,
            '_flatten_image_tensors':lambda x:list(x.values()),
            '_load_qwen_audit_module':lambda:types.SimpleNamespace(
                ensure_lm_studio_model_ready=ensure_ready,_ready_lm_studio_bases=lambda base:[base]),
            'comfy_nodes':types.SimpleNamespace(NODE_CLASS_MAPPINGS={
                'H3StandardPrompt':types.SimpleNamespace(local_visual_helper=Helper,local_lm_helper=Helper)}),
            '_atomic_json':lambda p,d:p.write_text(json.dumps(d))}
        exec(compile(ast.Module(body=functions,type_ignores=[]),'duplicate-witness-execute','exec'),ns)
        _audit_test_namespace(ns)
        video=torch.full((312,8,8,3),.5);segment=types.SimpleNamespace(index=0,context_frames=0,output_frames=312)
        prompt='subject_definitions: <Subject 1> red bob. <Subject 2> blonde bob.\nsummary: Two friends.\ndetailed_description:\n00:00.000-00:13.000\nThey stand in a park.'
        if composition:
            prompt+=' [Lyric L001] Singing performance.\n[00:00-00:06] [Shot 1] A readable medium performance view.\n[00:06-00:13] [Shot 2] A wider composition reveals the park.'
        with tempfile.TemporaryDirectory() as directory:
            result=ns['_audit_three_mode_segment'](video,None,None,segment,None,prompt,{'opening':video[:1]},
                ('fixture','http://localhost:1234/v1',20),Path(directory)/'evidence.png')
            witness_paths=list(Path(directory).glob('*.duplicate_witness.json'))
            witness=json.loads(witness_paths[0].read_text()) if witness_paths else None
            self.last_report=json.loads((Path(directory)/'evidence.json').read_text())
            self.readiness_checks=readiness_checks
        return result,calls,witness
    def test_false_montage_claim_is_cleared_by_one_normal_single_frame(self):
        result,calls,witness=self.run_case()
        self.assertEqual(result,([],'pass'))
        self.assertEqual([name for name,_ in calls],['combined','duplicate','witness','opening'])
        self.assertEqual(next(n for phase,n in calls if phase=='witness'),2)
        self.assertEqual(witness['status'],'confirmed_no_duplicate')

    def test_held_motion_witness_clears_only_the_false_replay(self):
        result,calls,_=self.run_case(motion_case='clear')
        self.assertEqual(result,([],'pass'))
        self.assertIn(('motion',9),calls)

    def test_replay_witness_keeps_earlier_completed_event_and_actual_frame(self):
        result,_,_=self.run_case(motion_case='replay')
        self.assertTrue(result[1].startswith('fail:'))
        issues=self.last_report['detail']['issues']
        self.assertEqual([(x['kind'],x['frame']) for x in issues],[('action_replay',7)])

    def test_uncertain_or_failed_motion_witness_cannot_pass_or_trigger_false_failure(self):
        for case in ('unknown','outage'):
            with self.subTest(case=case):
                result,_,_=self.run_case(motion_case=case)
                self.assertTrue(result[1].startswith('unavailable:'))
                self.assertIsNone(self.last_report['passed'])

    def test_motion_uncertainty_preserves_independent_opening_failure(self):
        result,_,_=self.run_case(motion_case='unknown',opening_failure=True)
        self.assertTrue(result[1].startswith('fail:'))
        self.assertTrue(any(x['kind']=='scene_reset' for x in self.last_report['detail']['issues']))
    def test_confirmed_single_frame_duplicate_remains_a_failure(self):
        result,calls,witness=self.run_case(duplicate=True)
        self.assertTrue(result[0]);self.assertTrue(result[1].startswith('fail:'))
        self.assertEqual(witness['status'],'confirmed_duplicate')
        self.assertEqual(sum(name in ('duplicate','witness') for name,_ in calls),2)
    def test_confirmed_duplicate_overrides_clean_montage_recheck(self):
        result,calls,witness=self.run_case(duplicate=True,initial_claim=True,broad_clean=True)
        self.assertEqual(witness['status'],'confirmed_duplicate')
        self.assertTrue(result[0]);self.assertTrue(result[1].startswith('fail:'))
        self.assertEqual(sum(name in ('duplicate','witness') for name,_ in calls),2)
    def test_false_initial_claim_with_clean_single_frame_remains_normal(self):
        result,_,witness=self.run_case(initial_claim=True,broad_clean=True)
        self.assertEqual(witness['status'],'confirmed_no_duplicate');self.assertEqual(result,([],'pass'))

    def test_format_retry_still_confirms_the_returned_frame_claim(self):
        result,calls,witness=self.run_case(malformed_first=True)
        self.assertEqual(result,([],'pass'))
        self.assertEqual(sum(name=='duplicate' for name,_ in calls),2)
        self.assertEqual(self.witness_frames,[8])
        self.assertEqual(witness['status'],'confirmed_no_duplicate')
    def test_each_unique_frame_claim_is_independently_confirmed_before_clear(self):
        result,calls,witness=self.run_case(initial_claim=True,extra_claim=True)
        self.assertEqual(result,([],'pass'))
        self.assertEqual(self.witness_frames,[7,8])
        self.assertEqual(witness['status'],'confirmed_no_duplicate')

    def test_duplicate_in_a_later_claimed_frame_is_detected(self):
        result,_,_=self.run_case(initial_claim=True,extra_claim=True,duplicate_frames=(8,))
        self.assertEqual(self.witness_frames,[7,8])
        self.assertTrue(result[1].startswith('fail:'))
        self.assertEqual([x['frame'] for x in self.last_report['detail']['issues']],[8])

    def test_uncertain_claim_is_not_erased_by_other_clear_frames(self):
        result,_,_=self.run_case(extra_claim=True,unknown_frames=(7,))
        self.assertEqual(self.witness_frames,[7,7,8])
        self.assertTrue(result[1].startswith('unavailable:'))
        self.assertIsNone(self.last_report['passed'])

    def test_uncertain_visibility_gets_one_independent_clarification(self):
        result,_,witness=self.run_case(transient_unknown_frames=(8,))
        self.assertEqual(result,([],'pass'))
        self.assertEqual(self.witness_frames,[8,8])
        self.assertEqual(witness['status'],'confirmed_no_duplicate')
        self.assertEqual(witness['initial_observation']['status'],'unknown')

    def test_uncertain_first_claim_does_not_hide_later_confirmed_duplicate(self):
        result,_,_=self.run_case(extra_claim=True,unknown_frames=(7,),duplicate_frames=(8,))
        self.assertTrue(result[1].startswith('fail:'))
        self.assertEqual([x['frame'] for x in self.last_report['detail']['issues']],[8])

    def test_unconfirmed_montage_claim_during_outage_stays_unknown(self):
        result,_,_=self.run_case(initial_claim=True,duplicate_outage=True)
        self.assertTrue(result[1].startswith('unavailable:'))
        self.assertIsNone(self.last_report['passed'])


    def test_duplicate_outage_does_not_hide_observed_composition_failure(self):
        result,calls,_=self.run_case(duplicate_outage=True,composition=True,composition_stasis=True)
        self.assertTrue(result[1].startswith('fail:'))
        self.assertIn('composition_stasis',result[1])
        self.assertEqual([name for name,_ in calls],['combined','duplicate','composition','opening'])
        self.assertIn('duplicate_cast',self.last_report['detail']['unavailable_checks'])
        self.assertFalse(self.last_report['passed'])
    def test_duplicate_outage_with_other_checks_pass_remains_unknown(self):
        result,calls,_=self.run_case(duplicate_outage=True,composition=True)
        self.assertTrue(result[1].startswith('unavailable:'))
        self.assertTrue(self.last_report['focused_composition']['pass'])
        self.assertTrue(self.last_report['focused_opening_plan']['pass'])
        self.assertIsNone(self.last_report['passed'])
        self.assertEqual(sum(name=='duplicate' for name,_ in calls),1)
    def test_composition_outage_does_not_hide_observed_opening_failure(self):
        result,calls,_=self.run_case(composition=True,composition_outage=True,opening_failure=True)
        self.assertTrue(result[1].startswith('fail:'))
        self.assertIn('scene_reset',result[1])
        self.assertEqual(calls[-1][0],'opening')
        self.assertIn('composition',self.last_report['detail']['unavailable_checks'])


    def test_model_expiring_during_video_decode_is_reloaded_before_each_actual_request(self):
        result,calls,_=self.run_case(expires_during_decode=True)
        self.assertEqual(result,([],'pass'))
        self.assertTrue(self.readiness_checks[0])  # Initial endpoint guard precedes decoding.
        self.assertFalse(self.readiness_checks[1])  # Decode expires the model before inference.
        self.assertTrue(all(self.readiness_checks[2:]))
        self.assertEqual(len(self.readiness_checks),len(calls)+1)
        self.assertTrue(self.last_report['passed'])


    def test_gui_interrupt_after_inference_prevents_all_followup_inspection_requests(self):
        with self.assertRaises(AuditInterrupted):self.run_case(interrupt_after_first=True)
        self.assertEqual([name for name,_ in self.execution_calls],['combined'])


class CompositionObservationTests(unittest.TestCase):
 def rows(self,groups,events=()):
  return {"observations":[{"frame":i,"composition_group":group,"activity_group":"A","distinct_event":i in events,"evidence":"Observed view "+group} for i,group in enumerate(groups)]}
 def evaluate(self,groups,events=(),offsets=None):
  return m.parse_composition_observations(json.dumps(self.rows(groups,events)),offsets or list(range(len(groups))))
 def test_real_response_contradiction_cannot_turn_changes_into_stasis(self):
  self.assertTrue(self.evaluate(["A"]+["B"]*5+["C"]*3)["pass"])
 def test_repeated_majority_over_six_seconds_fails(self):
  result=self.evaluate(["A"]*8+["B"])
  self.assertFalse(result["pass"])
  self.assertEqual([r["frame"] for r in result["issues"]],[0,7])
 def test_clear_event_progression_and_short_takes_allowed(self):
  self.assertTrue(self.evaluate(["A"]*9,events=(4,))["pass"])
  self.assertTrue(self.evaluate(["A"]*5)["pass"])
 def test_unknown_or_missing_coverage_is_not_pass(self):
  with self.assertRaises(ValueError):self.evaluate(["A","unknown","B"])
  rows=self.rows(["A"]*9);rows["observations"][3]["frame"]=2
  with self.assertRaises(ValueError):m.parse_composition_observations(json.dumps(rows),list(range(9)))

if __name__=='__main__':unittest.main()
