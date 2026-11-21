"""Real offline browser positive/negative controls and lease recovery. No model calls."""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collab.config import Config
from collab.engine import Engine
from collab.store import Store, dump
from collab.tools import Tools
from collab.preview import start, stop

APP = '''import os,socket,json,errno
from http.server import BaseHTTPRequestHandler,HTTPServer
PAGE = """<!doctype html><meta charset="utf-8"><title>Browser control</title>
<button id="go" onclick="add()">Book</button><p id="status">Ready</p>
<script>function add(){document.querySelector('#status').textContent='Booked';}</script>"""
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path == '/network':
   denied=[]
   for target in [('1.1.1.1',443),('127.0.0.1',8000)]:
    try: socket.create_connection(target,timeout=1).close();denied.append(False)
    except OSError as exc: denied.append(exc.errno==errno.EPERM)
   with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
    try: udp.sendto(b'probe',('1.1.1.1',53));denied.append(False)
    except OSError as exc: denied.append(exc.errno==errno.EPERM)
   payload=json.dumps({'denied':denied,'no_key':not bool(os.getenv('LLM_API_KEY'))}).encode()
   self.send_response(200);self.end_headers();self.wfile.write(payload);return
  self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(PAGE.encode())
 def log_message(self,*args):pass
HTTPServer(('0.0.0.0',int(os.getenv('PORT','8000'))),Handler).serve_forever()
'''


def main():
    config = Config.from_env()
    store = Store('runtime/development_m2', '_browser_controls')
    if not store.state_path.exists():
        Engine.create(store, {'roles': {'dev': {'title': 'dev', 'prompt': 'dev'}}, 'tasks': []})
    state = store.load()
    tools = Tools(store, config)
    root = store.workspace('dev')
    (root / 'app.py').write_text(APP, encoding='utf-8')
    good = tools.execute(state, 'dev', 'artifact.publish_bundle', {'paths': ['app.py'], 'entrypoints': ['app.py']})
    plan = {'argv': ['python', 'app.py'], 'checks': [
        {'action': 'click', 'selector': '#go'},
        {'action': 'expect_text', 'selector': '#status', 'text': 'Booked'}]}
    positive = tools.execute(state, 'dev', 'browser.verify', {**plan, 'bundle_id': good['id']})
    print('browser_positive_exit_code:', positive['exit_code'], flush=True)
    assert positive['exit_code'] == 0, positive
    (root / 'app.py').write_text(APP.replace('onclick="add()"', 'onclick="missing()"'), encoding='utf-8')
    broken = tools.execute(state, 'dev', 'artifact.publish_bundle', {'paths': ['app.py']})
    negative = tools.execute(state, 'dev', 'browser.verify', {**plan, 'bundle_id': broken['id']})
    print('syntax_valid_but_broken_button_rejected:', negative['exit_code'] != 0, flush=True)
    assert negative['exit_code'] != 0, negative
    lease = start(store, state, config, {'bundle_id': good['id'], 'argv': ['python', 'app.py'],
                                      'port': 8766, 'lease_seconds': 12})
    try:
        for attempt in range(30):
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(lease['url'], timeout=1) as response:
                    assert response.status == 200
                    break
            except OSError: time.sleep(0.2)
        else: raise RuntimeError('Local preview unreachable')
        print('preview_loopback_http_ok:', True, flush=True)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(lease['url'] + '/network', timeout=2) as response:
            network = json.load(response)
        assert all(network['denied']) and network['no_key'], network
        print('preview_outbound_tcp_udp_blocked_and_no_secret:', True, flush=True)
        time.sleep(max(0, lease['expires_at'] - time.time()) + 1)
        exists = subprocess.run(['docker', 'inspect', lease['container']], capture_output=True, timeout=10).returncode == 0
        assert not exists, 'Preview container survived its lease'
        print('preview_lease_auto_expired:', True, flush=True)
    finally:
        cleanup = stop(store, state, lease['id'])
        store.save(state)
    image = subprocess.check_output(['docker', 'image', 'inspect', config.browser_image, '--format', '{{.Id}}'], text=True).strip()
    report = {'positive': positive, 'negative': negative, 'preview': {'loopback_http': True,
              'auto_expired': True, 'outbound_denied': network, 'cleanup': cleanup}, 'browser_image': image}
    dump(store.path / 'verification.json', report)
    print('m2_controls_passed: True', flush=True)


if __name__ == '__main__': main()
