import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from contextlib import contextmanager
from .config import Config
from .store import Store
from .scenarios import discover, load_scenario
from .llm import Client
from .tools import Tools
from .engine import Engine
from .report import export, metrics


@contextmanager
def locked(store):
    lock = store.path / 'writer.lock'
    if lock.is_dir():
        raise ValueError('Legacy directory lock found. Exit the old chat process first; remove the empty writer.lock directory only after confirming it has stopped.')
    handle = lock.open('a+b')
    acquired = False
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise ValueError('Another writer is active for this group; close that chat before opening another writer.') from None
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def status(engine):
    state = engine.state
    for run in state['runs'].values():
        if run['status'] == 'running':
            safe_print(
                f"Run {run['id']}: actions "
                f"{run.get('actions_used', run.get('model_calls', 0))}/"
                f"{run.get('max_actions', engine.config.max_run_actions)}"
                + (' BUDGET EXHAUSTED' if run.get('budget_exhausted') else '')
            )
    for task in state['tasks'].values():
        safe_print(f"{task['id']:>4} {task['owner']:<15} {task['status']:<22} {task['description'][:70]}")
    safe_print('\nPending human requests:')
    for request in state['requests'].values():
        if request['status'] == 'pending':
            safe_print(json.dumps(request, ensure_ascii=False, indent=2))
    safe_print(f"\nQueue: {len(state['queue'])}. Use resume to continue. Export includes complete dialogue and artifacts.")


def safe_print(text, **kwargs):
    """Print to a Windows console that may not accept the model's Unicode.

    Agent messages can contain emoji or other characters outside the active
    code page. Printing must never abort a run, so unencodable characters are
    replaced instead of raising.
    """
    stream = kwargs.get('file') or sys.stdout
    encoding = getattr(stream, 'encoding', None) or 'ascii'
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        text = text.encode(encoding, errors='replace').decode(encoding, errors='replace')
    print(text, **kwargs)


def observe(event):
    kind = event['kind']
    if kind == 'agent_action':
        action = event['action']
        detail = action.get('name', action.get('to', ''))
        safe_print(f"[{event['seq']}] {event['agent']}/{event['task']}: {action['type']} {detail}", flush=True)
    elif kind == 'message':
        safe_print(f"{event['sender']} -> {event['to']}: {event['text']}", flush=True)
    elif kind in {'action_error', 'model_error'}:
        safe_print(f"{kind}: {event['error']}", flush=True)
    elif kind == 'human_request':
        safe_print('HUMAN REQUEST: ' + json.dumps(event['request'], ensure_ascii=False), flush=True)
    elif kind == 'run_budget_exhausted':
        safe_print(
            f"RUN BUDGET EXHAUSTED: {event['actions_used']}/{event['max_actions']} actions",
            flush=True,
        )


def chat(engine):
    print('Commands: /task 1|2, /resume [steps], /answer q1 text, /approve q1, /reject q1 reason,')
    print('/dm agent text, /say text, /abort reason, /status, /export, /quit. Plain text posts a new group task.')
    while True:
        try:
            line = input('human> ').strip()
            if not line:
                continue
            command, _, rest = line.partition(' ')
            if command == '/quit':
                break
            if command == '/task':
                number = int(rest)
                if number not in {1, 2}:
                    raise ValueError('Task must be 1 or 2')
                engine.submit(engine.state['scenario']['tasks'][number - 1]['text'])
                engine.run(30)
            elif command == '/resume':
                engine.run(int(rest) if rest else 30)
            elif command in {'/answer', '/approve', '/reject'}:
                request, _, text = rest.partition(' ')
                if command == '/answer' and not text:
                    raise ValueError('Provide an actual answer')
                engine.answer(request, text, None if command == '/answer' else command == '/approve')
            elif command == '/dm':
                agent, _, text = rest.partition(' ')
                if not text:
                    raise ValueError('Provide a private message')
                engine.submit(text, owner=agent, private=True)
                engine.run(10)
            elif command == '/say':
                engine.event('human_message', sender='human', to='group', text=rest)
            elif command == '/abort':
                engine.abort(rest)
            elif command == '/export':
                safe_print(export(engine.store, engine.state))
            elif command == '/status':
                pass
            elif command.startswith('/'):
                raise ValueError('Unknown command')
            else:
                engine.submit(line)
                engine.run(30)
            status(engine)
        except EOFError:
            break
        except KeyboardInterrupt:
            print('\nPaused; state saved. Use /resume or /quit.')
        except (ValueError, KeyError, OSError) as exc:
            safe_print(f'Error: {exc}')


def parser():
    p = argparse.ArgumentParser(description='Multi-agent course platform (DeepSeek/HKGAI only)')
    p.add_argument('--runtime', default='runtime')
    sub = p.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('scenarios')
    listing.add_argument('--data', default='NLP_Mid_Test')
    create = sub.add_parser('create')
    create.add_argument('group')
    create.add_argument('--scenario', required=True, help='Path to scenario directory')
    start = sub.add_parser('post')
    start.add_argument('group')
    choice = start.add_mutually_exclusive_group(required=True)
    choice.add_argument('--task', type=int, choices=[1, 2])
    choice.add_argument('--text')
    choice.add_argument('--file')
    start.add_argument('--to', help='Private one-to-one task to a named agent')
    start.add_argument('--steps', type=int, default=30)
    resume = sub.add_parser('resume')
    resume.add_argument('group')
    resume.add_argument('--steps', type=int, default=30)
    revive = sub.add_parser('revive')
    revive.add_argument('group')
    revive.add_argument('--task', default='root')
    revive.add_argument('--steps', type=int, default=30)
    for cmd in ('answer', 'approve', 'reject'):
        item = sub.add_parser(cmd)
        item.add_argument('group')
        item.add_argument('request')
        item.add_argument('text', nargs='?', default='')
    say = sub.add_parser('say')
    say.add_argument('group')
    say.add_argument('text')
    say.add_argument('--to', default='group')
    for cmd in ('status', 'export', 'metrics', 'chat'):
        item = sub.add_parser(cmd)
        item.add_argument('group')
    sub.add_parser('doctor')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == 'scenarios':
        for path in discover(args.data):
            scenario = load_scenario(path)
            print(f"{path}\n  Roles: {', '.join(scenario['roles'])}; tasks: {len(scenario['tasks'])}")
        return 0
    try:
        config = Config.from_env()
        if args.command == 'doctor':
            import shutil
            print(f'Provider={config.provider}; model={config.model}; key configured={bool(config.api_key)}; search configured={bool(config.search_key)}')
            print('Search mode=' + ('API key' if config.search_key else 'Tavily keyless (rate-limited)'))
            print('Static JavaScript checker ready=' + str(bool(shutil.which('node'))))
            result = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], capture_output=True, timeout=15)
            print('Docker ready=' + str(result.returncode == 0))
            if result.returncode == 0:
                image = subprocess.run(['docker', 'image', 'inspect', config.image], capture_output=True, timeout=15)
                print('Sandbox image ready=' + str(image.returncode == 0))
            return 0 if result.returncode == 0 and config.api_key else 1
        store = Store(args.runtime, args.group)
        with locked(store):
            if args.command == 'create':
                Engine.create(store, load_scenario(args.scenario))
                print('Group created; post task 1, then task 2 to the same group.')
                return 0
            engine = Engine(store, config, Client(config), Tools(store, config), observer=observe)
            if args.command == 'post':
                text = args.text or (Path(args.file).read_text(encoding='utf-8') if args.file else engine.state['scenario']['tasks'][args.task - 1]['text'])
                engine.submit(text, owner=args.to, private=bool(args.to))
                engine.run(args.steps)
                status(engine)
            elif args.command == 'resume':
                engine.run(args.steps)
                status(engine)
            elif args.command == 'revive':
                run_id = args.task
                if run_id == 'root':
                    run_id = next((r['root'] for r in engine.state['runs'].values()
                                   if r['status'] == 'failed'), None)
                if not run_id:
                    raise ValueError('No failed run to revive')
                engine.revive(run_id)
                engine.run(args.steps)
                status(engine)
            elif args.command in {'answer', 'approve', 'reject'}:
                if args.command == 'answer' and not args.text:
                    raise ValueError('Provide a real answer')
                engine.answer(args.request, args.text, None if args.command == 'answer' else args.command == 'approve')
                status(engine)
            elif args.command == 'say':
                if args.to != 'group' and args.to not in engine.state['roles']:
                    raise ValueError('Unknown recipient')
                engine.event('human_message', sender='human', to=args.to, text=args.text)
                print('Message saved. Use resume, or post --to AGENT for a new private task.')
            elif args.command == 'status':
                status(engine)
            elif args.command == 'chat':
                chat(engine)
            elif args.command == 'metrics':
                print(json.dumps(metrics(engine.state), ensure_ascii=False, indent=2))
            elif args.command == 'export':
                print(export(store, engine.state))
        return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        safe_print(f'Error: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
