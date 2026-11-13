"""Clean public bundle regressions: no private node registry or launcher."""
import importlib.util,json,pathlib,sys,tempfile,types,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).parent
PACKAGE=ROOT/'custom_nodes/comfyui-h3-standard-prompt'
def load():
    folder=types.ModuleType('folder_paths');folder.base_path=str(ROOT)
    sys.modules['folder_paths']=folder
    nodes=types.ModuleType('nodes');nodes.NODE_CLASS_MAPPINGS={};sys.modules['nodes']=nodes
    spec=importlib.util.spec_from_file_location('h3_clean_public',PACKAGE/'__init__.py',submodule_search_locations=[str(PACKAGE)])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module,sys.modules[spec.name+'.lm_client']
class PortableTests(unittest.TestCase):
    def setUp(self):
        originals = {name: sys.modules.get(name) for name in ('folder_paths', 'nodes')}
        def restore():
            for name, original in originals.items():
                if original is None: sys.modules.pop(name, None)
                else: sys.modules[name] = original
        self.addCleanup(restore)
        self.prompt,self.lm=load()
    def test_schema_and_helpers_exist_without_private_qwen_nodes(self):
        schema=self.prompt.H3StandardPrompt.INPUT_TYPES()
        self.assertIn('japanese_instruction',schema['required'])
        self.assertIs(self.prompt.H3StandardPrompt.local_lm_helper,self.lm.LocalLMHelper)
        self.assertFalse(schema['optional']['auto_gpu_for_prompt'][1]['default'])
    def test_screen_text_ban_does_not_request_speech(self):
        for label in ('画面文字','画面内文字','画面の文字','画面内の文字'):
            self.assertTrue(self.prompt.requests_global_silence('台詞・BGM・'+label+'なし。'))
    def test_manual_model_readiness_normal_and_wrong_model(self):
        data={'models':[{'key':'selected','loaded_instances':[{'id':'chosen'}]}]}
        with patch.object(self.lm,'_ready_lm_studio_bases',return_value=['http://configured:1234/v1']),patch.object(self.lm,'_post_model_list',return_value=data):
            self.assertTrue(self.lm.ensure_lm_studio_model_ready('chosen','http://configured:1234/v1',10)[0])
            self.assertFalse(self.lm.ensure_lm_studio_model_ready('missing','http://configured:1234/v1',10)[0])
    def test_absent_endpoint_is_manual_failure(self):
        with patch.object(self.lm,'_ready_lm_studio_bases',return_value=[]):
            self.assertFalse(self.lm.ensure_lm_studio_model_ready('chosen','http://configured:1234/v1',10)[0])
        self.assertFalse(self.lm._auto_start_lm_studio(1)[0])
    def test_conversion_uses_bundled_client_without_registry(self):
        helper=self.lm.LocalLMHelper();helper.JSON_SCHEMA={'type':'object'}
        result={'output':[{'type':'message','content':'{"ok":true}'}],'schema_evidence':{'ref':'test','sha256':'test'}}
        with patch.object(self.lm,'_ready_lm_studio_bases',return_value=['http://configured:1234/v1']),patch.object(self.lm,'_post_json_schema',return_value=result) as request:
            text,status=helper.convert('Inspect','chosen','http://configured:1234/v1',0,100,10,False)
        self.assertEqual(json.loads(text),{'ok':True});self.assertEqual(request.call_args.args[1]['model'],'chosen')
    def test_typed_transport_rejects_malformed_incomplete_and_wrong_schema(self):
        schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
        payload={'input':'inspect','model':'chosen','temperature':0,'system_prompt':'Inspect','max_output_tokens':100}
        cases=[('{"ok":true}', 'stop', True, True), ('{"ok":"yes"}', 'stop', True, False),
               ('{"ok":true,"ok":false}', 'stop', True, False), ('{"ok":true}', 'length', True, False),
               ('{"ok":true}', 'stop', False, False)]
        with tempfile.TemporaryDirectory() as directory:
            sys.modules['folder_paths'].get_output_directory=lambda:directory
            for text,finish,done,valid in cases:
                with self.subTest(text=text,finish=finish,done=done):
                    lines=[('data: '+json.dumps({'choices':[{'delta':{'content':text},'finish_reason':finish}]})+'\n').encode()]
                    if done: lines.append(b'data: [DONE]\n')
                    with patch.object(self.lm,'stream_lines',return_value=iter(lines)):
                        if valid:
                            data=self.lm._post_json_schema('http://configured/api/v1/chat',payload,10,schema)
                            self.assertEqual(json.loads(data['output'][0]['content']),{'ok':True})
                        else:
                            with self.assertRaises(ValueError):self.lm._post_json_schema('http://configured/api/v1/chat',payload,10,schema)
    def test_failed_typed_request_records_its_own_receipt(self):
        helper=self.lm.LocalLMHelper();helper.JSON_SCHEMA={'type':'object'}
        helper.JSON_SCHEMA_EVIDENCE=[{'ref':'previous-success.json','sha256':'old'}]
        failure=ValueError('invalid schema');failure.schema_evidence={'ref':'current-error.json','sha256':'new'}
        with patch.object(self.lm,'_ready_lm_studio_bases',return_value=['http://configured:1234/v1']),patch.object(self.lm,'_post_json_schema',side_effect=failure):
            with self.assertRaises(RuntimeError):helper.convert('Inspect','chosen','http://configured:1234/v1',0,100,10,False)
        self.assertEqual(helper.JSON_SCHEMA_EVIDENCE[-1],failure.schema_evidence)
if __name__=='__main__':unittest.main()
