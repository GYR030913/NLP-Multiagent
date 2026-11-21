import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from collab.cli import parser
from collab.config import Config
from collab.engine import Engine
from collab.migration import migrate
from collab.runner import resume_options
from collab.skills import SkillRegistry
from collab.store import Store
from collab.tools import Tools
from collab.budgets import available
from collab.browser import validate_plan
from collab.engine import normalize_action


class Client:
    usage = {}
    def __init__(self, actions): self.actions = iter(actions); self.calls = 0
    def generate(self, messages): self.calls += 1; return next(self.actions)


class MilestoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name, 'group')
        self.config = Config('hkgai', 'https://example.org/v1', 'test', '', max_run_actions=15)
        roles = {r: {'title': r, 'prompt': 'Original ' + r} for r in ('lead', 'dev', 'qa', 'release')}
        Engine.create(self.store, {'roles': roles, 'tasks': [{'text': 'one'}, {'text': 'two'}]})
        self.client = Client([])
        self.tools = Tools(self.store, self.config)
        self.engine = Engine(self.store, self.config, self.client, self.tools)
        self.root = self.engine.submit('Build a delivery')
        self.task = self.engine.state['tasks'][self.root]
        self.engine.apply(self.task, {'type': 'plan', 'criteria': ['Works'], 'text': 'Plan'})

    def files(self, count=10):
        root = self.store.workspace('dev')
        for i in range(count): (root / f'f{i}.py').write_text(f'x={i}\n', encoding='utf-8')
        return [f'f{i}.py' for i in range(count)]

    def publish(self, paths=None, **kwargs):
        return self.tools.execute(self.engine.state, 'dev', 'artifact.publish_bundle', {'paths': paths or self.files(), **kwargs})

    def test_resume_modes_reject_conflicts_without_exiting_chat(self):
        self.assertEqual(resume_options(''), (30, False))
        self.assertEqual(resume_options('3'), (3, False))
        self.assertEqual(resume_options('--continuous'), (30, True))
        for text in ('3 --continuous', '0', '-1', '--unknown'):
            with self.subTest(text=text), self.assertRaises(ValueError): resume_options(text)
        with redirect_stdout(io.StringIO()), patch('sys.stderr', new=io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(['resume', 'group', '--steps', '3', '--continuous'])

    def test_continuous_crosses_batch_limit_but_never_resets_total(self):
        self.config.max_steps = 2
        self.client.actions = iter({'type': 'message', 'to': 'group', 'text': str(i)} for i in range(30))
        self.engine.run(2, continuous=True)
        self.assertEqual(self.client.calls, 15)
        self.assertEqual(self.engine.state['execution']['stop_reason'], 'run_budget_exhausted')
        other = Engine(self.store, self.config, self.client, self.tools)
        other.run(continuous=True)
        self.assertEqual(self.client.calls, 15)

    def test_existing_and_new_human_requests_stop_before_other_ready_work(self):
        self.engine.new_task('dev', 'child', ['Works'], 'r1', self.root)
        self.client.actions = iter([{'type': 'ask', 'question': 'Actual user requirement?'}])
        self.engine.run(continuous=True)
        self.assertEqual(self.client.calls, 1)
        self.assertEqual(self.engine.state['execution']['stop_reason'], 'human_request')
        self.engine.run(continuous=True)
        self.assertEqual(self.client.calls, 1)

    def test_identical_no_progress_is_bounded(self):
        self.client.actions = iter([{'type': 'message', 'to': 'group', 'text': 'same'}] * 10)
        self.engine.run(continuous=True)
        self.assertEqual(self.client.calls, 5)
        self.assertEqual(self.engine.state['execution']['stop_reason'], 'no_progress')

    def test_alternating_repeated_reads_cannot_evade_no_progress(self):
        (self.store.workspace('lead')/'notes.txt').write_text('unchanged',encoding='utf-8')
        actions=[{'type':'tool','name':'files.read','args':{'path':'notes.txt'}},
                 {'type':'tool','name':'files.list','args':{}}]*10
        self.client.actions=iter(actions)
        self.engine.run(continuous=True)
        self.assertEqual(self.engine.state['execution']['stop_reason'],'no_progress')
        self.assertEqual(self.client.calls,14)
        self.assertTrue(any(e['kind']=='output_checkpoint_triggered' for e in self.engine.state['events']))
        self.assertEqual(self.engine.state['requests'],{})

    def test_single_tool_envelopes_cannot_change_task_criteria_or_permissions(self):
        expected={'type':'tool','name':'files.list','args':{}}
        self.assertEqual(normalize_action({'tool':'files.list','args':{},'criteria':['weakened']}),expected)
        self.assertEqual(normalize_action({'tool':{**expected,'criteria':['weakened']}}),expected)
        before=list(self.task['criteria'])
        self.client.actions=iter([{'tool':{**expected,'criteria':['weakened']}}])
        self.engine.run(1)
        self.assertEqual(self.task['criteria'],before)
        self.assertTrue(any(e['kind']=='agent_response_normalized' for e in self.engine.state['events']))
        with self.assertRaises(ValueError): normalize_action({'tool':{'type':'finish','name':'files.list'}})

    def test_invalid_response_is_not_replayed_as_a_valid_action_example(self):
        self.client.actions=iter([{'unexpected':'object'},{'type':'message','to':'group','text':'Correct JSON action'}])
        self.engine.run(2)
        self.assertTrue(any(e['kind']=='invalid_agent_response' for e in self.engine.state['events']))
        actions=self.engine.context(self.task)['recent_own_actions']
        self.assertTrue(all(a['action'].get('type') in {'plan','message','tool'} for a in actions))

    def test_normalized_external_tool_still_requires_approval(self):
        self.client.actions=iter([{'tool':'git.push','args':{'url':'https://github.com/example/repo.git'}}])
        self.engine.run(1)
        request=next(iter(self.engine.state['requests'].values()))
        self.assertEqual(request['kind'],'approval')
        self.assertEqual(request['action']['name'],'git.push')
        self.assertEqual(self.engine.state['execution']['stop_reason'],'human_request')

    def test_exact_flat_tool_parameters_are_normalized_without_guessing(self):
        self.assertEqual(normalize_action({'type':'tool','name':'files.write','path':'x.txt','content':'text'}),
                         {'type':'tool','name':'files.write','args':{'path':'x.txt','content':'text'}})
        self.assertEqual(normalize_action({'tool':'code.run','argv':['python','check.py']}),
                         {'type':'tool','name':'code.run','args':{'argv':['python','check.py']}})
        with self.assertRaises(ValueError):
            normalize_action({'type':'tool','name':'files.read','path':'one.txt','args':{'path':'two.txt'}})

    def test_failed_tool_request_is_not_a_successful_action_example(self):
        self.client.actions=iter([{'type':'tool','name':'files.read','args':{'path':'missing.txt'}},
                                  {'type':'message','to':'group','text':'Use the correct input path'}])
        self.engine.run(2)
        context=self.engine.context(self.task)
        self.assertFalse(any(a['action'].get('name')=='files.read' for a in context['recent_own_actions']))
        self.assertEqual(context['recent_action_failures'][-1]['name'],'files.read')

    def test_answer_resets_error_episode_without_refunding_budget(self):
        self.task['errors']=3
        self.task['recent_result_signatures']=['old']*5
        self.engine.state['runs']['r1']['actions_used']=7
        request=self.engine.request(self.task,'clarification',question='Resolve the actual problem')
        self.engine.answer(request,text='The issue is resolved')
        self.assertEqual(self.task['errors'],0)
        self.assertEqual(self.task['recent_result_signatures'],[])
        self.assertEqual(self.engine.state['runs']['r1']['actions_used'],7)

    def test_review_target_is_structured_and_distinct_from_reviewer_work(self):
        paths=self.files(1)
        bundle=self.publish(paths)
        tid=self.engine.new_task('dev','Build a small delivery',['Works'],'r1',self.root)
        target=self.engine.state['tasks'][tid]
        self.engine.apply(target,{'type':'finish','summary':'Submitted implementation','lesson':'Check current source before approval.',
                                 'artifacts':bundle['artifact_ids'],'evidence':[],'reviewer':'qa'})
        review=self.engine.state['tasks'][target['review_task']]
        context=self.engine.context(review)
        self.assertEqual(context['review_target']['task_id'],tid)
        self.assertEqual(context['review_target']['bundle_ids'],[bundle['id']])
        self.assertNotIn('Submission:',context['current_task']['description'])
        self.assertIn('Submission:',review['description'])
        self.assertEqual(context['response_contract']['completion_action'],'review')

    def test_child_contract_is_atomic_and_cannot_broaden_permissions(self):
        self.task['allowed_tools'] = ['files.read']
        before = len(self.engine.state['tasks'])
        with self.assertRaises(ValueError):
            self.engine.new_task('dev', 'child', ['Works'], 'r1', self.root, allowed_tools=['*'])
        self.assertEqual(len(self.engine.state['tasks']), before)
        child = self.engine.new_task('dev', 'child', ['Works'], 'r1', self.root,
             allowed_tools=['files.read'], deliverable_key='delivery', budget={'max_actions': 5})
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.engine.new_task('dev', 'child', ['Works'], 'r1', self.root,
                 allowed_tools=['files.read'], deliverable_key='delivery')
        with self.assertRaises(ValueError):
            self.engine.apply(self.engine.state['tasks'][child], {'type': 'tool', 'name': 'files.delete', 'args': {'path': 'x.txt'}})
        self.assertFalse(self.engine.state['requests'])

    def test_reserved_child_budget_cannot_be_spent_by_parent(self):
        child = self.engine.new_task('dev', 'child', ['Works'], 'r1', self.root, budget={'max_actions': 14})
        self.assertEqual(available(self.engine.state, 'r1', self.root), 1)
        self.assertEqual(available(self.engine.state, 'r1', child), 14)
        self.engine.state['runs']['r1']['actions_used'] = 1
        self.task['actions_used'] = 1
        self.assertEqual(available(self.engine.state, 'r1', self.root), 0)
        with self.assertRaises(ValueError):
            self.engine.new_task('qa', 'another', ['Works'], 'r1', self.root, budget={'max_actions': 2})

    def test_explicit_migration_is_backed_up_and_dry_run_does_not_write(self):
        state = self.store.load(); state['version'] = 1
        state['tasks'][self.root].pop('planned')
        self.store.save(state)
        raw = self.store.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'migrate'): Engine(self.store, self.config, self.client, self.tools)
        report = migrate(self.store, True)
        self.assertTrue(report['changed'])
        self.assertEqual(self.store.state_path.read_bytes(), raw)
        report = migrate(self.store, False)
        self.assertEqual(Path(report['backup']).read_bytes(), raw)
        loaded = self.store.load()
        self.assertEqual(list(loaded['tasks']), [self.root])
        self.assertFalse(loaded['tasks'][self.root]['planned'])
        self.assertEqual(loaded['runs']['r1']['actions_used'], state['runs']['r1']['actions_used'])

    def test_unknown_schema_is_rejected(self):
        self.store.state_path.write_text('{"version":99}', encoding='utf-8')
        with self.assertRaises(ValueError): self.store.load()

    def test_private_files_and_memories_are_not_in_public_context(self):
        private = self.engine.submit('Confidential note', owner='lead', private=True)
        t = self.engine.state['tasks'][private]
        self.engine.apply(t, {'type': 'plan', 'criteria': ['Private reply'], 'text': 'private'})
        self.engine.execute_tool(t, {'name': 'files.write', 'args': {'path': 'secret.txt', 'content': 'PRIVATE_MARKER'}})
        self.store.remember('lead', 'PRIVATE_MARKER', private, 'human_accepted')
        self.assertFalse((self.store.workspace('lead') / 'secret.txt').exists())
        public = json.dumps(self.engine.context(self.task))
        self.assertNotIn('PRIVATE_MARKER', public)
        self.assertNotIn('Confidential note', public)
        for name in ('artifact.publish_bundle', 'artifact.import_bundle', 'browser.verify'):
            with self.assertRaises(ValueError): self.engine.apply(t, {'type': 'tool', 'name': name, 'args': {}})

    def test_skills_are_versioned_role_filtered_and_pinned(self):
        listing = SkillRegistry().list('qa')
        self.assertIn('software.verify', [s['id'] for s in listing])
        with self.assertRaises(ValueError): SkillRegistry().get('software.build', 'qa')
        self.engine.execute_tool(self.task, {'name': 'skill.use', 'args': {'id': 'requirements.clarify'}})
        self.assertEqual(self.task['skill_refs'][0]['version'], '1.0.0')
        self.assertEqual(len(self.engine.context(self.task)['active_skills']), 1)

    def test_ten_file_bundle_round_trip_is_atomic_and_idempotent(self):
        b = self.publish(idempotency_key='delivery')
        again = self.publish([f'f{i}.py' for i in range(10)], idempotency_key='delivery')
        self.assertEqual(b['id'], again['id'])
        self.assertEqual(len(self.engine.state['artifacts']), 10)
        result = self.tools.execute(self.engine.state, 'qa', 'artifact.import_bundle', {'bundle_id': b['id'], 'path': 'clean'})
        self.assertEqual(result['files'], 10)
        for i in range(10): self.assertEqual((self.store.workspace('qa')/'clean'/f'f{i}.py').read_text(), f'x={i}\n')
        (self.store.workspace('qa')/'clean'/'f0.py').write_text('changed')
        with self.assertRaises(ValueError):
            self.tools.execute(self.engine.state, 'qa', 'artifact.import_bundle', {'bundle_id': b['id'], 'path': 'clean'})

    def test_bad_bundle_publishes_no_partial_references(self):
        paths = self.files()
        for bad in ('missing.py', '../escape.py', 'delivery.zip'):
            with self.subTest(bad=bad), self.assertRaises(ValueError): self.publish(paths + [bad])
            self.assertFalse(self.engine.state['artifacts'])
            self.assertFalse(self.engine.state['bundles'])
        with self.assertRaises(ValueError): self.publish(paths, required_paths=['README.md'])
        self.assertFalse(self.engine.state['artifacts'])

    def test_corrupt_bundle_is_not_imported(self):
        b = self.publish()
        artifact = self.engine.state['artifacts'][b['artifact_ids'][0]]
        (self.store.path/'artifacts'/artifact['sha256']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.tools.execute(self.engine.state, 'qa', 'artifact.import_bundle', {'bundle_id': b['id'], 'path': 'clean'})
        self.assertFalse((self.store.workspace('qa')/'clean').exists())

    def test_registry_validates_before_tool_or_approval(self):
        for name, args in [('files.delete', {'path': 'x.txt', 'extra': 1}), ('code.run', {'argv': 'shell'}), ('unknown', {})]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.engine.apply(self.task, {'type': 'tool', 'name': name, 'args': args})
        self.assertFalse(self.engine.state['requests'])

    def test_browser_does_not_accept_external_navigation_or_javascript(self):
        for check in [{'action': 'goto', 'path': '//evil.example/'}, {'action': 'evaluate', 'script': 'anything'}]:
            with self.assertRaises(ValueError): validate_plan({'argv': ['python', 'app.py'], 'checks': [check]})

    def test_preview_requires_approval_bound_to_the_exact_launch(self):
        action = {'type': 'tool', 'name': 'preview.start', 'args': {'bundle_id': 'b1', 'argv': ['python', 'app.py']}}
        self.engine.apply(self.task, action)
        request = self.engine.state['requests']['q1']
        self.assertEqual(request['kind'], 'approval')
        self.assertEqual(request['action']['args'], action['args'])

    def test_approval_cannot_be_reused_after_target_changes(self):
        self.engine.apply(self.task, {'type': 'tool', 'name': 'files.delete', 'args': {'path': 'x.txt'}})
        self.engine.state['requests']['q1']['action']['args']['path'] = 'another.txt'
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.engine.answer('q1', approved=True)
        self.assertEqual(self.engine.state['requests']['q1']['status'], 'pending')

    def test_new_evidence_invalidates_draft_without_faking_human_verdict(self):
        self.task['submission']={'artifacts':[]}
        self.engine.request(self.task,'acceptance',summary='draft')
        self.engine.invalidate_delivery('r1','new concurrency failure',{'receipt':'test.json'})
        self.assertEqual(self.engine.state['requests']['q1']['status'],'invalidated')
        self.assertNotIn('approved',self.engine.state['requests']['q1'])
        self.assertEqual(self.task['status'],'running')
        self.assertEqual(self.task['revision'],2)
        with self.assertRaises(ValueError):self.engine.answer('q1',approved=True)

    def test_review_can_use_budget_reserved_for_its_target(self):
        child=self.engine.new_task('dev','child',['Works'],'r1',self.root,budget={'max_actions':10})
        self.engine.state['tasks'][child]['status']='reviewing'
        review=self.engine.new_task('qa','review',['Works'],'r1',kind='review',target=child)
        self.assertEqual(available(self.engine.state,'r1',review),10)

    def test_corrupt_graph_is_not_migrated(self):
        state=self.store.load();state['version']=1;state['tasks'][self.root]['depends_on']=[self.root]
        self.store.save(state);before=self.store.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError,'cycle'):migrate(self.store,False)
        self.assertEqual(self.store.state_path.read_bytes(),before)

    def test_source_change_invalidates_previous_verification(self):
        self.engine.execute_tool(self.task,{'name':'files.write','args':{'path':'source.py','content':'x=1\n'}})
        self.engine.execute_tool(self.task,{'name':'ui.check','args':{'path':'source.py'}})
        evidence=len(self.engine.state['events'])
        self.engine.execute_tool(self.task,{'name':'files.write','args':{'path':'source.py','content':'x=2\n'}})
        self.engine.execute_tool(self.task,{'name':'artifact.publish','args':{'path':'source.py'}})
        artifact=self.engine.state['events'][-1]['result']['id']
        with self.assertRaisesRegex(ValueError,'changed after verification'):
            self.engine.apply(self.task,{'type':'finish','summary':'done','lesson':'verify','artifacts':[artifact],'evidence':[evidence],'reviewer':'qa'})

    def test_call_budget_is_durable_before_provider_request(self):
        class InterruptedClient:
            usage={}
            def generate(client,messages):
                self.assertEqual(self.store.load()['runs']['r1']['actions_used'],1)
                raise KeyboardInterrupt()
        self.engine.client=InterruptedClient()
        with self.assertRaises(KeyboardInterrupt):self.engine.run(continuous=True)
        recovered=Engine(self.store,self.config,self.client,self.tools)
        self.assertEqual(recovered.state['runs']['r1']['actions_used'],1)
        self.assertEqual(recovered.state['execution']['stop_reason'],'user_interrupted')

    def test_clarification_cannot_be_approved_in_place_of_answer(self):
        self.engine.request(self.task,'clarification',question='A real requirement?')
        with self.assertRaises(ValueError):self.engine.answer('q1',approved=True)
        self.assertEqual(self.engine.state['requests']['q1']['status'],'pending')

    def test_preview_snapshot_handoff_is_consistent_and_does_not_mount_live_data(self):
        import sqlite3,hashlib
        from collab.preview import snapshot_data
        with self.assertRaises(ValueError):snapshot_data(self.store,self.engine.state,'default','dev')
        self.engine.state['scenario']['tasks'][0]['text']=self.task['description']
        self.engine.state['runs']['r1']['status']='completed'
        data=self.store.path/'preview-data'/'default';data.mkdir(parents=True)
        source=data/'bookings.db'
        with sqlite3.connect(source) as db:
            db.execute('CREATE TABLE bookings(id INTEGER,status TEXT)')
            db.execute("INSERT INTO bookings VALUES(1,'cancelled')")
        db.close()
        before=source.read_bytes()
        result=snapshot_data(self.store,self.engine.state,'default','dev')
        snapshot=self.store.workspace('dev')/result['path']
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(),result['sha256'])
        with sqlite3.connect(snapshot) as db:self.assertEqual(db.execute('SELECT status FROM bookings').fetchone()[0],'cancelled')
        db.close()
        self.assertEqual(source.read_bytes(),before)
        # Remove the Windows read-only attribute before TemporaryDirectory cleanup.
        snapshot.chmod(0o666)


if __name__ == '__main__': unittest.main()
