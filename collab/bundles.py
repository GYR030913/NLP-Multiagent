"""Atomic manifests over immutable text artifacts; no ZIP or shared workspace."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from .store import dump, now


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def publish(store, state, agent, root, args):
    from .tools import safe_path
    from .ui_check import require_valid
    paths = args['paths']
    if not 1 <= len(paths) <= 100 or any(not isinstance(p, str) for p in paths) or len(set(paths)) != len(paths):
        raise ValueError('Bundle needs 1-100 unique relative text paths')
    contents, files = {}, []
    total = 0
    for name in paths:
        path = safe_path(root, name, True)
        if path.relative_to(root.resolve()).as_posix() != name:
            raise ValueError('Use canonical relative POSIX paths')
        data = path.read_bytes()
        total += len(data)
        if len(data) > 256000 or total > 10 * 1024 * 1024:
            raise ValueError('Bundle file/total size exceeds limit')
        text = data.decode('utf-8')
        for key in ('LLM_API_KEY', 'TAVILY_API_KEY', 'GITHUB_TOKEN'):
            secret = os.getenv(key, '')
            if secret and secret in text:
                raise ValueError('Refusing to publish credentials')
        if '-----BEGIN PRIVATE KEY-----' in text:
            raise ValueError('Refusing to publish a private key')
        require_valid(text, name)
        sha = hashlib.sha256(data).hexdigest()
        contents[sha] = data
        files.append({'path': name, 'sha256': sha, 'bytes': len(data)})
    entrypoints = args.get('entrypoints', [])
    required = args.get('required_paths', [])
    if any(not isinstance(p, str) or p not in paths for p in entrypoints + required):
        raise ValueError('Bundle entrypoints/required_paths must be present in paths')
    payload = {'files': sorted(files, key=lambda f: f['path']), 'entrypoints': entrypoints,
               'required_paths': required, 'data_contract': args.get('data_contract', {}),
               'version': args.get('version', 'draft')}
    if len(json.dumps(payload).encode()) > 64000:
        raise ValueError('Bundle manifest exceeds limit')
    sha = digest(payload)
    bundles = state.setdefault('bundles', {})
    key = args.get('idempotency_key')
    if key:
        for old in bundles.values():
            if old['owner'] == agent and old.get('idempotency_key') == key:
                if old['sha256'] != sha:
                    raise ValueError('Idempotency key already used for another bundle')
                validate(store, state, old['id'])
                return old
    artifacts = {}
    for index, item in enumerate(payload['files'], len(state['artifacts']) + 1):
        artifact_id = 'a' + str(index)
        artifacts[artifact_id] = {'id': artifact_id, 'owner': agent, **item,
                                  'description': args.get('description', ''), 'time': now()}
        item['artifact_id'] = artifact_id
    # artifact IDs are references, not part of the content identity.
    manifest = {'id': 'b' + str(len(bundles) + 1), 'owner': agent, 'sha256': sha,
                'payload': payload, 'artifact_ids': list(artifacts), 'time': now(),
                'description': args.get('description', ''), 'idempotency_key': key}
    for content_sha, data in contents.items():
        path = store.path / 'artifacts' / content_sha
        path.parent.mkdir(exist_ok=True)
        if path.exists() and path.read_bytes() != data:
            raise ValueError('Artifact content collision/corruption')
        path.write_bytes(data)
    dump(store.path / 'bundles' / (manifest['id'] + '.json'), manifest)
    # One state commit exposes all references together. Orphaned blobs are harmless.
    state['artifacts'].update(artifacts)
    bundles[manifest['id']] = manifest
    return manifest


def validate(store, state, bundle_id):
    from .tools import safe_path
    from .ui_check import require_valid
    manifest = state.get('bundles', {}).get(bundle_id)
    if not manifest:
        raise ValueError('Unknown bundle')
    payload = json.loads(json.dumps(manifest['payload']))
    for item in payload['files']:
        item.pop('artifact_id', None)
    if digest(payload) != manifest['sha256']:
        raise ValueError('Bundle manifest integrity check failed')
    seen = set()
    for item in manifest['payload']['files']:
        safe_path(store.path / 'validation', item['path'])
        if item['path'] in seen:
            raise ValueError('Duplicate bundle path')
        seen.add(item['path'])
        artifact = state['artifacts'].get(item['artifact_id'])
        if not artifact or artifact['path'] != item['path'] or artifact['sha256'] != item['sha256']:
            raise ValueError('Bundle artifact reference mismatch')
        data = (store.path / 'artifacts' / item['sha256']).read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Bundle artifact integrity check failed')
        require_valid(data.decode('utf-8'), item['path'])
    if not set(payload['entrypoints'] + payload['required_paths']).issubset(seen):
        raise ValueError('Required bundle files missing')
    return manifest


def materialize(store, state, bundle_id, root, prefix):
    from .tools import safe_path
    root = root.resolve()
    target = safe_path(root, prefix + '/bundle_manifest.json').parent
    if not target.is_relative_to(root) or target == root:
        raise ValueError('Bundle destination must be a new relative subdirectory')
    manifest = validate(store, state, bundle_id)
    if target.exists():
        actual = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
        expected = {f['path'] for f in manifest['payload']['files']}
        if actual != expected:
            raise ValueError('Destination contains a different delivery; choose a clean path')
        for item in manifest['payload']['files']:
            path = safe_path(target, item['path'], True)
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Destination differs; refusing to overwrite existing work')
        return target, manifest
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='bundle-stage-', dir=root))
    try:
        for item in manifest['payload']['files']:
            dest = safe_path(stage, item['path'])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((store.path / 'artifacts' / item['sha256']).read_bytes())
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(5):
            try:
                stage.replace(target)
                break
            except PermissionError:
                # Windows antivirus/indexers can briefly hold new source files.
                # Never fall back to a partially visible recursive copy.
                if attempt == 4 or target.exists():
                    raise
                time.sleep(0.1 * (attempt + 1))
    finally:
        if stage.exists() and stage.resolve().is_relative_to(root):
            shutil.rmtree(stage)
    return target, manifest
