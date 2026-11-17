import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import threading
import queue
from pathlib import Path


class ModelError(RuntimeError):
    def __init__(self, message, usage=None):
        super().__init__(message)
        self.usage = usage or {}


class CurlTransportError(OSError):
    """The curl fallback could not complete the provider request."""


class Client:
    """Provider-neutral chat JSON protocol; no OpenAI SDK or fallback provider."""
    def __init__(self, config):
        self.config = config
        self.usage = {}
        self.transport_used = None

    def generate(self, messages):
        self.usage = {}
        if not self.config.api_key:
            raise ModelError('Missing LLM_API_KEY. Configure .env locally first.')

        def make_request(history):
            body = json.dumps({'model': self.config.model, 'messages': history,
                               'max_tokens': self.config.max_output_tokens, 'response_format': {'type': 'json_object'}}).encode()
            auth = self.config.api_key if self.config.auth_header.lower() == 'api-key' else 'Bearer ' + self.config.api_key
            request = urllib.request.Request(self.config.base_url + '/chat/completions', data=body,
                        headers={'Content-Type': 'application/json'})
            request.add_unredirected_header(self.config.auth_header, auth)
            return request

        request = make_request(messages)
        repaired_truncation = False
        for attempt in range(3):
            try:
                result = self.request_with_deadline(request)
                for key, value in result.get('usage', {}).items():
                    self.usage[key] = self.usage.get(key, 0) + value if isinstance(value, (int, float)) else value
                choice = result['choices'][0]
                if choice.get('finish_reason') == 'length':
                    if repaired_truncation or attempt == 2:
                        raise ModelError('Model output truncated after bounded repair. Use a short edit or delegate smaller work.', self.usage)
                    repaired_truncation = True
                    repair = ('Your previous response was truncated and NO action from it was executed. '
                              'Return ONE short JSON action under 1200 characters. Do not rewrite a complete large file '
                              'or repeat a long summary. For an edit use files.replace if listed, or code.run with '
                              'a short Python exact-replacement script inside the sandbox; otherwise write one small chunk. '
                              'For the current task choose the smallest useful next action.')
                    request = make_request(messages + [{'role': 'system', 'content': repair}])
                    continue
                content = choice['message']['content']
                if not isinstance(content, str):
                    raise ValueError('Missing text content')
                if content.strip().startswith('```'):
                    content = content.strip().split('\n', 1)[1].rsplit('```', 1)[0]
                action = json.loads(content)
                if not isinstance(action, dict):
                    raise ValueError('Action must be a JSON object')
                return action
            except ModelError as exc:
                exc.usage = dict(self.usage)
                raise
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise ModelError(f'LLM HTTP {exc.code}; check endpoint/model/credentials (response omitted).', self.usage) from None
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                if isinstance(getattr(exc, 'reason', None), PermissionError):
                    raise ModelError('Network blocked by execution environment; run scripts/probe_api.py in your own terminal.', self.usage) from None
                if attempt == 2:
                    raise ModelError('LLM connection failed after retries.', self.usage) from None
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise ModelError(f'Invalid model JSON response: {type(exc).__name__}', self.usage) from None
            time.sleep(2 ** attempt)

    def request_with_deadline(self, request):
        if self.should_use_curl_first():
            return self.request_with_curl(request)
        try:
            return self.request_with_urllib(request)
        except urllib.error.HTTPError:
            raise
        except (ModelError, TimeoutError, urllib.error.URLError, OSError) as primary:
            if self.config.transport == 'auto' and shutil.which('curl'):
                try:
                    return self.request_with_curl(request)
                except urllib.error.HTTPError:
                    raise
                except Exception:
                    pass
            raise primary

    def should_use_curl_first(self):
        if self.config.transport == 'curl':
            return True
        if self.config.transport != 'auto':
            return False
        # The HKGAI WAF requests TLS renegotiation that this Windows
        # Python/OpenSSL combination stalls on. Native curl uses Schannel.
        return os.name == 'nt' and sys.version_info >= (3, 11) and bool(shutil.which('curl'))

    def request_with_curl(self, request):
        curl = shutil.which('curl')
        if not curl:
            raise CurlTransportError('curl is not installed')
        timeout = max(1.0, float(self.config.request_timeout_seconds))
        body_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix='collab-llm-', suffix='.json', delete=False) as handle:
                handle.write(request.data or b'')
                body_path = Path(handle.name)
            auth = request.get_header(self.config.auth_header)
            if not auth or '\r' in auth or '\n' in auth:
                raise CurlTransportError('Missing or invalid provider credential header')
            environment = os.environ.copy()
            environment['COLLAB_LLM_AUTH_VALUE'] = auth
            completed = subprocess.run(
                [
                    curl,
                    '--silent',
                    '--show-error',
                    '--max-redirs',
                    '0',
                    '--connect-timeout',
                    f'{min(15, timeout):g}',
                    '--max-time',
                    f'{timeout:g}',
                    '--max-filesize',
                    '2000000',
                    '--variable',
                    '%COLLAB_LLM_AUTH_VALUE',
                    '--expand-header',
                    f'{self.config.auth_header}: {{{{COLLAB_LLM_AUTH_VALUE}}}}',
                    '--header',
                    'Content-Type: application/json',
                    '--data-binary',
                    f'@{body_path.resolve().as_posix()}',
                    '--write-out',
                    '\\n%{http_code}',
                    request.full_url,
                ],
                capture_output=True,
                timeout=timeout + 5,
                env=environment,
                check=False,
            )
            if completed.returncode:
                raise CurlTransportError(f'curl exited with code {completed.returncode}')
            raw, separator, status_text = completed.stdout.rpartition(b'\n')
            if not separator:
                raise CurlTransportError('curl returned no HTTP status')
            try:
                status = int(status_text.strip())
            except ValueError:
                raise CurlTransportError('curl returned an invalid HTTP status') from None
            if status >= 400:
                raise urllib.error.HTTPError(
                    request.full_url,
                    status,
                    'provider HTTP error',
                    {},
                    io.BytesIO(raw),
                )
            if len(raw) > 2000000:
                raise CurlTransportError('Provider response exceeds limit')
            self.transport_used = 'curl'
            return json.loads(raw)
        except subprocess.TimeoutExpired:
            raise TimeoutError('curl provider request timed out') from None
        finally:
            if body_path is not None:
                body_path.unlink(missing_ok=True)

    def request_with_urllib(self, request):
        """Bound the whole request even if a server sends whitespace/keepalive bytes."""
        channel = queue.Queue(maxsize=1)

        def worker():
            try:
                with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                    raw = response.read(2000001)
                    if len(raw) > 2000000:
                        raise ValueError('Provider response exceeds limit')
                    value = json.loads(raw)
                channel.put((True, value))
            except Exception as exc:
                channel.put((False, exc))

        threading.Thread(target=worker, daemon=True).start()
        try:
            ok, value = channel.get(timeout=self.config.request_timeout_seconds)
        except queue.Empty:
            raise ModelError('LLM total request deadline exceeded; task preserved. Resume with a smaller action or inspect provider load.') from None
        if not ok:
            raise value
        self.transport_used = 'urllib'
        return value
