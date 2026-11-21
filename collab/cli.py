import argparse
import json
import os
import subprocess
from pathlib import Path
from contextlib import contextmanager
from .config import Config
from .store import Store
from .scenarios import discover, load_scenario
from .llm import Client
from .tools import Tools
from .engine import Engine
from .report import export, metrics
from .runner import resume_options, resume
from .migration import migrate


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
            print(
                f"Run {run['id']}: actions "
                f"{run.get('actions_used', run.get('model_calls', 0))}/"
                f"{run.get('max_actions', engine.config.max_run_actions)}"
                f"; remaining={max(0, run.get('max_actions', engine.config.max_run_actions) - run.get('actions_used', run.get('model_calls', 0)))}"
                + (' BUDGET EXHAUSTED' if run.get('budget_exhausted') else '')
            )
    for task in state['tasks'].values():
        print(f"{task['id']:>4} {task['owner']:<15} {task['status']:<22} {task['description'][:70]}")
    print('\nPending human requests:')
    for request in state['requests'].values():
        if request['status'] == 'pending':
            print(json.dumps(request, ensure_ascii=False, indent=2))
    print(f"\nQueue: {len(state['queue'])}. Use resume to continue. Export includes complete dialogue and artifacts.")
    execution = state.get('execution', {})
    print(f"Execution: {'active' if engine._is_running else 'not active'}; mode={execution.get('mode', 'not started')}; "
          f"stop_reason={execution.get('stop_reason', 'not started')}; actions_this_call={execution.get('actions_this_call', 0)}")
    reason = execution.get('stop_reason')
    if any(r['status'] == 'pending' for r in state['requests'].values()):
        print('Next: answer/approve/reject the pending request before /resume. No automatic approval.')
    elif reason and 'budget' in reason:
        print('Next: inspect the exhausted budget; repeating /resume cannot reset it.')
    elif reason == 'completed':
        print('Next: /export or submit the next task after acceptance.')
    else:
        print('Next: /resume [steps] or /resume --continuous (same persistent total budget).')


def observe(event):
    kind = event['kind']
    if kind == 'agent_action':
        action = event['action']
        detail = action.get('name', action.get('to', ''))
        print(f"[{event['seq']}] {event['agent']}/{event['task']}: {action['type']} {detail}", flush=True)
    elif kind == 'message':
        print(f"{event['sender']} -> {event['to']}: {event['text']}", flush=True)
    elif kind in {'action_error', 'model_error'}:
        print(f"{kind}: {event['error']}", flush=True)
    elif kind == 'human_request':
        print('HUMAN REQUEST: ' + json.dumps(event['request'], ensure_ascii=False), flush=True)
    elif kind == 'run_budget_exhausted':
        print(
            f"RUN BUDGET EXHAUSTED: {event['actions_used']}/{event['max_actions']} actions",
            flush=True,
        )


def chat(engine):
    print('Commands: /task 1|2, /resume [steps] OR /resume --continuous, /answer q1 text, /approve q1, /reject q1 reason,')
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
                steps, continuous = resume_options(rest)
                resume(engine, steps, continuous)
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
                print(export(engine.store, engine.state))
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
            print(f'Error: {exc}')


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
    resume_parser = sub.add_parser('resume')
    resume_parser.add_argument('group')
    mode = resume_parser.add_mutually_exclusive_group()
    mode.add_argument('--steps', type=int)
    mode.add_argument('--continuous', action='store_true')
    migration = sub.add_parser('migrate', help='Backed-up explicit migration of legacy state')
    migration.add_argument('group')
    migration.add_argument('--dry-run', action='store_true')
    preview = sub.add_parser('preview', help='Explicit operator request to start/stop a local bundle preview')
    preview.add_argument('group')
    operation = preview.add_mutually_exclusive_group(required=True)
    operation.add_argument('--bundle')
    operation.add_argument('--stop')
    operation.add_argument('--snapshot', metavar='DATA_KEY')
    preview.add_argument('--to-role')
    preview.add_argument('--port', type=int, default=8000)
    preview.add_argument('--app-port', type=int, default=8000)
    preview.add_argument('--lease-seconds', type=int, default=1800)
    preview.add_argument('--data-key', default='default')
    preview.add_argument('--argv', nargs=argparse.REMAINDER)
    memory = sub.add_parser('memory', help='Scoped memory inspection and explicit operator administration')
    memory.add_argument('group')
    memory.add_argument('operation', choices=['list','inspect','promote','retract','supersede','purge','rebuild'])
    memory.add_argument('--id')
    memory.add_argument('--digest', help='Exact digest returned by inspect; required for changes')
    memory.add_argument('--reason', default='')
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
            if args.command == 'migrate':
                print(json.dumps(migrate(store, dry_run=args.dry_run), indent=2))
                return 0
            if args.command == 'preview':
                from .preview import start, stop, snapshot_data
                state = store.load()
                if state.get('version') != 2:
                    raise ValueError('Migrate the development copy first')
                if args.stop:
                    result = stop(store, state, args.stop)
                elif args.snapshot:
                    if not args.to_role:
                        raise ValueError('--snapshot requires --to-role')
                    result = snapshot_data(store, state, args.snapshot, args.to_role)
                else:
                    if not args.argv:
                        raise ValueError('preview needs --argv followed by the application command')
                    result = start(store, state, config, {'bundle_id': args.bundle, 'argv': args.argv,
                        'port': args.port, 'app_port': args.app_port, 'lease_seconds': args.lease_seconds, 'data_key': args.data_key})
                store.event(state, 'operator_preview', result=result)
                print(json.dumps(result, indent=2))
                return 0
            if args.command == 'memory':
                from .memory import Memory, fingerprint
                state = store.load()
                backend = Memory(store,state)
                backend.flush()
                if args.operation == 'rebuild':
                    result = backend.rebuild()
                elif args.operation == 'list':
                    result = [{'id':r['id'],'owner':r['owner'],'status':backend.effective(r)['status'],
                               'origin_scope':r['origin_scope'],'version':r['version']} for r in backend.records().values()]
                else:
                    record = backend.records().get(args.id)
                    if not record:
                        raise ValueError('Unknown memory ID')
                    digest = fingerprint(record)
                    if args.operation == 'inspect':
                        result = {'record':backend.effective(record),'approval_digest':digest}
                    else:
                        if not args.reason.strip() or args.digest != digest:
                            raise ValueError('Inspect current memory first; provide its exact --digest and --reason')
                        if args.operation == 'purge':
                            result = backend.purge(args.id,args.reason)
                        else:
                            target = {'promote':'active','retract':'retracted','supersede':'superseded'}[args.operation]
                            result = backend.transition(args.id,target,'human',[],args.reason)
                        store.event(state,'memory_operator_action',memory_id=args.id,operation=args.operation,approval_digest=digest)
                print(json.dumps(result,ensure_ascii=False,indent=2))
                return 0
            engine = Engine(store, config, Client(config), Tools(store, config), observer=observe)
            if args.command == 'post':
                text = args.text or (Path(args.file).read_text(encoding='utf-8') if args.file else engine.state['scenario']['tasks'][args.task - 1]['text'])
                engine.submit(text, owner=args.to, private=bool(args.to))
                engine.run(args.steps)
                status(engine)
            elif args.command == 'resume':
                resume(engine, args.steps if args.steps is not None else 30, args.continuous)
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
        print(f'Error: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
