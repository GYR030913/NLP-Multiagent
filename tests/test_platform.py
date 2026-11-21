import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from collab.config import Config
from collab.engine import Engine
from collab.llm import ModelError
from collab.store import Store, ident
from collab.tools import Tools, safe_path, public_url, TextParser
from collab.report import metrics, export
from collab.scenarios import discover, load_scenario


ROLES = {name: {'title': name, 'prompt': 'You are ' + name} for name in ['lead', 'dev', 'qa', 'release']}


class FakeClient:
    usage = {'total_tokens': 10}

    def __init__(self, actions=()):
        self.actions = iter(actions)

    def generate(self, messages):
        return next(self.actions)


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name, 'test')
        self.config = Config('hkgai', 'https://example.org', 'test', '')
        Engine.create(self.store, {'roles': ROLES, 'tasks': [{'text': 'first'}, {'text': 'second'}]})
        self.tools = Tools(self.store, self.config)
        self.engine = Engine(self.store, self.config, FakeClient(), self.tools)
        self.root = self.engine.submit('Build a tested artifact')
        self.act(self.root, type='plan',
                 criteria=['Deliver a tested artifact', 'Explain how to use it'],
                 text='Initial test plan')

    def tearDown(self):
        self.temp.cleanup()

    def act(self, task_id, **action):
        self.engine.apply(self.engine.state['tasks'][task_id], action)
        self.store.save(self.engine.state)

    def tool(self, task_id, name, **args):
        self.act(task_id, type='tool', name=name, args=args)
        return self.engine.state['events'][-1]

    def finish(self, task_id, reviewer='qa', artifacts=None):
        self.act(task_id, type='finish', summary='Work delivered', lesson='Verify the artifact independently before delivery.',
                 artifacts=artifacts or [], evidence=[], reviewer=reviewer)
        return self.engine.state['tasks'][task_id].get('review_task')

    def approve_review(self, review_id):
        event = self.tool(review_id, 'files.write', path='review.txt', content='Independent check')
        criteria = self.engine.state['tasks'][review_id]['criteria']
        self.act(review_id, type='review', approved=True, feedback='Verified',
                 checks=[{'criterion': c, 'passed': True, 'evidence': [event['seq']]} for c in criteria])

    def final_artifact(self):
        self.tool(self.root, 'files.write', path='report.md', content='# Actual test fixture')
        return self.tool(self.root, 'artifact.publish', path='report.md')['result']['id']

    def test_end_to_end_delegation_review_acceptance_and_second_task(self):
        self.act(self.root, type='delegate', to='dev', description='Implement artifact', criteria=['Artifact exists'])
        child = self.engine.state['tasks'][self.root]['children'][0]
        self.act(self.root, type='wait')
        self.tool(child, 'files.write', path='implementation.py', content='print(42)\n')
        artifact = self.tool(child, 'artifact.publish', path='implementation.py')['result']['id']
        reviewer = self.finish(child, artifacts=[artifact])
        self.tool(reviewer, 'artifact.import', id=artifact, path='implementation.py')
        self.tool(reviewer, 'files.read', path='implementation.py')
        self.approve_review(reviewer)
        self.assertEqual(self.engine.state['tasks'][self.root]['status'], 'running')
        review = self.finish(self.root, reviewer='release', artifacts=[self.final_artifact(), artifact])
        self.approve_review(review)
        self.assertEqual(metrics(self.engine.state)['task_completion_rate'], 0.)
        request = next(q for q in self.engine.state['requests'].values() if q['kind'] == 'acceptance')
        self.engine.answer(request['id'], approved=True)
        self.assertEqual(metrics(self.engine.state)['task_completion_rate'], 1.)
        self.assertEqual(metrics(self.engine.state)['runs'][0]['subtasks_completed'], 1)
        second = self.engine.submit('Extend the previous implementation')
        context = self.engine.context(self.engine.state['tasks'][second])
        self.assertIn(artifact, context['artifacts'])
        self.assertFalse(context['memory'])  # A completed outcome does not validate its lesson.
        from collab.memory import Memory
        self.assertTrue(Memory(self.store, self.engine.state).records())
        self.assertEqual(len(self.engine.state['runs']), 2)

    def test_group_survives_reload(self):
        self.tool(self.root, 'files.write', path='x.txt', content='persistent')
        other = Engine(self.store, self.config, FakeClient(), self.tools)
        self.assertEqual(other.state['events'], self.engine.state['events'])

    def test_next_turn_retains_the_code_action_that_produced_a_file(self):
        action = {'type': 'tool', 'name': 'files.write', 'args': {'path': 'verify.py', 'content': "print('specific verifier')\n"}}
        self.engine.event('agent_action', agent='lead', task=self.root, action=action)
        self.engine.apply(self.engine.state['tasks'][self.root], action)
        context = self.engine.context(self.engine.state['tasks'][self.root])
        self.assertEqual(context['recent_own_actions'][-1]['action'], action)
        self.assertEqual(context['current_task_evidence_index'][-1]['path'], 'verify.py')

    def test_review_context_excludes_plan_and_exposes_current_evidence(self):
        review = self.finish(self.root, artifacts=[self.final_artifact()])
        self.tool(review, 'files.write', path='review.txt', content='Checked')
        context = self.engine.context(self.engine.state['tasks'][review])
        self.assertNotIn('plan', context['allowed_actions'])
        self.assertIn('review', context['allowed_actions'])
        self.assertNotIn('finish', context['allowed_actions'])
        self.assertEqual(len(context['current_task_evidence_index']), 1)

    def test_clarification_persists_and_resumes(self):
        self.act(self.root, type='ask', question='Which date range?')
        other = Engine(self.store, self.config, FakeClient(), self.tools)
        self.assertEqual(other.state['tasks'][self.root]['status'], 'waiting_for_human')
        other.answer('q1', 'November 2026')
        self.assertIn(self.root, other.state['queue'])
        self.assertEqual(other.state['tasks'][self.root]['status'], 'running')
        with self.assertRaises(ValueError):
            other.answer('q1', 'again')

    def test_delete_needs_approval(self):
        self.tool(self.root, 'files.write', path='x.txt', content='keep')
        self.tool(self.root, 'files.delete', path='x.txt')
        path = self.store.workspace('lead') / 'x.txt'
        self.assertTrue(path.exists())
        with self.assertRaises(ValueError):
            self.engine.answer('q1', 'yes')
        self.engine.answer('q1', approved=False)
        self.assertTrue(path.exists())
        self.tool(self.root, 'files.delete', path='x.txt')
        self.engine.answer('q2', approved=True)
        self.assertFalse(path.exists())

    def test_approved_tool_failure_is_logged(self):
        self.tool(self.root, 'files.delete', path='missing.txt')
        self.engine.answer('q1', approved=True)
        self.assertEqual(self.engine.state['events'][-1]['kind'], 'action_error')

    def test_rejected_review_reopens_task_and_records_lesson(self):
        review = self.finish(self.root, artifacts=[self.final_artifact()])
        self.act(review, type='review', approved=False, feedback='Missing cancellation test', checks=[])
        self.assertEqual(self.engine.state['tasks'][self.root]['status'], 'running')
        self.assertEqual(self.store.recall('lead', 'cancellation')[0]['outcome'], 'review_rejected')

    def test_finish_cannot_skip_children(self):
        self.act(self.root, type='delegate', to='dev', description='work', criteria=['test'])
        with self.assertRaises(ValueError):
            self.finish(self.root)

    def test_self_review_and_self_delegation_forbidden(self):
        with self.assertRaises(ValueError):
            self.finish(self.root, reviewer='lead')
        with self.assertRaises(ValueError):
            self.act(self.root, type='delegate', to='lead', description='work', criteria=['test'])

    def test_reviews_require_real_independent_evidence(self):
        review = self.finish(self.root, artifacts=[self.final_artifact()])
        for refs in [[], [9999], [1]]:
            with self.assertRaises(ValueError):
                self.act(review, type='review', approved=True, feedback='ok',
                         checks=[{'criterion': self.engine.state['tasks'][self.root]['criteria'][0], 'passed': True, 'evidence': refs}])
        self.assertEqual(self.engine.state['tasks'][self.root]['status'], 'reviewing')

    def test_missing_criteria_cannot_pass_review(self):
        review = self.finish(self.root, artifacts=[self.final_artifact()])
        event = self.tool(review, 'files.write', path='check.txt', content='checked')
        with self.assertRaises(ValueError):
            self.act(review, type='review', approved=True, feedback='ok',
                     checks=[{'criterion': 'Deliver a tested artifact', 'passed': True,
                              'evidence': [event['seq']]}])

    def test_user_can_reject_final_delivery(self):
        review = self.finish(self.root, artifacts=[self.final_artifact()])
        self.approve_review(review)
        self.engine.answer('q1', 'Fix missing use instructions', approved=False)
        self.assertEqual(self.engine.state['tasks'][self.root]['status'], 'running')
        self.assertEqual(metrics(self.engine.state)['task_completion_rate'], 0.)

    def test_invalid_review_does_not_close_review_task(self):
        review = self.finish(self.root, artifacts=[self.final_artifact()])
        with self.assertRaises(ValueError):
            self.act(review, type='review', approved=False, checks=[])
        self.assertEqual(self.engine.state['tasks'][review]['status'], 'running')
        self.assertEqual(self.engine.state['tasks'][self.root]['status'], 'reviewing')

    def test_invalid_finish_cannot_poison_memory(self):
        with self.assertRaises(ValueError):
            self.act(self.root, type='finish', summary='text', lesson={'invalid': 'object'},
                     artifacts=[self.final_artifact()], evidence=[], reviewer='qa')
        self.assertEqual(self.engine.state['tasks'][self.root]['status'], 'running')

    def test_private_messages_not_in_other_agent_context(self):
        self.engine.event('human_message', sender='human', to='dev', text='PRIVATE-NOTE')
        lead_context = json.dumps(self.engine.context(self.engine.state['tasks'][self.root]))
        self.assertNotIn('PRIVATE-NOTE', lead_context)
        private = self.engine.submit('PRIVATE-TASK', owner='dev', private=True)
        lead_context = json.dumps(self.engine.context(self.engine.state['tasks'][self.root]))
        self.assertNotIn('PRIVATE-TASK', lead_context)
        with self.assertRaises(ValueError):
            self.act(private, type='delegate', to='qa', description='private task', criteria=['x'])
        with self.assertRaises(ValueError):
            self.act(private, type='message', to='group', text='private')

    def test_separate_agent_filesystems(self):
        self.tool(self.root, 'files.write', path='secret.txt', content='owned by lead')
        with self.assertRaises(ValueError):
            self.tools.execute(self.engine.state, 'dev', 'files.read', {'path': 'secret.txt'})

    def test_artifacts_are_immutable_and_hash_checked(self):
        artifact = self.final_artifact()
        self.tool(self.root, 'files.write', path='report.md', content='modified')
        self.tools.execute(self.engine.state, 'dev', 'artifact.import', {'id': artifact, 'path': 'copy.md'})
        self.assertEqual((self.store.workspace('dev') / 'copy.md').read_text(), '# Actual test fixture')
        item = self.engine.state['artifacts'][artifact]
        (self.store.path / 'artifacts' / item['sha256']).write_text('tampered')
        with self.assertRaises(ValueError):
            self.tools.execute(self.engine.state, 'dev', 'artifact.import', {'id': artifact, 'path': 'copy.md'})

    def test_model_failure_preserves_queue_and_no_success(self):
        with patch.object(self.engine.client, 'generate', side_effect=ModelError('Network unavailable')):
            self.engine.run(1)
        self.assertIn(self.root, self.engine.state['queue'])
        self.assertEqual(metrics(self.engine.state)['task_completion_rate'], 0.)

    def test_repeated_bad_actions_pause_instead_of_loop(self):
        self.engine.client = FakeClient([{'type': 'invalid'}] * 3)
        self.engine.run(3)
        self.assertEqual(self.engine.state['tasks'][self.root]['status'], 'waiting_for_human')

    def test_unexpected_crash_cannot_count_as_success(self):
        with patch.object(self.engine.client, 'generate', side_effect=AssertionError('simulated crash')):
            with self.assertRaises(AssertionError):
                self.engine.run(1)
        row = metrics(self.engine.state)['runs'][0]
        self.assertTrue(row['fatal_error'])
        self.assertFalse(row['success'])

    def test_limits_prevent_unbounded_delegation(self):
        self.config.max_tasks = 1
        with self.assertRaises(ValueError):
            self.act(self.root, type='delegate', to='dev', description='work', criteria=['x'])

    def test_plan_cannot_repeat_without_doing_work(self):
        with self.assertRaisesRegex(ValueError, 'already has a plan'):
            self.act(self.root, type='plan', criteria=['reworded outcome'], text='Same plan')

    def test_root_task_must_plan_before_tools_or_delegation(self):
        root = self.engine.state['tasks'][self.root]
        root['planned'] = False
        with self.assertRaisesRegex(ValueError, 'Plan the root task'):
            self.engine.apply(root, {'type': 'tool', 'name': 'files.write',
                                     'args': {'path': 'too-early.txt', 'content': 'no'}})
        context = self.engine.context(root)
        self.assertIn('plan', context['allowed_actions'])
        self.assertNotIn('tool', context['allowed_actions'])
        self.assertNotIn('delegate', context['allowed_actions'])

    def test_dependency_waits_and_wakes_after_verified_completion(self):
        self.act(self.root, type='delegate', to='dev', description='Build', criteria=['exists'])
        dev = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(self.root, type='delegate', to='release', description='Package', criteria=['documented'], depends_on=[dev])
        release = self.engine.state['tasks'][self.root]['children'][-1]
        self.assertEqual(self.engine.state['tasks'][release]['status'], 'waiting_for_dependencies')
        self.assertNotIn(release, self.engine.state['queue'])
        review = self.finish(dev)
        self.approve_review(review)
        self.assertEqual(self.engine.state['tasks'][release]['status'], 'running')
        self.assertIn(release, self.engine.state['queue'])

    def test_parent_child_dependency_cycle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.act(self.root, type='delegate', to='dev', description='Build', criteria=['x'], depends_on=[self.root])
        self.act(self.root, type='delegate', to='dev', description='Build', criteria=['x'])
        dev = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(self.root, type='delegate', to='release', description='Package', criteria=['y'], depends_on=[dev])
        release = self.engine.state['tasks'][self.root]['children'][-1]
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.act(dev, type='delegate', to='qa', description='Check', criteria=['z'], depends_on=[release])

    def test_abort_records_failure_and_clears_pending_requests(self):
        self.act(self.root, type='ask', question='Need info')
        self.engine.abort('User stops this development trial')
        self.assertEqual(self.engine.state['runs']['r1']['status'], 'failed')
        self.assertEqual(self.engine.state['requests']['q1']['status'], 'cancelled')
        self.assertFalse(self.engine.state['queue'])
        self.assertEqual(metrics(self.engine.state)['task_completion_rate'], 0.)

    def test_restart_recovers_a_running_task_missing_from_queue(self):
        self.engine.state['queue'] = []
        self.store.save(self.engine.state)
        other = Engine(self.store, self.config, FakeClient(), self.tools)
        self.assertIn(self.root, other.state['queue'])

    def test_append_updates_only_the_own_file(self):
        self.tool(self.root, 'files.write', path='part.txt', content='first\n')
        self.tool(self.root, 'files.append', path='part.txt', content='second\n')
        self.assertEqual(self.tools.execute(self.engine.state, 'lead', 'files.read', {'path': 'part.txt'}), 'first\nsecond\n')
        with self.assertRaises(ValueError):
            self.tools.execute(self.engine.state, 'dev', 'files.append', {'path': 'part.txt', 'content': 'no'})

    def test_exact_replacement_avoids_rewriting_a_large_file(self):
        self.tool(self.root, 'files.write', path='page.txt', content='before\nTARGET\nafter\n')
        self.tool(self.root, 'files.replace', path='page.txt', old='TARGET', new='FIXED', expected_count=1)
        self.assertEqual((self.store.workspace('lead') / 'page.txt').read_text(), 'before\nFIXED\nafter\n')

    def test_replacement_match_mismatch_does_not_modify_file(self):
        self.tool(self.root, 'files.write', path='page.txt', content='match match')
        with self.assertRaisesRegex(ValueError, 'found 2'):
            self.tools.execute(self.engine.state, 'lead', 'files.replace', {'path': 'page.txt', 'old': 'match', 'new': 'changed'})
        self.assertEqual((self.store.workspace('lead') / 'page.txt').read_text(), 'match match')

    def test_zero_discovered_tests_create_validation_warning(self):
        with patch.object(self.tools.sandbox, 'run', return_value={'exit_code': 0, 'output': 'Ran 0 tests in 0.000s\n\nOK\n'}):
            event = self.tool(self.root, 'code.run', argv=['python', '-m', 'unittest', 'discover'])
            self.assertIn('validation_warnings', event['result'])

    def test_export_assembles_final_files_in_original_paths(self):
        artifact = self.final_artifact()
        self.finish(self.root, artifacts=[artifact])
        output = export(self.store, self.engine.state)
        delivery = output / 'delivery' / 'r1'
        self.assertEqual((delivery / 'report.md').read_text(), '# Actual test fixture')
        self.assertFalse(json.loads((delivery / 'delivery_manifest.json').read_text())['human_accepted'])

    def test_export_contains_trace_metrics_artifacts(self):
        artifact = self.final_artifact()
        output = export(self.store, self.engine.state)
        self.assertTrue((output / 'dialogue.md').is_file())
        self.assertTrue((output / 'artifacts' / artifact / 'report.md').is_file())
        report = json.loads((output / 'metrics.json').read_text())
        self.assertEqual(report['task_completion_rate'], 0.)
        self.assertNotIn('.env', [p.name for p in output.rglob('*')])
        report_text = (
            output / 'report.md'
        ).read_text(encoding='utf-8')

        self.assertIn('## Global metrics', report_text)
        self.assertIn('Overall goal completion rate', report_text)
        self.assertIn('Global subtask completion rate', report_text)
        self.assertIn('Total human wait time', report_text)

    def test_secret_redaction_in_tool_results(self):
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'sentinel-private-token'}):
            with patch.object(self.tools, 'execute', return_value={'output': 'sentinel-private-token'}):
                self.tool(self.root, 'code.run', argv=['python', '-V'])
        self.assertNotIn('sentinel-private-token', json.dumps(self.engine.state['events']))


class SecurityAndDataTests(unittest.TestCase):
    def test_path_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ['../escape.txt', '/tmp/x.txt', 'C:/x.txt', 'a/../../x.txt', '.git/config.txt', 'a\\x.txt', 'report.pdf', 'report.docx']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    safe_path(root, name)
            self.assertEqual(safe_path(root, 'a/report.md'), (root / 'a' / 'report.md').resolve())

    def test_symlink_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'root'
            root.mkdir()
            try:
                (root / 'link').symlink_to(Path(temp), target_is_directory=True)
            except OSError:
                self.skipTest('Symlink creation not permitted on this Windows host')
            with self.assertRaises(ValueError):
                safe_path(root, 'link/out.txt')

    def test_group_ids_cannot_escape_runtime(self):
        for value in ['../x', 'x/y', '', 'x:y']:
            with self.assertRaises(ValueError):
                ident(value)

    def test_private_network_urls_are_blocked(self):
        private = [(2, 1, 6, '', ('127.0.0.1', 443))]
        with patch('socket.getaddrinfo', return_value=private):
            for url in ['https://localhost/x', 'http://example.com', 'https://user:pass@example.com', 'https://example.com:8443']:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    public_url(url)

    def test_primary_scenario_prompts_load_without_macos_noise(self):
        scenarios = [load_scenario(p) for p in discover('NLP_Mid_Test')]
        self.assertEqual(len(scenarios), 3)
        for scenario in scenarios:
            self.assertEqual(len(scenario['roles']), 4)
            self.assertEqual(len(scenario['tasks']), 2)

    def test_html_scripts_not_in_research_text(self):
        parser = TextParser()
        parser.feed('<h1>Facts</h1><script>untrusted()</script><p>Evidence</p>')
        self.assertEqual(parser.parts, ['Facts', 'Evidence'])


if __name__ == '__main__':
    unittest.main()
