"""Explicit local preview leases: immutable source, separate data, bounded lifetime."""
import json
import os
import subprocess
import time
import uuid
import hashlib
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing
from .bundles import materialize
from .browser import validate_plan
from .store import dump, ident


def snapshot_data(store, state, data_key, role):
    """Explicit operator handoff; generated code never mounts the live database."""
    ident(data_key)
    if role not in state['roles']:
        raise ValueError('Unknown recipient role')
    first = state['scenario'].get('tasks', [{}])[0].get('text')
    if not any(r['status'] == 'completed' and not r.get('private') and
               state['tasks'][r['root']]['description'] == first for r in state['runs'].values()):
        raise ValueError('Accept task 1 before sharing its preview data with task 2')
    data_root = store.path / 'preview-data'
    source = data_root / data_key / 'bookings.db'
    if source.is_symlink() or not source.resolve().is_relative_to(data_root.resolve()) or not source.is_file():
        raise ValueError('Preview database unavailable or outside the managed data directory')
    snapshots = store.path / 'data-snapshots'
    snapshots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=snapshots) as work:
        temp = Path(work) / 'snapshot.sqlite3'
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            page_size = original.execute('PRAGMA page_size').fetchone()[0]
            def check_size(status, remaining, total):
                if total * page_size > 20 * 1024 * 1024:
                    raise ValueError('Preview data snapshot exceeds 20 MB')
            with closing(sqlite3.connect(temp)) as target:
                original.backup(target, pages=256, progress=check_size)
        data = temp.read_bytes()
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('Preview data snapshot exceeds 20 MB')
    sha = hashlib.sha256(data).hexdigest()
    archived = snapshots / (sha + '.sqlite3')
    if not archived.exists(): archived.write_bytes(data)
    relative = 'inputs/bookings-' + sha[:16] + '.sqlite3'
    root = store.workspace(role)
    from .tools import safe_path
    safe_path(root, 'inputs/manifest.json')
    destination = root / relative
    destination.parent.mkdir(exist_ok=True)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError('Snapshot destination has been modified; choose a clean role workspace')
    if not destination.exists(): destination.write_bytes(data)
    destination.chmod(0o444)
    result = {'data_key': data_key, 'recipient': role, 'path': relative, 'sha256': sha,
              'bytes': len(data), 'mode': 'readonly-snapshot', 'source': 'operator-approved preview data',
              'instructions': 'Open with SQLite URI mode=ro, verify SHA256; do not publish raw personal rows. Live database is not mounted.'}
    dump(root / ('inputs/bookings-' + sha[:16] + '.json'), result)
    store.event(state, 'human_message', sender='operator', to=role, text='Task 1 data snapshot explicitly shared: ' + json.dumps(result))
    return result


def docker(argv, check=True):
    result = subprocess.run(['docker', *argv], capture_output=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError('Docker preview operation failed: ' + result.stderr.decode(errors='replace')[:800])
    return result


def stop(store, state, lease_id, owner='operator'):
    lease = state.get('previews', {}).get(lease_id)
    if not lease:
        raise ValueError('Unknown preview lease')
    if owner != 'operator' and lease['owner'] != owner:
        raise ValueError('Cannot stop another role preview')
    if not lease['container'].startswith('collab-preview-') or not lease['network'].startswith('collab-preview-net-'):
        raise ValueError('Invalid preview resource identity')
    docker(['rm', '-f', lease['container']], check=False)
    result = docker(['network', 'rm', lease['network']], check=False)
    lease.update(status='stopped', cleanup_pending=result.returncode != 0)
    return {'lease_id': lease_id, 'status': lease['status'], 'cleanup_pending': lease['cleanup_pending']}


def start(store, state, config, args, owner='operator'):
    port, app_port = args.get('port', 8000), args.get('app_port', 8000)
    seconds = args.get('lease_seconds', 1800)
    data_key = ident(args.get('data_key', 'default'))
    validate_plan({'argv': args['argv'], 'checks': [{'action': 'expect_visible', 'selector': 'body'}], 'port': app_port})
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValueError('Invalid host port')
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 3600:
        raise ValueError('Preview lease must be 1-3600 seconds')
    for lease in state.setdefault('previews', {}).values():
        if lease['status'] == 'active' and lease['expires_at'] <= time.time():
            stop(store, state, lease['id'])
    if sum(l['status'] == 'active' for l in state['previews'].values()) >= 3:
        raise ValueError('Stop an existing preview before starting another')
    if any(l['status'] == 'active' and l.get('data_key', 'default') == data_key for l in state['previews'].values()):
        raise ValueError('Stop the existing preview before reusing its writable data')
    lease_id = uuid.uuid4().hex
    source, bundle = materialize(store, state, args['bundle_id'], store.path / 'preview-leases', lease_id + '/source')
    root = source.parent
    launch, data = root / 'launch', store.path / 'preview-data' / data_key
    launch.mkdir(exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        data.chmod(0o777)
    expires = time.time() + seconds
    dump(launch / 'spec.json', {'argv': args['argv'], 'app_port': app_port, 'expires_at': expires})
    name, network = 'collab-preview-' + lease_id, 'collab-preview-net-' + lease_id
    # Docker Desktop cannot reliably publish ports on an internal network.
    # The trusted supervisor adds a fail-closed outbound seccomp filter to the
    # generated application, while the ordinary bridge permits incoming localhost.
    docker(['network', 'create', network])
    try:
        docker(['run', '--detach', '--rm', '--name', name, '--init', '--network', network,
                '--memory', '512m', '--cpus', '1', '--pids-limit', '128', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--read-only', '--user', '65534:65534',
                '--tmpfs', '/tmp:rw,nosuid,size=256m', '-p', f'127.0.0.1:{port}:{app_port}',
                '--mount', f'type=bind,source={source.resolve()},target=/workspace,readonly',
                '--mount', f'type=bind,source={launch.resolve()},target=/launch,readonly',
                '--mount', f'type=bind,source={data.resolve()},target=/data',
                config.browser_image, 'python', '/opt/collab/preview_worker.py'])
    except Exception:
        docker(['rm', '-f', name], check=False)
        docker(['network', 'rm', network], check=False)
        raise
    lease = {'id': lease_id, 'owner': owner, 'bundle_id': bundle['id'], 'sha256': bundle['sha256'],
             'url': f'http://127.0.0.1:{port}', 'container': name, 'network': network,
             'expires_at': expires, 'status': 'active', 'cleanup_pending': False, 'data_key': data_key}
    state['previews'][lease_id] = lease
    return lease
