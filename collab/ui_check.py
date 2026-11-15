"""Static JavaScript compilation only: generated code is never executed on the host."""
import ast
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.current = None
        self.module = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key, value in attrs.items():
            if key.startswith('on') and value:
                self.chunks.append(('function handler(event) {\n' + value + '\n}', False))
        if tag == 'script' and not attrs.get('src'):
            kind = attrs.get('type', '').lower()
            if kind in {'', 'module', 'text/javascript', 'application/javascript'}:
                self.current = []
                self.module = kind == 'module'

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.chunks.append((''.join(self.current), self.module))
            self.current = None


def embedded_html(source):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'INDEX_HTML' for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                return None
            return value if isinstance(value, str) else None
    return None


def check_source(source, filename):
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.py', '.html', '.js', '.mjs'}:
        return {'exit_code': 0, 'checked_scripts': 0}
    if suffix == '.py':
        source = embedded_html(source)
        if source is None:
            return {'exit_code': 0, 'checked_scripts': 0}
    if suffix in {'.js', '.mjs'}:
        chunks = [(source, suffix == '.mjs')]
    else:
        parser = Scripts()
        parser.feed(source)
        chunks = parser.chunks
    if not chunks:
        return {'exit_code': 0, 'checked_scripts': 0}
    node = shutil.which('node')
    if not node:
        raise ValueError('Node.js is required for static JavaScript syntax checks; install Node.js and reopen the terminal.')
    checks = []
    with tempfile.TemporaryDirectory(prefix='collab-js-check-') as temp:
        for index, (script, module) in enumerate(chunks, 1):
            path = Path(temp) / (f'script{index}' + ('.mjs' if module else '.js'))
            path.write_text(script, encoding='utf-8')
            # --check parses but does not execute scripts, imports, or fetch calls.
            result = subprocess.run([node, '--check', str(path)], capture_output=True, timeout=10, shell=False)
            checks.append({'script': index, 'exit_code': result.returncode,
                           'output': result.stderr.decode('utf-8', errors='replace')[:4000]})
    return {'exit_code': 0 if all(c['exit_code'] == 0 for c in checks) else 1,
            'checked_scripts': len(checks), 'checks': checks}


def require_valid(source, filename):
    result = check_source(source, filename)
    if result['exit_code']:
        failure = next(c for c in result['checks'] if c['exit_code'])
        raise ValueError('JavaScript syntax check failed for ' + filename + ': ' + failure['output'])
    return result
