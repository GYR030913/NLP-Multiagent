import hashlib
import ipaddress
import json
import re
import socket
import http.client
import urllib.request
from urllib.parse import urlsplit
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from .store import now
from .sandbox import Sandbox
from .ui_check import check_source, require_valid


TEXT_EXTENSIONS = {'.md', '.txt', '.py', '.json', '.jsonl', '.toml', '.yaml', '.yml', '.csv', '.html', '.css', '.js', '.sql', '.ini', '.cfg'}


def safe_path(root, name, must_exist=False):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Use a relative POSIX path')
    parts = PurePosixPath(name).parts
    if name.startswith('/') or any(p in {'.', '..', '.git'} or p.startswith('.') for p in parts):
        raise ValueError('Traversal and hidden files are prohibited')
    root = root.resolve()
    target = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor /= part
        if cursor.is_symlink() or (hasattr(cursor, 'is_junction') and cursor.is_junction()):
            raise ValueError('Links and junctions are prohibited')
    resolved = target.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError('Path escapes agent workspace')
    if must_exist and not resolved.is_file():
        raise ValueError('File does not exist')
    if resolved.suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError('Unsupported format; documents must be Markdown/text, HTML only for executable web artifacts')
    return resolved


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in {None, 443}:
        raise ValueError('Only public HTTPS URLs on port 443 are permitted')
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Private/local network access is prohibited')
    return url


class PublicHTTPSConnection(http.client.HTTPSConnection):
    """Pin the validated address used by the socket, retaining TLS hostname verification."""
    def connect(self):
        original = self._create_connection

        def connect_public(address, timeout, source_address=None):
            host, port = address
            rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not rows or any(not ipaddress.ip_address(row[4][0]).is_global for row in rows):
                raise ValueError('Private/local network access is prohibited')
            return original((rows[0][4][0], port), timeout, source_address)

        self._create_connection = connect_public
        super().connect()


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PublicHTTPSConnection, request, context=self._context)


PUSH_SCRIPT = r'''
import pathlib, shutil, subprocess, sys, tempfile
source = pathlib.Path('/workspace/.git')
with tempfile.TemporaryDirectory(prefix='collab-push-') as temp:
    destination = pathlib.Path(temp)
    subprocess.run(['git', 'init', '--bare', temp], check=True, stdout=subprocess.DEVNULL)
    for name in ['objects', 'refs']:
        shutil.copytree(source / name, destination / name, dirs_exist_ok=True)
    for name in ['HEAD', 'packed-refs']:
        if (source / name).is_file():
            shutil.copyfile(source / name, destination / name)
    # Do not read source config/hooks, which generated code could have modified.
    result = subprocess.run(['git', '-C', temp, '-c', 'core.hooksPath=/tmp/no-hooks',
                             'push', sys.argv[1], 'HEAD:refs/heads/' + sys.argv[2]])
    sys.exit(result.returncode)
'''


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript'}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript'}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


TOOL_HELP = '''
Use {"type":"tool","name":NAME,"args":{...}}. Available tools:
files.list {} ; files.read {path}; files.write {path,content}; files.delete {path} [human approval].
files.append {path,content} appends to an existing text file; split large files into smaller actions.
files.replace {path,old,new,expected_count:1} performs a small exact edit. Prefer it to rewriting large files.
code.run {argv:["python","-m","unittest","discover"],timeout:60} [isolated Docker; no network].
ui.check {path:"app.py or page.html or client.js"} [static Node --check; does not execute target code].
web.search {query,topic?:"general|news|finance",time_range?:"day|week|month|year",include_domains?:["hkust.edu.hk"]} [Tavily, keyless if no search key]; web.fetch {url} [public HTTPS, text/HTML only].
artifact.publish {path,description} -> immutable artifact ID; artifact.import {id,path} -> own workspace.
git.init {}; git.status {}; git.diff {}; git.commit {message} [own container repository].
git.push {url:"https://github.com/OWNER/REPO.git",branch:"main"} [human approval, network, optional GITHUB_TOKEN].
Do not attempt to execute host commands. Use Python standard library or dependencies already in sandbox.
Code tools return exit_code; nonzero is failure, not proof of success. Transfer code using published files.
Keep each generated file chunk below roughly 12000 characters to avoid truncated JSON responses.
For browser artifacts, validate actual JavaScript syntax, not only HTML text and HTTP APIs. Python embedding
can consume JavaScript backslash escapes: use raw strings or separate JS files. Publication rejects invalid JS.
'''


class Tools:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.sandbox = Sandbox(config.image)

    def needs_approval(self, name):
        return name in {'files.delete', 'git.push'}

    def execute(self, state, agent, name, args):
        root = self.store.workspace(agent)
        if name == 'files.list':
            return [str(p.relative_to(root)).replace('\\', '/') for p in root.rglob('*')
                    if p.is_file() and not any(part.startswith('.') for part in p.relative_to(root).parts)][:500]
        if name == 'files.read':
            path = safe_path(root, args['path'], True)
            if path.stat().st_size > 256000:
                raise ValueError('File exceeds 256 KB; split it into smaller files')
            return path.read_text(encoding='utf-8')
        if name == 'files.write':
            path = safe_path(root, args['path'])
            content = args['content']
            if not isinstance(content, str) or len(content.encode('utf-8')) > 256000:
                raise ValueError('Write content must be text, at most 256 KB')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
            return {'path': args['path'], 'bytes': path.stat().st_size}
        if name == 'files.delete':
            safe_path(root, args['path'], True).unlink()
            return {'deleted': args['path']}
        if name == 'files.append':
            path = safe_path(root, args['path'], True)
            content = args['content']
            if not isinstance(content, str) or path.stat().st_size + len(content.encode('utf-8')) > 256000:
                raise ValueError('Appended file must remain text and at most 256 KB')
            with path.open('a', encoding='utf-8') as handle:
                handle.write(content)
            return {'path': args['path'], 'bytes': path.stat().st_size}
        if name == 'files.replace':
            path = safe_path(root, args['path'], True)
            old, new = args['old'], args['new']
            expected = args.get('expected_count', 1)
            if not isinstance(old, str) or not old or not isinstance(new, str) or not isinstance(expected, int) or expected < 1:
                raise ValueError('Replacement requires nonempty old text, new text and a positive expected_count')
            source = path.read_text(encoding='utf-8')
            actual = source.count(old)
            if actual != expected:
                raise ValueError(f'Expected {expected} exact matches, found {actual}; read the relevant file before retrying')
            changed = source.replace(old, new)
            if len(changed.encode('utf-8')) > 256000:
                raise ValueError('Updated file exceeds 256 KB')
            path.write_text(changed, encoding='utf-8')
            return {'path': args['path'], 'replacements': actual, 'bytes': path.stat().st_size,
                    'sha256': hashlib.sha256(changed.encode('utf-8')).hexdigest()}
        if name == 'code.run':
            result = self.sandbox.run(root, args['argv'], args.get('timeout', 60))
            if re.search(r'Ran\s+0\s+tests?\b', result['output']):
                result['validation_warnings'] = ['Zero tests discovered. Fix discovery or run explicit tests before approval.']
            return result
        if name == 'ui.check':
            path = safe_path(root, args['path'], True)
            return check_source(path.read_text(encoding='utf-8'), path.name)
        if name.startswith('git.'):
            base = ['git', '-c', 'safe.directory=/workspace', '-c', 'core.hooksPath=/tmp/no-hooks',
                    '-c', 'user.name=Course Agent', '-c', 'user.email=agent@localhost']
            if name == 'git.init':
                return self.sandbox.run(root, base + ['init', '-b', 'main'])
            if name == 'git.status':
                return self.sandbox.run(root, base + ['status', '--short'])
            if name == 'git.diff':
                return self.sandbox.run(root, base + ['diff', 'HEAD'])
            if name == 'git.commit':
                added = self.sandbox.run(root, base + ['add', '--all'])
                if added['exit_code']:
                    return added
                return self.sandbox.run(root, base + ['commit', '-m', args['message']])
            if name == 'git.push':
                url = args['url']
                branch = args.get('branch', 'main')
                if not re.fullmatch(r'https://github\.com/[\w.-]+/[\w.-]+\.git', url):
                    raise ValueError('Push requires a plain GitHub HTTPS repository URL')
                if not re.fullmatch(r'[A-Za-z0-9_-]+', branch):
                    raise ValueError('Invalid branch name')
                import os
                return self.sandbox.run(root, ['python', '-c', PUSH_SCRIPT, url, branch],
                                        timeout=120, network=True, credentials=bool(os.getenv('GITHUB_TOKEN')))
        if name == 'artifact.publish':
            path = safe_path(root, args['path'], True)
            data = path.read_bytes()
            if len(data) > 256000:
                raise ValueError('Artifact exceeds 256 KB')
            require_valid(data.decode('utf-8'), args['path'])
            digest = hashlib.sha256(data).hexdigest()
            artifact_id = 'a' + str(len(state['artifacts']) + 1)
            blob = self.store.path / 'artifacts' / digest
            blob.parent.mkdir(exist_ok=True)
            blob.write_bytes(data)
            item = {'id': artifact_id, 'owner': agent, 'path': args['path'], 'sha256': digest,
                    'description': args.get('description', ''), 'time': now()}
            state['artifacts'][artifact_id] = item
            return item
        if name == 'artifact.import':
            item = state['artifacts'][args['id']]
            data = (self.store.path / 'artifacts' / item['sha256']).read_bytes()
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Artifact integrity check failed')
            path = safe_path(root, args['path'])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return {'imported': args['id'], 'path': args['path']}
        if name == 'web.search':
            if not isinstance(args.get('query'), str) or not 1 <= len(args['query']) <= 1000:
                raise ValueError('Search query must contain 1-1000 characters')
            payload = {'query': args['query'], 'max_results': 5, 'include_published_date': True}
            if 'topic' in args:
                if args['topic'] not in {'general', 'news', 'finance'}:
                    raise ValueError('Invalid search topic')
                payload['topic'] = args['topic']
            if 'time_range' in args:
                if args['time_range'] not in {'day', 'week', 'month', 'year'}:
                    raise ValueError('Invalid search time range')
                payload['time_range'] = args['time_range']
            if 'include_domains' in args:
                domains = args['include_domains']
                if not isinstance(domains, list) or len(domains) > 10 or any(not isinstance(d, str) or not re.fullmatch(r'[A-Za-z0-9.-]+', d) for d in domains):
                    raise ValueError('include_domains must be a list of plain domain names')
                payload['include_domains'] = domains
            request = urllib.request.Request('https://api.tavily.com/search',
                    data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json'})
            if self.config.search_key:
                request.add_unredirected_header('Authorization', 'Bearer ' + self.config.search_key)
            else:
                request.add_header('X-Tavily-Access-Mode', 'keyless')
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            if not isinstance(result.get('results'), list):
                raise ValueError('Search unavailable or rate-limited; configure TAVILY_API_KEY or retry later. Do not fabricate results.')
            sources = []
            for row in result.get('results', []):
                item = self.source(state, agent, row['url'], row.get('title', ''),
                                   row.get('content', '')[:10000], 'search_snippet')
                item.update(published_date=row.get('published_date'), query=args['query'])
                sources.append(item)
            return sources
        if name == 'web.fetch':
            url = public_url(args['url'])
            request = urllib.request.Request(url, headers={'User-Agent': 'CampusCollab/0.1 (academic research)'})
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), CheckedRedirect(), PublicHTTPSHandler()).open(request, timeout=30) as response:
                content_type = response.headers.get_content_type()
                if content_type not in {'text/plain', 'text/html', 'application/json'}:
                    raise ValueError('Only text, HTML and JSON sources are supported')
                data = response.read(1000001)
                if len(data) > 1000000:
                    raise ValueError('Source exceeds 1 MB; find a text summary')
                text = data.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
            if content_type == 'text/html':
                parser = TextParser()
                parser.feed(text)
                text = '\n'.join(parser.parts)
            return self.source(state, agent, url, '', text[:24000], 'fetched_text')
        raise ValueError('Unknown tool: ' + name)

    def validate_artifacts(self, state, artifacts):
        for artifact_id in artifacts:
            item = state['artifacts'][artifact_id]
            path = self.store.path / 'artifacts' / item['sha256']
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Artifact integrity check failed')
            require_valid(data.decode('utf-8'), item['path'])

    def source(self, state, agent, url, title, text, kind):
        source_id = 's' + str(len(state['sources']) + 1)
        item = {'id': source_id, 'agent': agent, 'url': url, 'title': title,
                'retrieved_at': now(), 'kind': kind, 'text': text,
                'sha256': hashlib.sha256(text.encode()).hexdigest()}
        state['sources'][source_id] = item
        return item
