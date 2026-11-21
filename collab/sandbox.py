import os
import subprocess
import uuid


class Sandbox:
    def __init__(self, image):
        self.image = image

    def run(self, workspace, argv, timeout=60, network=False, credentials=False, stdin=None, cwd=None):
        if not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError('argv must be a non-empty list of strings')
        timeout = max(1, min(int(timeout), 120))
        workdir='/workspace'
        if cwd is not None and cwd!='.':
            from .tools import safe_path
            if not isinstance(cwd,str) or not cwd:
                raise ValueError('cwd must be a relative directory within the workspace')
            target=safe_path(workspace,cwd+'/cwd.txt').parent
            if not target.is_dir() or target.relative_to(workspace.resolve()).as_posix()!=cwd:
                raise ValueError('cwd must be an existing canonical workspace directory')
            workdir+='/'+cwd
        name = 'collab-' + uuid.uuid4().hex[:16]
        user = '65534:65534' if os.name == 'nt' or os.geteuid() == 0 else f'{os.geteuid()}:{os.getegid()}'
        command = ['docker', 'run', '--rm', '--name', name, '--init',
                   '--network', 'bridge' if network else 'none', '--memory', '512m',
                   '--cpus', '1', '--pids-limit', '128', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--read-only',
                   '--tmpfs', '/tmp:rw,nosuid,size=128m', '--user', user,
                   '--mount', f'type=bind,source={workspace.resolve()},target=/workspace',
                   '--workdir', workdir]
        if credentials:
            if not os.getenv('GITHUB_TOKEN'):
                raise ValueError('GITHUB_TOKEN is not configured locally')
            command += ['-e', 'GITHUB_TOKEN', '-e', 'GIT_CONFIG_COUNT=1',
                        '-e', 'GIT_CONFIG_KEY_0=credential.helper',
                        '-e', 'GIT_CONFIG_VALUE_0=!f() { echo username=x-access-token; echo password=$GITHUB_TOKEN; }; f']
        command += [self.image, *argv]
        if stdin is not None:
            if not isinstance(stdin, bytes) or len(stdin) > 64000:
                raise ValueError('Sandbox input must be at most 64 KB')
            command.insert(2, '-i')
        try:
            # Temporary disk file avoids unbounded subprocess stdout memory.
            import tempfile
            with tempfile.TemporaryFile() as output:
                result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=timeout,
                                        shell=False, **({'input': stdin} if stdin is not None else {}))
                output.seek(0)
                raw = output.read(32001)
                return {'exit_code': result.returncode, 'output': raw[:32000].decode('utf-8', errors='replace'),
                        'truncated': len(raw) > 32000}
        except subprocess.TimeoutExpired:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)
            return {'exit_code': 124, 'output': 'Sandbox execution timed out', 'truncated': False}
        except FileNotFoundError:
            raise RuntimeError('Docker is required for execution. No host execution fallback is allowed.') from None
