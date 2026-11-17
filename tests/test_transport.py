import io
import http.client
import json
import os
import tempfile
import unittest
import urllib.error
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from collab.config import Config
from collab.llm import Client, ModelError
from collab.sandbox import Sandbox
from collab.report import metrics
from collab.store import Store
from collab.tools import Tools, PublicHTTPSConnection
from collab.cli import locked


class Response(io.BytesIO):
    def __init__(self, content):
        super().__init__(json.dumps(content).encode())


def response(content='{"status":"ok"}', finish_reason='stop'):
    return Response({'choices': [{'message': {'content': content}, 'finish_reason': finish_reason}],
                     'usage': {'total_tokens': 42}})


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.config = Config('hkgai', 'https://api1.hkgai.net/v1', 'course-model',
                             'test-only-sentinel', transport='urllib')

    def test_correct_endpoint_and_json_protocol(self):
        with patch('urllib.request.urlopen', return_value=response()) as opened:
            client = Client(self.config)
            self.assertEqual(client.generate([{'role': 'user', 'content': 'JSON'}]), {'status': 'ok'})
            request = opened.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api1.hkgai.net/v1/chat/completions')
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-sentinel')
            self.assertEqual(opened.call_args.kwargs['timeout'], 120)
            self.assertEqual(client.usage['total_tokens'], 42)

    def test_configured_total_deadline_is_honored(self):
        self.config.request_timeout_seconds = 240
        with patch('urllib.request.urlopen', return_value=response()) as opened:
            Client(self.config).generate([])
            self.assertEqual(opened.call_args.kwargs['timeout'], 240)

    def test_api_key_header_can_be_configured(self):
        self.config.auth_header = 'api-key'
        with patch('urllib.request.urlopen', return_value=response()) as opened:
            Client(self.config).generate([])
            self.assertEqual(opened.call_args.args[0].get_header('Api-key'), 'test-only-sentinel')

    def test_http_auth_failure_does_not_echo_response_or_key(self):
        error = urllib.error.HTTPError('https://example.org', 401, 'secret-body', {}, None)
        with patch('urllib.request.urlopen', side_effect=error) as opened:
            with self.assertRaises(ModelError) as caught:
                Client(self.config).generate([])
            self.assertEqual(opened.call_count, 1)
            self.assertNotIn('test-only-sentinel', str(caught.exception))
            self.assertNotIn('secret-body', str(caught.exception))

    def test_transient_http_error_retries(self):
        error = urllib.error.HTTPError('https://example.org', 429, 'limited', {}, None)
        with patch('urllib.request.urlopen', side_effect=[error, response()]) as opened, patch('time.sleep'):
            self.assertEqual(Client(self.config).generate([])['status'], 'ok')
            self.assertEqual(opened.call_count, 2)

    def test_remote_disconnect_is_retried_as_a_transport_error(self):
        disconnect = http.client.RemoteDisconnected('Remote end closed connection')
        with patch('urllib.request.urlopen', side_effect=[disconnect, response()]) as opened, patch('time.sleep'):
            self.assertEqual(Client(self.config).generate([])['status'], 'ok')
            self.assertEqual(opened.call_count, 2)

    def test_curl_transport_keeps_credential_out_of_process_arguments(self):
        self.config.transport = 'curl'
        payload = response().getvalue()
        completed = SimpleNamespace(
            returncode=0,
            stdout=payload + b'\n200',
            stderr=b'',
        )
        with patch('collab.llm.shutil.which', return_value='curl.exe'), patch(
            'collab.llm.subprocess.run', return_value=completed
        ) as invoked:
            result = Client(self.config).generate([])

        self.assertEqual(result, {'status': 'ok'})
        argv = invoked.call_args.args[0]
        self.assertNotIn('test-only-sentinel', ' '.join(argv))
        self.assertEqual(
            invoked.call_args.kwargs['env']['COLLAB_LLM_AUTH_VALUE'],
            'Bearer test-only-sentinel',
        )
        self.assertTrue(any('%{http_code}' in str(item) for item in argv))
        self.assertTrue(any('{{COLLAB_LLM_AUTH_VALUE}}' in str(item) for item in argv))

    def test_curl_http_error_is_not_reported_as_success(self):
        self.config.transport = 'curl'
        completed = SimpleNamespace(returncode=0, stdout=b'forbidden\n403', stderr=b'')
        with patch('collab.llm.shutil.which', return_value='curl.exe'), patch(
            'collab.llm.subprocess.run', return_value=completed
        ):
            with self.assertRaisesRegex(ModelError, 'LLM HTTP 403'):
                Client(self.config).generate([])

    def test_truncated_json_is_not_a_completed_action(self):
        with patch('urllib.request.urlopen', side_effect=[response(finish_reason='length'), response(finish_reason='length')]) as opened:
            with self.assertRaisesRegex(ModelError, 'truncated'):
                Client(self.config).generate([])
            self.assertEqual(opened.call_count, 2)

    def test_truncated_response_recovers_once_with_short_action_instruction(self):
        with patch('urllib.request.urlopen', side_effect=[response(finish_reason='length'), response()]) as opened:
            client = Client(self.config)
            self.assertEqual(client.generate([]), {'status': 'ok'})
            self.assertEqual(client.usage['total_tokens'], 84)
            history = json.loads(opened.call_args.args[0].data)['messages']
            self.assertIn('short JSON action', history[-1]['content'])
            self.assertEqual(opened.call_count, 2)

    def test_invalid_json_is_a_recoverable_model_error(self):
        with patch('urllib.request.urlopen', return_value=response('not JSON')):
            with self.assertRaisesRegex(ModelError, 'Invalid model JSON'):
                Client(self.config).generate([])

    def test_missing_key_makes_no_request(self):
        self.config.api_key = ''
        with patch('urllib.request.urlopen') as opened:
            with self.assertRaises(ModelError):
                Client(self.config).generate([])
            opened.assert_not_called()

    def test_blocked_network_not_retried(self):
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError(PermissionError(10013, 'blocked'))) as opened:
            with self.assertRaisesRegex(ModelError, 'Network blocked'):
                Client(self.config).generate([])
            self.assertEqual(opened.call_count, 1)

    def test_prohibited_provider_rejected(self):
        with patch.dict(os.environ, {'LLM_PROVIDER': 'openai'}):
            with self.assertRaises(ValueError):
                Config.from_env()

    def test_total_deadline_stops_even_if_transport_does_not_timeout(self):
        release = threading.Event()
        self.config.request_timeout_seconds = .05

        def stall(*args, **kwargs):
            release.wait(1.)
            return response()

        with patch('urllib.request.urlopen', side_effect=stall) as opened:
            try:
                with self.assertRaisesRegex(ModelError, 'total request deadline'):
                    Client(self.config).generate([])
                self.assertEqual(opened.call_count, 1)
            finally:
                release.set()


class SandboxBoundaryTests(unittest.TestCase):
    def test_execution_is_always_in_restricted_docker(self):
        def execute(argv, **kwargs):
            self.assertEqual(argv[:2], ['docker', 'run'])
            self.assertIn('--read-only', argv)
            self.assertIn('no-new-privileges', argv)
            self.assertEqual(argv[argv.index('--network') + 1], 'none')
            self.assertIn('--cap-drop', argv)
            self.assertNotIn('-e', argv)
            kwargs['stdout'].write(b'42\n')
            return type('Result', (), {'returncode': 0})()
        with tempfile.TemporaryDirectory() as temp, patch('subprocess.run', side_effect=execute):
            result = Sandbox('image').run(Path(temp), ['python', 'main.py'])
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(result['output'], '42\n')

    def test_missing_docker_does_not_fall_back_to_host(self):
        with tempfile.TemporaryDirectory() as temp, patch('subprocess.run', side_effect=FileNotFoundError) as run:
            with self.assertRaisesRegex(RuntimeError, 'No host execution fallback'):
                Sandbox('image').run(Path(temp), ['python', 'main.py'])
            self.assertEqual(run.call_count, 1)

    def test_overlapping_human_wait_is_not_double_counted(self):
        state = {'runs': {'r1': {'id': 'r1', 'status': 'running', 'started_at': '2026-01-01T00:00:00+00:00',
                    'finished_at': '2026-01-01T00:00:30+00:00', 'fatal_error': False,
                    'model_calls': 0, 'tokens': 0, 'provider': 'hkgai', 'model': 'test', 'memory_before': {}}},
                 'tasks': {'t1': {'run': 'r1', 'kind': 'work', 'parent': None}},
                 'requests': {'q1': {'task': 't1', 'created_at': '2026-01-01T00:00:05+00:00', 'answered_at': '2026-01-01T00:00:15+00:00'},
                              'q2': {'task': 't1', 'created_at': '2026-01-01T00:00:10+00:00', 'answered_at': '2026-01-01T00:00:20+00:00'}}}
        row = metrics(state)['runs'][0]
        self.assertEqual(row['human_wait_seconds'], 15.)
        self.assertEqual(row['duration_seconds'], 30.)


class SearchAndFetchTests(unittest.TestCase):
    def test_keyless_search_is_real_api_request_without_model_key(self):
        config = Config('hkgai', 'https://api1.hkgai.net/v1', 'model', 'private-model-key')
        with tempfile.TemporaryDirectory() as temp:
            tools = Tools(Store(temp, 'search'), config)
            result = Response({'results': [{'url': 'https://hkust.edu.hk/news', 'title': 'News', 'content': 'Fact', 'published_date': '2026-11-01'}]})
            with patch('urllib.request.urlopen', return_value=result) as opened:
                state = {'sources': {}}
                rows = tools.execute(state, 'researcher', 'web.search', {'query': 'campus news'})
                request = opened.call_args.args[0]
                self.assertEqual(request.get_header('X-tavily-access-mode'), 'keyless')
                self.assertIsNone(request.get_header('Authorization'))
                self.assertNotIn('private-model-key', str(request.headers) + str(request.data))
                self.assertEqual(rows[0]['published_date'], '2026-11-01')
                self.assertEqual(rows[0]['kind'], 'search_snippet')

    def test_keyed_search_only_uses_search_credential(self):
        config = Config('hkgai', 'https://example.org', 'model', 'model-key', 'search-key')
        with tempfile.TemporaryDirectory() as temp, patch('urllib.request.urlopen', return_value=Response({'results': []})) as opened:
            Tools(Store(temp, 'search'), config).execute({'sources': {}}, 'agent', 'web.search', {'query': 'test'})
            request = opened.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer search-key')
            self.assertIsNone(request.get_header('X-tavily-access-mode'))

    def test_socket_revalidates_dns_and_rejects_rebinding(self):
        conn = PublicHTTPSConnection('example.org')
        rows = [(2, 1, 6, '', ('127.0.0.1', 443))]
        with patch('socket.getaddrinfo', return_value=rows), patch('socket.create_connection') as connect:
            with self.assertRaises(ValueError):
                conn.connect()
            connect.assert_not_called()


class WriterLockTests(unittest.TestCase):
    def test_two_simultaneous_writers_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp, 'group')
            with locked(store):
                with self.assertRaisesRegex(ValueError, 'Another writer'):
                    with locked(store):
                        self.fail('Concurrent writer acquired the lock')
            with locked(store):
                pass

    def test_lock_releases_after_an_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp, 'group')
            with self.assertRaises(RuntimeError):
                with locked(store):
                    raise RuntimeError('simulated interruption')
            with locked(store):
                pass

    def test_legacy_directory_lock_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp, 'group')
            (store.path / 'writer.lock').mkdir()
            with self.assertRaisesRegex(ValueError, 'Legacy directory lock'):
                with locked(store):
                    self.fail('Legacy writer protection was bypassed')


if __name__ == '__main__':
    unittest.main()
