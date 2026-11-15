"""Run manually in a normal terminal to verify Docker and optionally web search."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collab.config import Config
from collab.store import Store
from collab.tools import Tools


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--search', action='store_true', help='Also issue a public test search')
    args = parser.parse_args()
    config = Config.from_env()
    base = Path(__file__).resolve().parents[1]
    store = Store(base / 'runtime', '_diagnostics')
    tools = Tools(store, config)
    state = {'sources': {}, 'artifacts': {}}
    code = '''import json, os, pathlib, socket
pathlib.Path('isolation-proof.txt').write_text('sandbox writes only own workspace')
blocked = False
try:
    socket.create_connection(('1.1.1.1', 443), timeout=2)
except OSError:
    blocked = True
print(json.dumps({'own_write': pathlib.Path('isolation-proof.txt').exists(), 'network_blocked': blocked,
                  'no_llm_secret': not bool(os.getenv('LLM_API_KEY')), 'no_git_secret': not bool(os.getenv('GITHUB_TOKEN'))}))
'''
    first = tools.execute(state, 'first', 'code.run', {'argv': ['python', '-c', code]})
    print('first_agent:', json.dumps(first))
    second = tools.execute(state, 'second', 'code.run', {'argv': ['python', '-c',
                "import pathlib; print('other_agent_file_visible=' + str(pathlib.Path('isolation-proof.txt').exists()))"]})
    print('second_agent:', json.dumps(second))
    ok = first['exit_code'] == 0 and second['exit_code'] == 0
    if ok:
        checks = json.loads(first['output'].strip())
        ok = all(checks.values()) and 'other_agent_file_visible=False' in second['output']
    if args.search:
        try:
            results = tools.execute(state, 'first', 'web.search', {'query': 'HKUST artificial intelligence campus', 'include_domains': ['hkust.edu.hk']})
            print('search:', json.dumps({'results': len(results), 'urls': [r['url'] for r in results]}))
            ok = ok and bool(results)
        except Exception as exc:
            # Do not print provider payloads, credentials or arbitrary exception content.
            print('search_failed:', type(exc).__name__)
            ok = False
    print('runtime_checks_passed:', ok)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
