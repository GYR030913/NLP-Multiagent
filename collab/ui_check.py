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


STRUCTURAL_LIMITS = {'html': 1, 'body': 1, 'head': 1}


def structural_warnings(source, filename):
    """Report document-shell duplication that a JavaScript parser cannot see.

    Chunked generation can silently emit two complete documents, or two
    copies of one script. The JavaScript stays syntactically valid, so only an
    explicit structural check can catch the lost or duplicated content.
    """
    if Path(filename).suffix.lower() != '.html':
        return []
    class ShellTags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.counts = dict.fromkeys(STRUCTURAL_LIMITS, 0)

        def handle_starttag(self, tag, attrs):
            if tag in self.counts:
                self.counts[tag] += 1

    parser = ShellTags()
    parser.feed(source)
    warnings = []
    for tag, limit in STRUCTURAL_LIMITS.items():
        count = parser.counts[tag]
        if count > limit:
            warnings.append(f'HTML contains {count} <{tag}> elements; expected {limit}. '
                            'The document may contain duplicated or concatenated content.')
    return warnings


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
        return {'exit_code': 0, 'checked_scripts': 0,
                'validation_warnings': structural_warnings(source, filename)}
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
            'checked_scripts': len(checks), 'checks': checks,
            'validation_warnings': structural_warnings(source, filename)}


def require_valid(source, filename):
    result = check_source(source, filename)
    if result['exit_code']:
        failure = next(c for c in result['checks'] if c['exit_code'])
        raise ValueError('JavaScript syntax check failed for ' + filename + ': ' + failure['output'])
    warnings = result.get('validation_warnings') or []
    if warnings:
        raise ValueError('Structural validation failed for ' + filename + ': ' + '; '.join(warnings))
    return result
