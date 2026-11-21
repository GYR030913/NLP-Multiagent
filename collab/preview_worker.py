"""Lease-bounded application supervisor for local human preview."""
import json
import os
import shutil
import subprocess
import tempfile
import time


def deny_outbound():
    """Stack a connect/send restriction on Docker's default seccomp filter.

    Published incoming TCP still works; the generated application cannot initiate
    connections. Fail closed on unsupported architectures/filter installation.
    """
    import ctypes
    import platform
    machine = platform.machine()
    if machine == 'x86_64':
        arch, connect, sendto, sendmsg, sendmmsg = 0xC000003E, 42, 44, 46, 307
    elif machine == 'aarch64':
        arch, connect, sendto, sendmsg, sendmmsg = 0xC00000B7, 203, 206, 211, 269
    else:
        raise RuntimeError('Unsupported preview architecture')
    class Filter(ctypes.Structure):
        _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte), ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint)]
    class Program(ctypes.Structure):
        _fields_ = [('len', ctypes.c_ushort), ('filter', ctypes.POINTER(Filter))]
    ops = [(0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x80000000),
           (0x20, 0, 0, 0), (0x54, 0, 0, 0xBFFFFFFF)]
    for number in (connect, sendmsg, sendmmsg, 425, 426, 427):
        ops += [(0x15, 0, 1, number), (0x06, 0, 0, 0x00050001)]
    # sendto(NULL) is ordinary output on an already accepted TCP connection.
    ops += [(0x15, 0, 5, sendto), (0x20, 0, 0, 48), (0x15, 0, 2, 0),
            (0x20, 0, 0, 52), (0x15, 1, 0, 0), (0x06, 0, 0, 0x00050001), (0x06, 0, 0, 0x7FFF0000)]
    instructions = (Filter * len(ops))(*(Filter(*op) for op in ops))
    program = Program(len(ops), instructions)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise RuntimeError('Cannot install preview outbound network restriction')


def main():
    with open('/launch/spec.json', encoding='utf-8') as handle:
        spec = json.load(handle)
    with tempfile.TemporaryDirectory(prefix='preview-', dir='/tmp') as work:
        shutil.copytree('/workspace', work, dirs_exist_ok=True)
        env = {**os.environ, 'HOST': '0.0.0.0', 'PORT': str(spec['app_port']),
               'DB_PATH': '/data/bookings.db', 'PYTHONPATH': work}
        app = subprocess.Popen(spec['argv'], cwd=work, env=env, start_new_session=True, preexec_fn=deny_outbound)
        try:
            while app.poll() is None and time.time() < spec['expires_at']:
                time.sleep(1)
        finally:
            if app.poll() is None:
                app.terminate()
                try: app.wait(timeout=3)
                except subprocess.TimeoutExpired: app.kill(); app.wait()
    return app.returncode or 0


if __name__ == '__main__':
    raise SystemExit(main())
