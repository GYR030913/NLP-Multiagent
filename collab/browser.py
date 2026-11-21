"""Verify immutable bundles in a dedicated, offline browser sandbox."""
import json
import uuid
from .bundles import materialize
from .sandbox import Sandbox

CHECKS = {
    'goto': {'path': str}, 'fill': {'selector': str, 'value': str},
    'click': {'selector': str}, 'select': {'selector': str, 'value': str},
    'press': {'selector': str, 'key': str}, 'expect_text': {'selector': str, 'text': str},
    'expect_value': {'selector': str, 'value': str}, 'expect_visible': {'selector': str},
    'expect_count': {'selector': str, 'count': int},
}


def validate_plan(args):
    argv = args['argv']
    if not argv or len(argv) > 30 or any(not isinstance(a, str) or '\x00' in a or len(a) > 4000 for a in argv):
        raise ValueError('argv must be a bounded list of strings (no shell)')
    port = args.get('port', 8000)
    timeout = args.get('timeout', 90)
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValueError('Invalid container port')
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
        raise ValueError('Browser timeout must be 1-120 seconds')
    if not isinstance(args['checks'], list) or not 1 <= len(args['checks']) <= 50:
        raise ValueError('Browser verification needs 1-50 declarative checks')
    for check in args['checks']:
        if not isinstance(check, dict) or check.get('action') not in CHECKS:
            raise ValueError('Unknown browser check action; arbitrary JavaScript is not allowed')
        fields = CHECKS[check['action']]
        if set(check) != set(fields) | {'action'}:
            raise ValueError('Missing or unknown browser check fields')
        for key, kind in fields.items():
            if not isinstance(check[key], kind) or (kind is int and isinstance(check[key], bool)):
                raise ValueError('Invalid browser check parameter')
            if kind is str and len(check[key]) > 4000:
                raise ValueError('Browser check parameter exceeds limit')
        if check['action'] == 'goto' and (not check['path'].startswith('/') or check['path'].startswith('//') or '\\' in check['path']):
            raise ValueError('Navigation must use a relative local path')
        if check['action'] == 'press' and check['key'] not in {'Enter', 'Tab', 'Escape', 'ArrowLeft', 'ArrowRight', 'PageDown', 'PageUp', 'Space'}:
            raise ValueError('Unsupported navigation key')
    for item in args.get('expected_http_errors', []):
        if not isinstance(item, dict) or set(item) != {'path', 'status'} or not isinstance(item['path'], str) or not item['path'].startswith('/') or item['path'].startswith('//') or not isinstance(item['status'], int) or not 400 <= item['status'] <= 599:
            raise ValueError('Expected HTTP errors need exact local path and 4xx/5xx status')
    if len(json.dumps(args).encode()) > 64000:
        raise ValueError('Browser plan exceeds limit')


def verify(store, state, config, args):
    validate_plan(args)
    source, bundle = materialize(store, state, args['bundle_id'], store.path / 'browser', 'source-' + uuid.uuid4().hex)
    spec = {k: args[k] for k in ('argv', 'checks')}
    spec['port'] = args.get('port', 8000)
    spec['timeout'] = args.get('timeout', 90)
    spec['expected_http_errors'] = args.get('expected_http_errors', [])
    result = Sandbox(config.browser_image).run(source, ['python', '/opt/collab/browser_worker.py'],
             timeout=min(120, spec['timeout'] + 5), stdin=json.dumps(spec).encode())
    result.update(bundle_id=bundle['id'], subject_digest=bundle['sha256'])
    if result['exit_code'] == 0:
        try:
            receipt = json.loads(result['output'])
            if receipt.get('passed') is not True or len(receipt.get('checks', [])) != len(spec['checks']):
                raise ValueError('Incomplete browser receipt')
            result['checks'] = receipt['checks']
        except (ValueError, TypeError):
            result['exit_code'] = 1
            result['validation_warnings'] = ['Browser did not return a complete passing receipt']
    return result
