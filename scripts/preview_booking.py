"""Preview the selected booking delivery in Docker; never import generated code on the host."""
import argparse
import hashlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from collab.config import Config
from collab.store import Store, dump
from collab.tools import safe_path


def prepare(store, state, run_id):
    run = state['runs'][run_id]
    if run.get('private'):
        raise ValueError('Private tasks are not public previews')
    task = state['tasks'][run['root']]
    selected = task.get('submission', {}).get('artifacts', [])
    if not selected:
        raise ValueError('No submitted delivery is available yet')
    source = store.path / 'preview' / run_id / 'source'
    source.mkdir(parents=True, exist_ok=True)
    names = []
    for artifact_id in selected:
        artifact = state['artifacts'][artifact_id]
        data = (store.path / 'artifacts' / artifact['sha256']).read_bytes()
        if hashlib.sha256(data).hexdigest() != artifact['sha256']:
            raise ValueError('Artifact hash mismatch; stop preview')
        target = safe_path(source, artifact['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        names.append(artifact['path'])
    if not {'app.py', 'booking_core.py'}.issubset(names):
        raise ValueError('This helper requires the booking app delivery with app.py and booking_core.py')
    dump(source / 'preview_manifest.json', {'run': run_id, 'human_accepted': run['status'] == 'completed', 'files': names})
    # Preserve real manual booking records when previewing the next version in this group.
    data_dir = store.path / 'preview' / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt' and os.geteuid() == 0:
        data_dir.chmod(0o777)
    return source, data_dir


def command(image, source, data_dir, name, port):
    if not 1024 <= port <= 65535:
        raise ValueError('Choose a local port from 1024 to 65535')
    user = '65534:65534' if os.name == 'nt' or os.geteuid() == 0 else f'{os.geteuid()}:{os.getegid()}'
    # app binds loopback by default. Override only runtime settings inside the container,
    # preserving the exact reviewed source bytes and limiting the host port to loopback.
    launch = "import app; app.HOST='0.0.0.0'; app.PORT=8000; app.DB_PATH='/data/bookings.db'; app.main()"
    return ['docker', 'run', '--rm', '--name', name, '--init', '--network', 'bridge',
            '--memory', '512m', '--cpus', '1', '--pids-limit', '128', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--read-only', '--user', user,
            '--tmpfs', '/tmp:rw,nosuid,size=128m', '-p', f'127.0.0.1:{port}:8000',
            '--mount', f'type=bind,source={source.resolve()},target=/workspace,readonly',
            '--mount', f'type=bind,source={data_dir.resolve()},target=/data',
            '--workdir', '/workspace', image, 'python', '-u', '-c', launch]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', default='software')
    parser.add_argument('--run', help='Default: latest public run')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(argv)
    store = Store(BASE / 'runtime', args.group)
    state = store.load()
    public_runs = [r['id'] for r in state['runs'].values() if not r.get('private')]
    if not public_runs:
        raise ValueError('No public delivery exists')
    run_id = args.run or public_runs[-1]
    source, data_dir = prepare(store, state, run_id)
    print('Reviewed source snapshot: ' + str(source), flush=True)
    print('Persistent preview data: ' + str(data_dir), flush=True)
    if args.prepare_only:
        return 0
    config = Config.from_env()
    name = 'collab-preview-' + uuid.uuid4().hex[:12]
    argv = command(config.image, source, data_dir, name, args.port)
    print(f'Open http://127.0.0.1:{args.port} after the server starts. Keep this terminal open; Ctrl+C stops it.', flush=True)
    try:
        return subprocess.run(argv, shell=False).returncode
    except KeyboardInterrupt:
        return 0
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(f'Preview failed: {exc}')
        raise SystemExit(1)
