import tempfile
import unittest
import urllib.error
from unittest.mock import Mock
from unittest.mock import patch
from pathlib import Path
from collab.recovery import checkpoint,public_source_call
from collab.context import tool_patterns
from collab.store import Store
from collab.config import Config
from collab.engine import Engine
from collab.tools import Tools


class RecoveryTests(unittest.TestCase):
    def test_failure_context_is_bounded_and_rejects_forged_external_frames(self):
        from collab.tools import failure_context
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'workspace';root.mkdir()
            (root/'test_case.py').write_text('def test_case():\n    data = {"actual": 1}\n    assert data["expected"] == 1\n',encoding='utf-8')
            (Path(directory)/'secret.py').write_text('outside_canary',encoding='utf-8')
            output='File "/workspace/test_case.py", line 3\nFile "/workspace/../secret.py", line 1\nFile "/workspace/.env", line 1\n'
            rows=failure_context(root,output)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['line'],3)
            self.assertIn('data["expected"]',rows[0]['content'])
            self.assertNotIn('outside_canary',str(rows))
            self.assertEqual(failure_context(root,'File "/workspace/test_case.py", line 999999'),[])
            self.assertEqual(failure_context(root,'File "/workspace/test_case.py", line '+('9'*5000)),[])
            (root/'test_case.py').write_text(('x'*2000+'\n')*10,encoding='utf-8')
            rows=failure_context(root,'File "/workspace/test_case.py", line 3')
            self.assertLessEqual(len(rows[0]['content']),1000)

    def test_context_overflow_stops_locally_without_model_or_generic_request(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory,'group');config=Config('hkgai','https://example.org','test','',input_budget_tokens=100)
            Engine.create(store,{'roles':{'lead':{'title':'Lead','prompt':'Lead'}},'tasks':[]})
            client=Mock();client.attempts_last_call=0
            engine=Engine(store,config,client,Tools(store,config))
            engine.submit('Preserve this complete requirement and never invent approval.')
            engine.run(steps=10)
            client.generate.assert_not_called()
            self.assertEqual(engine.state['execution']['stop_reason'],'context_budget_exceeded')
            self.assertEqual(engine.state['requests'],{})
            self.assertEqual(engine.state['runs']['r1']['actions_used'],1)
            self.assertTrue(engine.state['queue'])

    def test_sandbox_cwd_is_bounded_and_passed_without_shell(self):
        from collab.sandbox import Sandbox
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'delivery').mkdir()
            with patch('collab.sandbox.subprocess.run',return_value=Mock(returncode=0)) as run:
                Sandbox('test-image').run(root,['python','-V'],cwd='delivery')
                command=run.call_args.args[0]
                self.assertEqual(command[command.index('--workdir')+1],'/workspace/delivery')
                self.assertFalse(run.call_args.kwargs['shell'])
            for cwd in ('../escape','/tmp','delivery/../delivery','delivery/','missing','.hidden'):
                with patch('collab.sandbox.subprocess.run') as run:
                    with self.assertRaises(ValueError):Sandbox('test-image').run(root,['python','-V'],cwd=cwd)
                    run.assert_not_called()

    def test_code_tool_reports_explicit_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            tools=Tools(Store(directory,'group'),Config('hkgai','https://example.org','test',''))
            with patch.object(tools.sandbox,'run',return_value={'exit_code':0,'output':'Ran 1 test'}) as run:
                result=tools.execute({},'dev','code.run',{'argv':['python','-m','unittest'],'cwd':'delivery'})
                self.assertEqual(run.call_args.kwargs['cwd'],'delivery')
                self.assertEqual(result['cwd'],'delivery')

    def setUp(self):
        self.task={'id':'t1','run':'r1','kind':'work','planned':True,'private':False,'allowed_tools':['*']}
        self.state={'events':[]}

    def add(self,name,result=None):
        self.state['events'].append({'task':'t1','kind':'tool_result','name':name,'result':result or {'ok':True}})

    def test_read_budget_requires_output_and_then_reopens_inspection(self):
        for _ in range(12):self.add('files.read')
        self.task['output_checkpoint']=checkpoint(self.state,self.task)
        self.assertTrue(self.task['output_checkpoint']['required'])
        self.assertNotIn('files.read',tool_patterns(self.task))
        self.assertIn('files.write',tool_patterns(self.task))
        self.add('files.write',{'path':'draft.md'})
        self.task['output_checkpoint']=checkpoint(self.state,self.task)
        self.assertFalse(self.task['output_checkpoint']['required'])
        self.assertEqual(tool_patterns(self.task),['*'])

    def test_failed_output_does_not_reset_and_review_is_exempt(self):
        for _ in range(12):self.add('web.fetch',{'ok':False})
        self.add('files.write',{'ok':False})
        self.assertTrue(checkpoint(self.state,self.task)['required'])
        self.task['kind']='review'
        self.assertFalse(checkpoint(self.state,self.task)['required'])

    def test_checkpoint_does_not_broaden_tool_permissions(self):
        self.task.update(allowed_tools=['files.read'],output_checkpoint={'required':True})
        self.assertEqual(tool_patterns(self.task),[])

    def test_noop_restore_and_republication_do_not_buy_inspection(self):
        self.add('files.write',{'path':'a.py','sha256':'v1','changed':True})
        self.add('artifact.publish_bundle',{'id':'b1','sha256':'bundle-v1'})
        for _ in range(12):self.add('files.read')
        for result in ({'path':'a.py','sha256':'v1','changed':False},
                       {'path':'a.py','sha256':'v1','changed':True}):
            self.add('files.write',result)
            self.assertTrue(checkpoint(self.state,self.task)['required'])
        self.add('artifact.publish_bundle',{'id':'b2','sha256':'bundle-v1'})
        self.assertTrue(checkpoint(self.state,self.task)['required'])
        self.add('files.write',{'path':'a.py','sha256':'v2','changed':True})
        self.assertFalse(checkpoint(self.state,self.task)['required'])

    def test_real_file_tools_report_noops_and_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            tools=Tools(Store(directory,'group'),Config('hkgai','https://example.org','test',''))
            def call(name,**args):return tools.execute({},'dev',name,args)
            first=call('files.write',path='a.py',content='x=1\n')
            self.assertTrue(first['changed'])
            for name,args in [('files.write',{'content':'x=1\n'}),
                              ('files.append',{'content':''}),
                              ('files.replace',{'old':'x=1','new':'x=1'})]:
                result=call(name,path='a.py',**args)
                self.assertFalse(result['changed'])
                self.assertEqual(result['sha256'],first['sha256'])
            self.assertTrue(call('files.replace',path='a.py',old='x=1',new='x=2')['changed'])

    def test_early_checkpoint_replays_and_releases_only_after_new_output(self):
        self.add('files.write',{'path':'a.py','sha256':'v1','changed':True})
        self.state['events'][-1]['seq']=1
        self.state['events'].append({'seq':2,'task':'t1','kind':'output_checkpoint_triggered'})
        self.assertTrue(checkpoint(self.state,self.task)['required'])
        self.add('files.write',{'path':'a.py','sha256':'v1','changed':False})
        self.state['events'][-1]['seq']=3
        self.assertTrue(checkpoint(self.state,self.task)['required'])
        self.add('files.write',{'path':'a.py','sha256':'v2','changed':True})
        self.state['events'][-1]['seq']=4
        self.assertFalse(checkpoint(self.state,self.task)['required'])

    def test_conflicted_edit_grants_one_targeted_read_without_broadening_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory,'group');config=Config('hkgai','https://example.org','test','')
            Engine.create(store,{'roles':{'lead':{'title':'Lead','prompt':'Lead'}},'tasks':[]})
            tools=Tools(store,config);engine=Engine(store,config,None,tools)
            tid=engine.submit('Fix an application');task=engine.state['tasks'][tid]
            engine.apply(task,{'type':'plan','text':'Fix','criteria':['Working application']})
            tools.execute({},'lead','files.write',{'path':'app.py','content':'x=1\n'})
            engine.event('output_checkpoint_triggered',task=tid)
            action={'type':'tool','name':'files.replace','args':{'path':'app.py','old':'absent','new':'x=2'}}
            engine.event('agent_action',task=tid,action=action)
            engine.event('action_error',task=tid,error='Expected 1 exact matches, found 0; read the relevant file before retrying')
            task['output_checkpoint']=checkpoint(engine.state,task)
            self.assertIn('files.read',tool_patterns(task))
            for args in ({'path':'other.py','limit':1500},{'path':'app.py','limit':1501},{'path':'app.py'}):
                with self.assertRaises(ValueError):tools.execute_for_task(engine.state,task,'files.read',args)
            result=tools.execute_for_task(engine.state,task,'files.read',{'path':'app.py','limit':1500})
            self.assertIn('x=1',result['content'])
            with self.assertRaises(ValueError):tools.execute_for_task(engine.state,task,'files.read',{'path':'app.py','limit':1500})
            engine.event('tool_result',task=tid,name='files.read',result=result)
            task['output_checkpoint']=checkpoint(engine.state,task)
            self.assertIsNone(task['output_checkpoint']['repair_read'])
            self.assertTrue(task['output_checkpoint']['required'])
            task['output_checkpoint']['repair_read']={'path':'app.py'}
            task['allowed_tools']=['files.replace']
            self.assertNotIn('files.read',tool_patterns(task))

    def test_403_is_a_failure_result_with_one_attempt_across_tools(self):
        call=Mock(side_effect=urllib.error.HTTPError('https://example.org/a',403,'blocked',{},None))
        args={'url':'https://example.org/a'}
        first=public_source_call(self.state,self.task,'web.fetch',args,call)
        second=public_source_call(self.state,self.task,'web.fetch_document',args,call)
        self.assertFalse(first['ok']);self.assertTrue(second['cached_failure'])
        self.assertEqual(call.call_count,1)

    def test_transient_failure_has_two_attempts_and_private_scope_isolated(self):
        call=Mock(side_effect=urllib.error.URLError('reset'));args={'url':'https://example.org/a'}
        for _ in range(4):public_source_call(self.state,self.task,'web.fetch',args,call)
        self.assertEqual(call.call_count,2)
        private={**self.task,'id':'t2','private':True}
        public_source_call(self.state,private,'web.fetch',args,call)
        self.assertEqual(call.call_count,3)

    def test_native_transport_failures_use_the_same_bounded_source_policy(self):
        for exc in (ValueError('Public HTTPS status 403'),RuntimeError('Verified public HTTPS download failed (curl exit 35)')):
            with self.subTest(error=str(exc)):
                state={'events':[]};call=Mock(side_effect=exc)
                result=public_source_call(state,self.task,'web.fetch',{'url':'https://example.org'},call)
                self.assertFalse(result['ok'])
                self.assertEqual(result['error_category'],'source_unavailable')

    def test_security_errors_are_not_converted_to_network_failures(self):
        call=Mock(side_effect=ValueError('Private/local network access is prohibited'))
        with self.assertRaises(ValueError):
            public_source_call(self.state,self.task,'web.fetch',{'url':'https://127.0.0.1'},call)
        self.assertEqual(self.state.get('source_failures'),{})

    def test_three_blocked_fetches_do_not_create_generic_human_request(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory,'group');config=Config('hkgai','https://example.org','test','')
            Engine.create(store,{'roles':{'lead':{'title':'Lead','prompt':'Lead'}},'tasks':[]})
            tools=Tools(store,config)
            action={'type':'tool','name':'web.fetch','args':{'url':'https://example.org/blocked'}}
            client=Mock();client.generate.return_value=action;client.usage={};client.attempts_last_call=0
            engine=Engine(store,config,client,tools)
            tid=engine.submit('Research an actual public source')
            engine.apply(engine.state['tasks'][tid],{'type':'plan','text':'Research','criteria':['Verify sources']})
            tools.execute=Mock(side_effect=urllib.error.HTTPError(action['args']['url'],403,'blocked',{},None))
            engine.run(steps=3)
            self.assertEqual(tools.execute.call_count,1)
            self.assertEqual(engine.state['requests'],{})
            failures=[ev for ev in engine.state['events'] if ev['kind']=='tool_result']
            self.assertEqual(len(failures),3)
            self.assertTrue(all(ev['result']['ok'] is False for ev in failures))
            self.assertEqual(engine.state['runs']['r1']['actions_used'],3)

    def test_checkpoint_feedback_is_bounded_without_execution_or_generic_human_request(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory,'group');config=Config('hkgai','https://example.org','test','')
            Engine.create(store,{'roles':{'lead':{'title':'Lead','prompt':'Lead'}},'tasks':[]})
            tools=Tools(store,config);tools.execute=Mock()
            client=Mock();client.generate.side_effect=[
                {'type':'tool','name':name,'args':args} for name,args in [
                    ('files.read',{'path':'app.py'}),('files.list',{}),
                    ('code.run',{'argv':['python','test.py']}),('files.list',{}),
                    ('files.read',{'path':'app.py'})]]
            client.usage={};client.attempts_last_call=0
            engine=Engine(store,config,client,tools)
            tid=engine.submit('Build a working application')
            task=engine.state['tasks'][tid]
            engine.apply(task,{'type':'plan','text':'Implement and test','criteria':['Deliver runnable code']})
            for _ in range(12):engine.event('tool_result',task=tid,name='files.read',result='old observation')
            context=engine.context(task)
            self.assertEqual(context['response_contract']['completion_action'],'tool')
            engine.run(steps=10)
            self.assertEqual(engine.state['execution']['stop_reason'],'no_progress')
            self.assertEqual(engine.state['runs']['r1']['actions_used'],5)
            self.assertEqual(engine.state['requests'],{})
            tools.execute.assert_not_called()
            task['allowed_tools']=['files.write']
            with self.assertRaises(ValueError):
                engine.apply(task,{'type':'tool','name':'files.read','args':{'path':'app.py'}})

    def test_new_output_starts_new_no_progress_window_but_noop_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory,'group');config=Config('hkgai','https://example.org','test','')
            Engine.create(store,{'roles':{'lead':{'title':'Lead','prompt':'Lead'}},'tasks':[]})
            client=Mock();client.usage={};client.attempts_last_call=0
            engine=Engine(store,config,client,Tools(store,config))
            tid=engine.submit('Implement and verify application')
            engine.apply(engine.state['tasks'][tid],{'type':'plan','text':'Implement','criteria':['Working application']})
            write={'type':'tool','name':'files.write','args':{'path':'app.py','content':'x=1\n'}}
            read={'type':'tool','name':'files.read','args':{'path':'app.py'}}
            change={'type':'tool','name':'files.replace','args':{'path':'app.py','old':'x=1','new':'x=2'}}
            # Five identical post-edit reads must still pause when an
            # intervening write makes no content change.
            noop={'type':'tool','name':'files.write','args':{'path':'app.py','content':'x=2\n'}}
            client.generate.side_effect=[write]+[read]*3+[change]+[read]*3+[noop]+[read]*2
            engine.run(steps=8)
            self.assertEqual(engine.state['execution']['stop_reason'],'batch_complete')
            engine.run(steps=3)
            self.assertEqual(engine.state['execution']['stop_reason'],'batch_complete')
            self.assertTrue(checkpoint(engine.state,engine.state['tasks'][tid])['required'])
            client.generate.side_effect=[read]*5
            engine.run(steps=5)
            self.assertEqual(engine.state['execution']['stop_reason'],'no_progress')
            self.assertEqual(engine.state['runs']['r1']['actions_used'],16)
