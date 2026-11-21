"""Trusted worker copied into the browser image; never imports the host project."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


def main():
    from playwright.sync_api import sync_playwright
    spec = json.load(sys.stdin)
    port = spec['port']
    reports, errors, blocked, console_errors, http_errors = [], [], [], [], []
    with tempfile.TemporaryDirectory(prefix='app-', dir='/tmp') as work:
        shutil.copytree('/workspace', work, dirs_exist_ok=True)
        env = {**os.environ, 'HOST': '0.0.0.0', 'PORT': str(port), 'DB_PATH': work + '/bookings.db', 'PYTHONPATH': work}
        with tempfile.TemporaryFile() as log:
            app = subprocess.Popen(spec['argv'], cwd=work, env=env, stdout=log, stderr=log, start_new_session=True)
            try:
                deadline = time.monotonic() + min(20, spec['timeout'])
                while True:
                    if app.poll() is not None:
                        raise RuntimeError('Application exited before becoming ready')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Application did not start on declared port')
                        time.sleep(0.1)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
                    context = browser.new_context(service_workers='block')
                    def route(request):
                        u = urlsplit(request.request.url)
                        if u.scheme == 'http' and u.hostname in {'127.0.0.1', 'localhost'} and u.port == port and not u.username:
                            request.continue_()
                        else:
                            blocked.append(request.request.url[:200])
                            request.abort()
                    context.route('**/*', route)
                    page = context.new_page()
                    page.set_default_timeout(7000)
                    page.on('pageerror', lambda error: errors.append(str(error)[:500]))
                    page.on('console', lambda message: console_errors.append({'text': message.text[:500], 'url': message.location.get('url', '')}) if message.type == 'error' else None)
                    page.on('response', lambda response: http_errors.append({'path': urlsplit(response.url).path, 'status': response.status}) if response.status >= 400 else None)
                    page.goto(f'http://127.0.0.1:{port}/', wait_until='networkidle')
                    from playwright.sync_api import expect
                    for check in spec['checks']:
                        action = check['action']
                        loc = page.locator(check['selector']) if 'selector' in check else None
                        if action == 'goto':
                            page.goto(f'http://127.0.0.1:{port}' + check['path'], wait_until='networkidle')
                        elif action == 'fill': loc.fill(check['value'])
                        elif action == 'click': loc.click()
                        elif action == 'select': loc.select_option(check['value'])
                        elif action == 'press': loc.press(check['key'])
                        elif action == 'expect_text': expect(loc).to_contain_text(check['text'])
                        elif action == 'expect_value': expect(loc).to_have_value(check['value'])
                        elif action == 'expect_visible': expect(loc).to_be_visible()
                        elif action == 'expect_count': expect(loc).to_have_count(check['count'])
                        else: raise ValueError('Unsupported action')
                        reports.append({'action': action, 'passed': True})
                    page.wait_for_timeout(100)
                    expected = spec.get('expected_http_errors', [])
                    unexpected_http = [item for item in http_errors if item not in expected]
                    for message in console_errors:
                        path = urlsplit(message['url']).path
                        explained = message['text'].startswith('Failed to load resource:') and any(
                            item['path'] == path and item in expected for item in http_errors)
                        if not explained:
                            errors.append(message['text'])
                    if errors or blocked or unexpected_http:
                        raise RuntimeError('Browser errors or external resources detected')
                    context.close()
                    browser.close()
                print(json.dumps({'passed': True, 'checks': reports, 'browser_errors': errors, 'blocked_requests': blocked, 'expected_http_errors_observed': http_errors}))
                return 0
            except Exception as exc:
                log.seek(0)
                print(json.dumps({'passed': False, 'checks': reports, 'error': str(exc)[:2000],
                                  'browser_errors': errors[:10], 'blocked_requests': blocked[:10],
                                  'http_errors': http_errors[:10],
                                  'application_log': log.read(2000).decode(errors='replace')}))
                return 1
            finally:
                if app.poll() is None:
                    app.terminate()
                    try: app.wait(timeout=3)
                    except subprocess.TimeoutExpired: app.kill(); app.wait()


if __name__ == '__main__':
    raise SystemExit(main())
