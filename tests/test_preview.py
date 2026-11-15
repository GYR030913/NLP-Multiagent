import hashlib
import tempfile
import unittest
from pathlib import Path
from scripts.preview_booking import prepare, command
from collab.store import Store


class PreviewTests(unittest.TestCase):
    def fixture(self, temp):
        store = Store(temp, 'preview')
        artifacts = {}
        for index, name in enumerate(('app.py', 'booking_core.py'), 1):
            content = b'# Static fixture; not executable test input\n'
            digest = hashlib.sha256(content).hexdigest()
            blob = store.path / 'artifacts' / digest
            blob.parent.mkdir(exist_ok=True)
            blob.write_bytes(content)
            artifacts[f'a{index}'] = {'path': name, 'sha256': digest}
        state = {'runs': {'r1': {'id': 'r1', 'root': 't1', 'status': 'running'}},
                 'tasks': {'t1': {'submission': {'artifacts': list(artifacts)}}}, 'artifacts': artifacts}
        return store, state

    def test_preview_does_not_approve_or_modify_task_state(self):
        with tempfile.TemporaryDirectory() as temp:
            store, state = self.fixture(temp)
            source, data = prepare(store, state, 'r1')
            self.assertTrue((source / 'app.py').exists())
            self.assertEqual(state['runs']['r1']['status'], 'running')
            (data / 'existing.txt').write_text('preserve')
            prepare(store, state, 'r1')
            self.assertEqual((data / 'existing.txt').read_text(), 'preserve')

    def test_preview_only_mounts_reviewed_source_and_separate_data(self):
        argv = command('image', Path('/source'), Path('/data'), 'test-container', 8000)
        self.assertEqual(argv[:2], ['docker', 'run'])
        self.assertIn('127.0.0.1:8000:8000', argv)
        self.assertIn('--read-only', argv)
        self.assertIn('no-new-privileges', argv)
        self.assertNotIn('-e', argv)
        self.assertTrue(any(x.endswith('target=/workspace,readonly') for x in argv))

    def test_hash_mismatch_stops_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            store, state = self.fixture(temp)
            artifact = state['artifacts']['a1']
            (store.path / 'artifacts' / artifact['sha256']).write_text('modified')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                prepare(store, state, 'r1')

    def test_invalid_port_stops_before_launch(self):
        with self.assertRaises(ValueError):
            command('image', Path('/source'), Path('/data'), 'test', 0)
