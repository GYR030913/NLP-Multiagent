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


def failure_context(root, output):
    """Bounded source excerpts for failed sandbox frames, never host execution.

    Tracebacks are untrusted: only the same workspace's allowed text paths may
    be opened, even if generated code prints a forged /workspace frame.
    """
    contexts=[]
    seen=set()
    frames=re.findall(r'File "/workspace/([^"\r\n]+)", line (\d{1,9})(?!\d)',output)
    for name, number in reversed(frames):
        line=int(number)
        if (name,line) in seen:
            continue
        seen.add((name,line))
        try:
            path=safe_path(root,name,True)
            if path.stat().st_size>256000:
                continue
            lines=path.read_text(encoding='utf-8').splitlines()
        except (ValueError,OSError):
            continue
        if not 1<=line<=len(lines):
            continue
        start=max(0,line-4)
        contexts.append({'path':name,'line':line,'start_line':start+1,
                         'content':'\n'.join(lines[start:line+2])[:1000],
                         'note':'Untrusted source excerpt from the executed workspace; not a successful check.'})
        if len(contexts)>=3:
            break
    return contexts


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
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'meta' and values.get('content'):
            key = values.get('property', values.get('name', ''))
            if key in {'article:published_time', 'date', 'DC.date', 'og:title'}:
                self.metadata[key] = values['content'][:500]
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
        from .tool_registry import REGISTRY
        return REGISTRY.get(name, {}).get('approval', False)

    def execute_for_task(self, state, task, name, args):
        from .context import tool_patterns
        from .tool_registry import validate
        validate(name, args, tool_patterns(task))
        from .recovery import checkpoint_access
        if not checkpoint_access(task,name,args):
            raise ValueError('Checkpoint repair allows one read of the conflicting path with explicit limit<=1500')
        previous = getattr(self.store, '_task_scope', None)
        self.store._task_scope = task
        try:
            from .recovery import public_source_call
            result = public_source_call(state,task,name,args,
                lambda:self.execute(state, task['owner'], name, args))
            if name == 'skill.use':
                ref = {k: result[k] for k in ('id', 'version', 'content_sha256')}
                old = next((r for r in task['skill_refs'] if r['id'] == ref['id']), None)
                if old and old != ref:
                    raise ValueError('Skill version is pinned for this task')
                if not old:
                    task['skill_refs'].append(ref)
            if name in {'artifact.import', 'artifact.import_bundle'}:
                if name == 'artifact.import':
                    item = state['artifacts'][args['id']]
                    task['imported_files'][args['path']] = item['sha256']
                else:
                    bundle = state['bundles'][args['bundle_id']]
                    for item in bundle['payload']['files']:
                        task['imported_files'][args['path'] + '/' + item['path']] = item['sha256']
            if name=='files.read' and task.get('output_checkpoint',{}).get('required'):
                task['output_checkpoint']['repair_read']=None
            return result
        finally:
            self.store._task_scope = previous

    def execute(self, state, agent, name, args):
        from .tool_registry import validate
        validate(name, args)
        root = self.store.workspace(agent)
        if name.startswith(('evidence.', 'data.', 'document.')) or name == 'web.fetch_document':
            task = getattr(self.store, '_task_scope', None)
            if not task:
                raise ValueError('Research/data tools require a current task scope')
            if name == 'web.fetch_document':
                from .documents import fetch_document
                return fetch_document(self.store, state, task, **args)
            if name == 'document.extract':
                from .documents import extract
                return extract(self.store, state, task, self.config, **args)
            if name.startswith('evidence.'):
                from . import research
                if name == 'evidence.list_claims':
                    return research.list_claims(state, task, args.get('include_history', False))
                return getattr(research, name.split('.',1)[1])(self.store, state, task, **args)
            from . import data
            operation = 'export_dataset' if name == 'data.export' else name.split('.',1)[1]
            return getattr(data, operation)(self.store, state, task, **args)
        if name.startswith('memory.'):
            from .memory import Memory
            memory = Memory(self.store, state)
            task = getattr(self.store, '_task_scope', None)
            if not task:
                raise ValueError('Memory tools require a current task scope')
            operation = name.split('.', 1)[1]
            if operation == 'working_set':
                from .working_set import annotate
                return annotate(self.store, state, task, **args)
            if operation == 'share':
                return memory.share(task, private_approval=bool(getattr(self, '_approved_memory_share', False)), **args)
            return getattr(memory, operation)(task=task, **args)
        if name.startswith('skill.'):
            from .skills import SkillRegistry
            registry = SkillRegistry()
            return registry.list(agent, args.get('capability')) if name == 'skill.list' else registry.get(args['id'], agent, args.get('version'))
        if name.endswith('_bundle'):
            from . import bundles
            if name == 'artifact.publish_bundle':
                scope = getattr(self.store, '_task_scope', None)
                required = scope.get('outputs', {}).get('required_paths', []) if scope else []
                combined = {**args, 'required_paths': sorted(set(args.get('required_paths', []) + required))}
                return bundles.publish(self.store, state, agent, root, combined)
            if name == 'artifact.validate_bundle':
                bundle = bundles.validate(self.store, state, args['bundle_id'])
                return {'ok': True, 'bundle_id': bundle['id'], 'sha256': bundle['sha256'], 'files': len(bundle['artifact_ids'])}
            if name == 'artifact.import_bundle':
                target, bundle = bundles.materialize(self.store, state, args['bundle_id'], root, args['path'])
                return {'bundle_id': bundle['id'], 'sha256': bundle['sha256'], 'path': args['path'], 'files': len(bundle['artifact_ids'])}
        if name == 'browser.verify':
            from .browser import verify
            return verify(self.store, state, self.config, args)
        if name.startswith('preview.'):
            from .preview import start, stop
            return start(self.store, state, self.config, args, owner=agent) if name == 'preview.start' else stop(self.store, state, args['lease_id'], owner=agent)
        if name == 'files.list':
            return [str(p.relative_to(root)).replace('\\', '/') for p in root.rglob('*')
                    if p.is_file() and not any(part.startswith('.') for part in p.relative_to(root).parts)][:500]
        if name == 'files.read':
            path = safe_path(root, args['path'], True)
            if path.stat().st_size > 256000:
                raise ValueError('File exceeds 256 KB; split it into smaller files')
            text = path.read_text(encoding='utf-8')
            if 'offset' in args or 'limit' in args:
                offset, limit = args.get('offset', 0), args.get('limit', 12000)
                if offset < 0 or not 1 <= limit <= 32000:
                    raise ValueError('Invalid read offset/limit')
                return {'path': args['path'], 'offset': offset, 'content': text[offset:offset + limit],
                        'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                        'truncated': offset + limit < len(text)}
            return text
        if name == 'files.write':
            path = safe_path(root, args['path'])
            content = args['content']
            if not isinstance(content, str) or len(content.encode('utf-8')) > 256000:
                raise ValueError('Write content must be text, at most 256 KB')
            before = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
            data = path.read_bytes()
            return {'path': args['path'], 'bytes': len(data), 'changed': before != data,
                    'sha256': hashlib.sha256(data).hexdigest()}
        if name == 'files.delete':
            safe_path(root, args['path'], True).unlink()
            return {'deleted': args['path']}
        if name == 'files.append':
            path = safe_path(root, args['path'], True)
            before = path.read_bytes()
            content = args['content']
            if not isinstance(content, str) or path.stat().st_size + len(content.encode('utf-8')) > 256000:
                raise ValueError('Appended file must remain text and at most 256 KB')
            with path.open('a', encoding='utf-8') as handle:
                handle.write(content)
            data = path.read_bytes()
            return {'path': args['path'], 'bytes': len(data), 'changed': before != data,
                    'sha256': hashlib.sha256(data).hexdigest()}
        if name == 'files.replace':
            path = safe_path(root, args['path'], True)
            before = path.read_bytes()
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
                    'changed': before != path.read_bytes(),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if name == 'code.run':
            result = self.sandbox.run(root, args['argv'], args.get('timeout', 60),
                                      **({'cwd':args['cwd']} if 'cwd' in args else {}))
            result['cwd']=args.get('cwd','.')
            if result.get('exit_code',0)!=0:
                contexts=failure_context(root,result.get('output',''))
                if contexts:
                    result['failure_context']=contexts
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
            from .public_http import get
            url = args['url']
            data, headers, final = get(url,1000000)
            content_type = headers.get_content_type()
            if content_type not in {'text/plain', 'text/html', 'application/json'}:
                raise ValueError('Only text, HTML and JSON sources are supported')
            text = data.decode(headers.get_content_charset() or 'utf-8', errors='replace')
            metadata = {}
            if content_type == 'text/html':
                parser = TextParser()
                parser.feed(text)
                text = '\n'.join(parser.parts)
                metadata = parser.metadata
            item = self.source(state, agent, url, metadata.get('og:title',''), text, 'fetched_text')
            item.update(published_date=metadata.get('article:published_time',metadata.get('date',metadata.get('DC.date'))),
                        date_metadata_origin='publisher HTML metadata, unverified' if metadata else None,
                        event_date=None, final_url=final)
            task = getattr(self.store, '_task_scope', None)
            if task:
                from .research import read_source
                return read_source(self.store, state, task, item['id'])
            return item
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
                'sha256': hashlib.sha256(text.encode()).hexdigest(),
                'body_verified': kind == 'fetched_text', 'characters': len(text)}
        scope = getattr(self.store, '_task_scope', None)
        if scope:
            item.update(private=bool(scope.get('private')),task=scope['id'])
        if kind == 'fetched_text':
            folder = self.store.path / 'sources'
            folder.mkdir(exist_ok=True)
            (folder / item['sha256']).write_bytes(text.encode())
            item.update(content_ref=item['sha256'], text=text[:12000], truncated=len(text)>12000)
        state['sources'][source_id] = item
        return item
