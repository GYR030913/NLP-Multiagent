"""Offline operator workflows; never contact a model provider or execute Docker."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from collab.cli import locked, main as cli_main
from collab.config import Config
from collab.llm import ModelError
from collab.report import metrics
from collab.ui_service import UIService
from tests.test_platform import FakeClient

BASE = Path(__file__).resolve().parents[1]


class UIServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config('hkgai', 'https://example.org', 'fake', 'secret-test-key')
        self.service = UIService(self.temp.name, BASE / 'NLP_Mid_Test', self.config, lambda c: FakeClient())
        self.service.initialize()

    def tearDown(self):
        for job in self.service._jobs.values():
            job['stop'].set()
            job['thread'].join(3)
        self.temp.cleanup()

    def engine(self, group='software'):
        return self.service.engine(self.service.store(group))

    def request(self, kind, **payload):
        self.service.submit('software', 'Build fixture')
        engine = self.engine()
        return engine.request(engine.state['tasks']['t1'], kind, **payload)

    def wait(self, group='software'):
        self.service._jobs[group]['thread'].join(3)
        self.assertFalse(self.service.job(group)['active'])

    def test_initialization_preserves_state_and_groups_are_isolated(self):
        self.service.submit('software', 'unique')
        original = self.service.store('software').state_path.read_bytes()
        self.service.initialize()
        self.assertEqual(original, self.service.store('software').state_path.read_bytes())
        self.assertEqual(len(self.service.snapshot('software')['members']), 4)
        self.assertEqual(self.service.snapshot('newsroom')['tasks'], {})
        with self.assertRaises(ValueError):
            self.service.snapshot('../software')

    def test_benchmark_submission_is_exact_and_idempotent(self):
        self.service.submit('software', benchmark=1)
        self.service.submit('software', benchmark=1)
        state = self.engine().state
        self.assertEqual(len(state['runs']), 1)
        self.assertEqual(state['tasks']['t1']['description'], state['scenario']['tasks'][0]['text'])
        with self.assertRaises(ValueError):
            self.service.submit('software', benchmark=2)
        with self.assertRaises(ValueError):
            self.service.submit('software', benchmark=0)

    def test_custom_duplicate_and_invalid_recipient_do_not_write(self):
        self.service.submit('software', 'custom')
        self.service.submit('software', 'custom')
        with self.assertRaises(ValueError):
            self.service.submit('software', 'private', recipient='unknown')
        self.assertEqual(len(self.engine().state['runs']), 1)

    def test_private_messages_and_system_events_stay_in_channel(self):
        agents = list(self.engine().state['roles'])
        self.service.submit('software', 'public')
        self.service.submit('software', 'private-secret', recipient=agents[1])
        self.service.say('software', 'dm-secret', agents[1])
        self.service.say('software', 'group supplement')
        public = json.dumps(self.service.snapshot('software'))
        private = self.service.snapshot('software', agents[1])
        self.assertNotIn('private-secret', public)
        self.assertNotIn('dm-secret', public)
        self.assertIn('private-secret', json.dumps(private))
        self.assertNotIn('private-secret', json.dumps(self.service.snapshot('software', agents[2])))
        engine = self.engine()
        self.assertNotIn('dm-secret', json.dumps(engine.context(engine.state['tasks']['t1'])))
        self.assertIn('dm-secret', json.dumps(engine.context(engine.state['tasks']['t2'])))

    def test_clarification_and_duplicate_answer(self):
        request = self.request('clarification', question='Which language?')
        with self.assertRaises(ValueError):
            self.service.answer('software', request, '', None)
        with self.assertRaises(ValueError):
            self.service.answer('software', request, 'yes', True)
        self.service.answer('software', request, 'Python')
        self.assertEqual(self.engine().state['tasks']['t1']['status'], 'running')
        with self.assertRaises(ValueError):
            self.service.answer('software', request, 'Again')

    def test_approval_requires_explicit_decision_and_executes_once(self):
        request = self.request('approval', action={'type': 'tool', 'name': 'files.delete', 'args': {'path': 'x.txt'}})
        with patch('collab.tools.Tools.execute', return_value={'ok': True}) as execute:
            with self.assertRaises(ValueError):
                self.service.answer('software', request, 'yes')
            execute.assert_not_called()
            self.service.answer('software', request, approved=True)
            execute.assert_called_once()
            with self.assertRaises(ValueError):
                self.service.answer('software', request, approved=True)
            execute.assert_called_once()

    def test_reject_requires_reason_never_executes_tool(self):
        request = self.request('approval', action={'type': 'tool', 'name': 'files.delete', 'args': {}})
        with patch('collab.tools.Tools.execute') as execute:
            with self.assertRaises(ValueError):
                self.service.answer('software', request, approved=False)
            self.service.answer('software', request, 'Do not delete', False)
            execute.assert_not_called()
        self.assertFalse(self.engine().state['requests'][request]['approved'])

    def test_acceptance_validates_artifacts_and_rejection_reopens(self):
        request = self.request('acceptance', summary='Delivery', artifacts=['bad'])
        with self.assertRaises((ValueError, KeyError)):
            self.service.answer('software', request, approved=True)
        self.assertEqual(self.engine().state['requests'][request]['status'], 'pending')
        self.service.answer('software', request, 'Missing file', False)
        self.assertEqual(self.engine().state['tasks']['t1']['status'], 'running')

    def test_resume_recovers_missing_queue_without_duplicate_task(self):
        self.service.submit('software', 'resume fixture')
        engine = self.engine()
        engine.state['queue'] = []
        engine.store.save(engine.state)
        self.service.client_factory = lambda c: FakeClient([{'type': 'plan', 'criteria': ['Test'], 'text': 'Plan'}])
        self.service.start('software', 1)
        self.wait()
        state = self.engine().state
        self.assertEqual(len(state['runs']), 1)
        self.assertTrue(state['tasks']['t1']['planned'])
        self.assertEqual(state['runs']['r1']['actions_used'], 1)
        self.assertEqual(self.service.snapshot('software')['runs'][0]['remaining_actions'], 359)

    def test_network_error_stops_batch_and_is_resumable(self):
        self.service.submit('software', 'network')
        class Broken:
            def generate(self, messages):
                raise ModelError('network timeout')
        self.service.client_factory = lambda c: Broken()
        self.service.start('software', 10)
        self.wait()
        state = self.engine().state
        self.assertEqual(state['runs']['r1']['model_calls'], 1)
        self.assertIn('t1', state['queue'])
        self.assertTrue(any(e['kind'] == 'model_error' for e in state['events']))

    def test_pause_and_concurrent_writer_protection(self):
        entered, release = threading.Event(), threading.Event()
        class Slow:
            usage = {}
            def generate(self, messages):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('Test release timeout')
                return {'type': 'plan', 'criteria': ['One'], 'text': 'Plan'}
        self.service.submit('software', 'slow')
        self.service.client_factory = lambda c: Slow()
        self.service.start('software', 10)
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.service.snapshot('software')['execution']['active'])
            for operation in (lambda: self.service.start('software'), lambda: self.service.say('software', 'race'), lambda: self.service.export_group('software'), lambda: self.service.benchmark()):
                with self.assertRaises(ValueError):
                    operation()
            # An independent process uses exactly the same CLI lock.
            code = 'from collab.cli import locked; from collab.store import Store; import sys\nwith locked(Store(sys.argv[1], "software")): pass'
            process = subprocess.run([sys.executable, '-c', code, self.temp.name], cwd=BASE, capture_output=True, text=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn('Another writer', process.stderr)
            self.service.say('newsroom', 'Other group remains writable')
            self.service.pause('software')
        finally:
            release.set()
        self.wait()
        self.assertEqual(self.engine().state['runs']['r1']['model_calls'], 1)
        self.service.say('software', 'Lock released')

    def test_send_automatically_executes_and_duplicate_does_not_restart(self):
        self.service.client_factory = lambda c: FakeClient([{'type': 'plan', 'criteria': ['Check'], 'text': 'Plan'}])
        self.service.send('software', 'group', 'auto-start', steps=1)
        self.wait()
        self.assertTrue(self.engine().state['tasks']['t1']['planned'])
        self.service.send('software', 'group', 'auto-start', steps=1)
        self.assertEqual(self.engine().state['runs']['r1']['model_calls'], 1)

    def test_pause_discards_late_action_and_end_preserves_failure(self):
        for command in ('pause', 'end'):
            with self.subTest(command=command):
                group = 'software' if command == 'pause' else 'newsroom'
                entered, release = threading.Event(), threading.Event()
                class Slow:
                    usage = {'total_tokens': 7}
                    def generate(self, messages):
                        entered.set()
                        release.wait(3)
                        return {'type': 'plan', 'criteria': ['Must not apply'], 'text': 'Late response'}
                self.service.client_factory = lambda c: Slow()
                self.service.send(group, 'group', 'stop fixture', steps=10)
                try:
                    self.assertTrue(entered.wait(2))
                    getattr(self.service, command)(group)
                    self.assertIn('requested', self.service.job(group)['message'])
                finally:
                    release.set()
                self.wait(group)
                state = self.service.store(group).load()
                self.assertFalse(state['tasks']['t1']['planned'])
                self.assertEqual(state['runs']['r1']['model_calls'], 1)
                self.assertEqual(state['runs']['r1']['tokens'], 7)
                self.assertTrue(any(e['kind'] == 'execution_paused' for e in state['events']))
                self.assertEqual(state['runs']['r1']['status'], 'failed' if command == 'end' else 'running')
                if command == 'end':
                    self.assertEqual(state['queue'], [])
                else:
                    self.assertIn('t1', state['queue'])

    def test_end_is_scoped_to_selected_conversation_and_cancels_requests(self):
        self.service.submit('software', 'public')
        self.service.submit('software', 'private', recipient='dev')
        engine = self.engine()
        engine.request(engine.state['tasks']['t2'], 'clarification', question='Private question')
        self.service.end('software', 'dev')
        state = self.engine().state
        self.assertEqual(state['runs']['r1']['status'], 'running')
        self.assertEqual(state['runs']['r2']['status'], 'failed')
        self.assertEqual(state['requests']['q1']['status'], 'cancelled')
        self.assertEqual(self.service.end('software', 'dev'), 'No active task in this conversation')

    def test_answer_auto_continues_and_does_not_answer_twice(self):
        request = self.request('clarification', question='Language?')
        self.service.client_factory = lambda c: FakeClient([{'type': 'plan', 'criteria': ['Test'], 'text': 'Plan'}])
        self.service.answer_and_continue('software', request, 'Python', steps=1)
        self.wait()
        self.assertTrue(self.engine().state['tasks']['t1']['planned'])
        with self.assertRaises(ValueError):
            self.service.answer_and_continue('software', request, 'Again', steps=1)
        self.assertEqual(self.engine().state['runs']['r1']['model_calls'], 1)

    def test_followup_after_completion_creates_new_run_with_history(self):
        self.service.submit('software', 'Original work')
        engine = self.engine()
        engine.state['tasks']['t1']['status'] = 'completed'
        engine.end_run('r1', 'completed')
        self.service.client_factory = lambda c: FakeClient([{'type': 'message', 'to': 'group', 'text': 'Here is what we delivered.'}])
        self.service.send('software', 'group', 'What did you deliver?', 'message', 1)
        self.wait()
        engine = self.engine()
        self.assertEqual(engine.state['runs']['r1']['status'], 'completed')
        self.assertEqual(engine.state['runs']['r2']['model_calls'], 1)
        context = engine.context(engine.state['tasks']['t2'])
        self.assertIn('Original work', json.dumps(context))
        self.assertTrue(any(e.get('text') == 'Here is what we delivered.' for e in engine.state['events']))

    def test_existing_cli_writer_blocks_ui(self):
        with locked(self.service.store('software')):
            with self.assertRaises(ValueError):
                self.service.submit('software', 'blocked')
        self.assertEqual(self.engine().state['runs'], {})

    def test_evaluation_and_complete_exports_reuse_backend(self):
        self.service.submit('software', benchmark=1)
        with patch('collab.report.now', return_value='2099-01-01T00:00:00+00:00'):
            self.assertEqual(self.service.snapshot('software')['metrics'], metrics(self.engine().state))
        benchmark = self.service.benchmark()
        self.assertEqual(benchmark['scenarios']['newsroom']['tasks']['task_1']['status'], 'not_started')
        files = self.service.export_group('software')
        self.assertTrue({'metrics.json', 'report.md', 'dialogue.jsonl', 'dialogue.md'}.issubset({Path(p).name for p in files}))
        benchmark_files = self.service.benchmark(True)
        self.assertEqual({Path(p).name for p in benchmark_files}, {'benchmark_metrics.json', 'benchmark_report.md'})
        for path in files + benchmark_files:
            self.assertTrue(Path(path).is_file())

    def test_tampered_artifact_and_export_symlink_are_rejected(self):
        self.service.submit('software', 'artifact')
        engine = self.engine()
        engine.tools.execute(engine.state, 'lead', 'files.write', {'path': 'test.md', 'content': 'Original'})
        artifact = engine.tools.execute(engine.state, 'lead', 'artifact.publish', {'path': 'test.md'})
        engine.store.save(engine.state)
        blob = engine.store.path / 'artifacts' / artifact['sha256']
        blob.write_text('Tampered')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.service.export_group('software')
        output = engine.store.path / 'export'
        target = Path(self.temp.name) / 'untouched.md'
        target.write_text('keep')
        try:
            (output / 'escape.md').symlink_to(target)
        except OSError as exc:
            if getattr(exc, 'winerror', None) != 1314:
                raise
            self.skipTest('Symlink creation not permitted on this Windows host')
        with self.assertRaisesRegex(ValueError, 'links'):
            self.service.export_group('software')
        self.assertEqual(target.read_text(), 'keep')

    def test_benchmark_waiting_status_does_not_change_success_metric(self):
        self.service.submit('software', benchmark=1)
        engine = self.engine()
        engine.request(engine.state['tasks']['t1'], 'clarification', question='Need input')
        result = self.service.benchmark()
        row = result['scenarios']['software']['tasks']['task_1']
        self.assertEqual(row['display_status'], 'waiting_for_human')
        self.assertEqual(result['benchmark_completion_rate'], 0)
        self.assertFalse(row['success'])

    def test_gradio_build_and_export_callback_when_installed(self):
        import importlib.util
        if importlib.util.find_spec('gradio') is None:
            self.skipTest('Optional Gradio dependency not installed')
        from scripts.run_ui import build_ui
        app = build_ui(self.service)
        try:
            # Exercise the actual callback used by the download component.
            export_callback = next(fn.fn for fn in app.fns.values()
                                   if fn.outputs and getattr(fn.outputs[0], 'label', '') == 'Final deliveries, reports & full archive')
            self.assertFalse(any(c['type'] == 'tabs' for c in app.config['components']))
            labels = [str(c.get('props', {}).get('label', '')) + str(c.get('props', {}).get('value', '')) for c in app.config['components'] if c['type'] != 'state']
            self.assertFalse(any('原始' in label or 'Task 1' in label or 'Task 2' in label for label in labels))
            self.assertTrue(any('■ End' in label for label in labels))
            refresh = next(fn.fn for fn in app.fns.values() if len(fn.outputs) == 14)
            initial = refresh('software', 'group', None, '')
            repeated = refresh('software', 'group', None, initial[-2])
            self.assertIsNone(repeated[3])  # no pending request on an empty group
            paths = export_callback('software')
            self.assertTrue(paths)
            self.assertTrue(all(Path(p).is_relative_to(app.download_cache.name) for p in paths))
            self.assertIn('metrics.json', [Path(p).name for p in paths])
            import zipfile
            self.assertIn('complete_execution_archive.zip', [Path(p).name for p in paths])
            self.service.submit('software', 'Export fixture')
            engine = self.engine()
            ids = []
            for content in ('Draft', 'Final'):
                engine.tools.execute(engine.state, 'lead', 'files.write', {'path': 'article.md', 'content': content})
                artifact = engine.tools.execute(engine.state, 'lead', 'artifact.publish', {'path': 'article.md'})
                ids.append(artifact['id'])
            engine.state['tasks']['t1']['submission'] = {'artifacts': [ids[-1]]}
            engine.store.save(engine.state)
            files = export_callback('software')
            names = [Path(p).name for p in files]
            self.assertEqual(sum(name.endswith('article.md') for name in names), 1)
            self.assertIn('delivery__r1__article.md', names)
            archive = next(p for p in files if p.endswith('.zip'))
            with zipfile.ZipFile(archive) as handle:
                self.assertEqual(handle.read('delivery/r1/article.md'), b'Final')
                self.assertTrue(all(f'artifacts/{aid}/article.md' in handle.namelist() for aid in ids))
            q = engine.request(engine.state['tasks']['t1'], 'clarification', question='Question')
            shown = refresh('software', 'group', None, '')
            self.assertEqual(shown[3], 'software:group:' + q)
            self.assertEqual(shown[-1], '')

        finally:
            app.download_cache.cleanup()
            app.close()

    def test_cli_compatibility_and_redaction(self):
        with patch('collab.cli.Config.from_env', return_value=self.config):
            self.assertEqual(cli_main(['--runtime', self.temp.name, 'say', 'software', 'CLI text']), 0)
        self.assertIn('CLI text', json.dumps(self.service.snapshot('software')))
        self.service.say('software', 'secret-test-key')
        self.assertNotIn('secret-test-key', json.dumps(self.service.snapshot('software')))

    def test_invalid_steps_and_corrupt_state_are_reported(self):
        for value in (0, -1, 121, 1.5, True):
            with self.assertRaises(ValueError):
                self.service.start('software', value)
        self.service.store('software').state_path.write_text('{broken')
        with self.assertRaises(json.JSONDecodeError):
            self.service.snapshot('software')


if __name__ == '__main__':
    unittest.main()
