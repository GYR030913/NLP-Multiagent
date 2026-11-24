"""Local operator adapter. No Gradio dependency and no alternate state machine."""
from contextlib import ExitStack
from pathlib import Path
import threading

from .cli import locked
from .config import Config
from .engine import Engine
from .llm import Client
from .report import export, metrics
from .scenarios import discover, load_scenario
from .store import Store, ident
from .tools import Tools
from scripts.evaluate_benchmark import evaluate_benchmark, export_benchmark

GROUPS = ('software', 'newsroom', 'fund')


class UIService:
    def __init__(self, runtime='runtime', data='NLP_Mid_Test', config=None, client_factory=Client):
        self.runtime = Path(runtime).resolve()
        self.data = Path(data).resolve()
        self.config = config or Config.from_env()
        self.client_factory = client_factory
        self._guard = threading.RLock()
        self._jobs = {}

    def store(self, group):
        if group not in GROUPS:
            raise ValueError('Unknown scenario group')
        path = self.runtime / group
        if path.is_symlink():
            raise ValueError('Group directory must not be a link')
        return Store(self.runtime, group)

    def initialize(self):
        paths = discover(self.data)
        if len(paths) != 3:
            raise ValueError('Expected three original scenarios')
        for group, path in zip(GROUPS, paths):
            store = self.store(group)
            with locked(store):
                if not store.state_path.exists():
                    Engine.create(store, load_scenario(path))

    def engine(self, store):
        return Engine(store, self.config, self.client_factory(self.config), Tools(store, self.config))

    def redact(self, value):
        """Do not expose configured secrets in errors or operator views."""
        if isinstance(value, str):
            for secret in (self.config.api_key, self.config.search_key):
                if secret:
                    value = value.replace(secret, '[REDACTED]')
            return value
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        return value

    def job(self, group):
        self.store(group)
        with self._guard:
            job = self._jobs.get(group, {})
            return self.redact({k: v for k, v in job.items() if k not in {'stop', 'thread'}})

    def snapshot(self, group, channel='group'):
        state = self.store(group).load()  # atomic replace permits reads while model is busy
        if channel != 'group' and channel not in state['roles']:
            raise ValueError('Unknown private channel')

        def visible(task):
            return not task.get('private') if channel == 'group' else task.get('private') and task['owner'] == channel

        tasks = {k: v for k, v in state['tasks'].items() if visible(v)}
        events = []
        for event in state['events']:
            if event['kind'] in {'message', 'human_message'}:
                include = event.get('to') == channel or (channel != 'group' and event.get('sender') == channel and event.get('to') == 'human')
            else:
                task = event.get('task')
                task_id = task.get('id') if isinstance(task, dict) else task
                run_tasks = [t for t in tasks.values() if t['run'] == event.get('run')]
                include = task_id in tasks or bool(run_tasks)
            if include:
                events.append(event)
        runs = {t['run'] for t in tasks.values()}
        budgets = [{**r, 'remaining_actions': max(0, r.get('max_actions', self.config.max_run_actions) - r.get('actions_used', r['model_calls']))}
                   for r in state['runs'].values() if r['id'] in runs]
        result = {'group': group, 'channel': channel, 'members': {a: r['title'] for a, r in state['roles'].items()},
                  'tasks': tasks, 'queue': [t for t in state['queue'] if t in tasks], 'runs': budgets,
                  'requests': [q for q in state['requests'].values() if q['task'] in tasks],
                  'events': events, 'artifacts': state['artifacts'] if channel == 'group' else {},
                  'metrics': metrics(state), 'execution': self.job(group), 'experiment_type': 'Development / unofficial results'}
        result['agent_status'] = {a: [t['status'] for t in tasks.values() if t['owner'] == a] or ['idle'] for a in state['roles']}
        return self.redact(result)

    def mutate(self, group, operation):
        store = self.store(group)
        with ExitStack() as stack:
            with self._guard:
                if self._jobs.get(group, {}).get('active'):
                    raise ValueError('Execution active; pause and wait before writing')
                stack.enter_context(locked(store))
            return operation(self.engine(store))

    @staticmethod
    def nonempty(text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Provide nonempty text')
        return text.strip()

    def submit(self, group, text='', benchmark=None, recipient=None):
        def operation(engine):
            if recipient is not None and recipient not in engine.state['roles']:
                raise ValueError('Unknown recipient')
            description = self.nonempty(text) if benchmark is None else engine.state['scenario']['tasks'][int(benchmark) - 1]['text']
            # A repeated click/reload must never turn into a new run, even after completion.
            for task in engine.state['tasks'].values():
                if task['parent'] is None and task['kind'] == 'work' and task['description'] == description and task['private'] == bool(recipient) and (not recipient or task['owner'] == recipient):
                    return 'Existing task ' + task['id'] + '; use Resume'
            if benchmark == 2:
                first = engine.state['scenario']['tasks'][0]['text']
                if not any(t['description'] == first and not t['private'] and t['status'] == 'completed' for t in engine.state['tasks'].values()):
                    raise ValueError('Complete and accept Task 1 before Task 2')
            task = engine.submit(description, owner=recipient, private=bool(recipient))
            return 'Submitted ' + task + '; press Start / Resume'
        if benchmark is not None and benchmark not in (1, 2):
            raise ValueError('Benchmark must be 1 or 2')
        return self.mutate(group, operation)

    def say(self, group, text, recipient='group'):
        text = self.nonempty(text)
        def operation(engine):
            if recipient != 'group' and recipient not in engine.state['roles']:
                raise ValueError('Unknown recipient')
            engine.event('human_message', sender='human', to=recipient, text=text)
            return 'Message saved'
        return self.mutate(group, operation)

    def answer(self, group, request_id, text='', approved=None):
        def operation(engine):
            request = engine.state['requests'].get(request_id)
            if not request:
                raise ValueError('Unknown request; refresh pending requests')
            if request['kind'] == 'clarification':
                if approved is not None:
                    raise ValueError('Use Answer for clarification')
                self.nonempty(text)
            elif not isinstance(approved, bool):
                raise ValueError('Use explicit Approve / Reject')
            if approved is False:
                self.nonempty(text)
            engine.answer(request_id, text, approved)
            return 'Answer saved; press Resume to continue. Check events for tool errors.'
        return self.mutate(group, operation)

    def answer_and_continue(self, group, request_id, text='', approved=None, steps=30):
        if isinstance(steps, bool) or int(steps) != steps or not 1 <= steps <= self.config.max_steps:
            raise ValueError(f'Steps must be 1..{self.config.max_steps}')
        with self._guard:
            self.answer(group, request_id, text, approved)
            state = self.store(group).load()
            if not any(t['status'] == 'running' for t in state['tasks'].values()):
                return 'Answer saved. No further runnable work; check remaining requests or delivery.'
            try:
                return 'Answer saved. ' + self.start(group, steps)
            except (ValueError, OSError) as exc:
                return 'Answer saved, but execution did not start: ' + self.redact(str(exc))

    def start(self, group, steps=30):
        if isinstance(steps, bool) or int(steps) != steps or not 1 <= steps <= self.config.max_steps:
            raise ValueError(f'Steps must be 1..{self.config.max_steps}')
        self.store(group)
        with self._guard:
            if self._jobs.get(group, {}).get('active'):
                raise ValueError('Execution already active')
            with locked(self.store(group)):
                engine = self.engine(self.store(group))
                if not any(t['status'] == 'running' for t in engine.state['tasks'].values()):
                    if not engine.state['tasks']:
                        raise ValueError('No task submitted. Enter a message and click Send.')
                    raise ValueError('No runnable tasks. Answer pending requests or check whether the task has ended.')
            job = {'active': True, 'stop': threading.Event(), 'steps_done': 0, 'steps_requested': int(steps), 'message': 'Starting'}
            self._jobs[group] = job
            thread = threading.Thread(target=self._run, args=(group, job), daemon=True)
            job['thread'] = thread
            thread.start()
        return 'Execution started; events refresh every second'

    def _run(self, group, job):
        try:
            store = self.store(group)
            with locked(store):
                engine = self.engine(store)
                try:
                    for _ in range(job['steps_requested']):
                        if job['stop'].is_set() or not engine.state['queue']:
                            break
                        before = len(engine.state['events'])
                        job['message'] = 'Waiting for model response / executing action'
                        engine.run(1, stop_requested=job['stop'].is_set)
                        job['steps_done'] += 1
                        new = engine.state['events'][before:]
                        if any(e['kind'] in {'model_error', 'fatal_error', 'run_budget_exhausted'} for e in new):
                            job['message'] = 'Stopped: inspect error / budget events before continuing'
                            break
                        job['message'] = 'Step completed'
                    else:
                        job['message'] = 'Step limit reached; Continue to run more steps'
                    if job['stop'].is_set():
                        job['message'] = 'Paused — use Continue to resume'
                    elif job['message'] in {'Starting', 'Step completed'}:
                        job['message'] = 'Queue idle — check pending requests or task status'
                finally:
                    # Serialize finalization with Pause/End so a late End is never lost.
                    with self._guard:
                        for run_id in job.get('end_runs', []):
                            self._end_run(engine, run_id)
                        if job.get('end_runs'):
                            job['message'] = 'Ended by operator — history preserved'
                        job['active'] = False
        except BaseException as exc:
            with self._guard:
                job['message'] = self.redact(f'{type(exc).__name__}: {exc}')
                job['active'] = False

    def pause(self, group):
        self.store(group)
        with self._guard:
            job = self._jobs.get(group)
            if job and job['active']:
                job['stop'].set()
                job['message'] = 'Pause requested — waiting for the current request/tool to stop'
                return job['message']
        return 'Already paused or idle'

    @staticmethod
    def _end_run(engine, run_id):
        if engine.state['runs'][run_id]['status'] == 'running':
            engine.event('human_abort', run=run_id, reason='Ended by operator from chat')
            engine.end_run(run_id, 'failed')

    def end(self, group, channel='group'):
        store = self.store(group)
        with self._guard:
            state = store.load()
            if channel != 'group' and channel not in state['roles']:
                raise ValueError('Unknown private channel')
            runs = [r['id'] for r in state['runs'].values()
                    if r['status'] == 'running' and
                    ((channel == 'group' and not r.get('private')) or
                     (channel != 'group' and r.get('private') and state['tasks'][r['root']]['owner'] == channel))]
            if not runs:
                return 'No active task in this conversation'
            job = self._jobs.get(group)
            if job and job['active']:
                job['end_runs'] = list(set(job.get('end_runs', []) + runs))
                job['stop'].set()
                job['message'] = 'End requested — waiting for the current request/tool to stop'
                return job['message']
            def operation(engine):
                for run_id in runs:
                    self._end_run(engine, run_id)
                return 'Ended by operator — history preserved'
            return self.mutate(group, operation)

    def send(self, group, channel, text, mode='task', steps=30):
        # Keep the UI operation ordered; each backend mutation still uses the CLI lock.
        if mode not in {'task', 'message'}:
            raise ValueError('Unknown message mode')
        if isinstance(steps, bool) or int(steps) != steps or not 1 <= steps <= self.config.max_steps:
            raise ValueError(f'Steps must be 1..{self.config.max_steps}')
        with self._guard:
            if mode == 'task':
                result = self.submit(group, text, recipient=None if channel == 'group' else channel)
                if result.startswith('Existing task'):
                    return result  # repeated clicks must not spend another batch of calls
            else:
                def follow_up(engine):
                    if channel != 'group' and channel not in engine.state['roles']:
                        raise ValueError('Unknown private channel')
                    active = any(r['status'] == 'running' and
                                 ((channel == 'group' and not r.get('private')) or
                                  (channel != 'group' and r.get('private') and engine.state['tasks'][r['root']]['owner'] == channel))
                                 for r in engine.state['runs'].values())
                    if active:
                        engine.event('human_message', sender='human', to=channel, text=self.nonempty(text))
                    else:
                        # Keep accepted runs immutable; new follow-ups inherit the same group context.
                        engine.submit(self.nonempty(text), owner=None if channel == 'group' else channel,
                                      private=channel != 'group')
                self.mutate(group, follow_up)
            state = self.store(group).load()
            if not any(t['status'] == 'running' for t in state['tasks'].values()):
                return 'Message saved. No runnable tasks; check pending human requests.'
            try:
                return 'Message saved. ' + self.start(group, steps)
            except (ValueError, OSError) as exc:
                return 'Message saved, but execution did not start: ' + self.redact(str(exc))

    def abort(self, group, reason):
        return self.mutate(group, lambda e: e.abort(self.nonempty(reason)))

    @staticmethod
    def check_export_tree(path):
        if path.is_symlink() or any(p.is_symlink() for p in path.rglob('*')):
            raise ValueError('Export directory must not contain links')

    def export_group(self, group):
        def operation(engine):
            self.check_export_tree(engine.store.path / 'export')
            for item in engine.state['artifacts'].values():
                ident(item['id'])
                digest = item['sha256']
                if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                    raise ValueError('Invalid artifact digest')
                blob = engine.store.path / 'artifacts' / digest
                if blob.is_symlink() or blob.parent.is_symlink():
                    raise ValueError('Artifact blobs must not be links')
            output = export(engine.store, engine.state)
            # Preserve legacy exports on disk, but do not offer the obsolete report name.
            return sorted(str(p) for p in output.rglob('*')
                          if p.is_file() and p != output / 'performance_report.md')
        return self.mutate(group, operation)

    def benchmark(self, export_files=False):
        # All three locks give one coherent evaluation snapshot; never wait on a writer.
        with self._guard, ExitStack() as stack:
            for group in GROUPS:
                stack.enter_context(locked(self.store(group)))
            if export_files:
                self.check_export_tree(self.runtime / 'benchmark')
                output = export_benchmark(self.runtime)
                return [str(output / 'benchmark_metrics.json'), str(output / 'benchmark_report.md')]
            result = evaluate_benchmark(self.runtime)
            result['experiment_type'] = 'Development / unofficial results'
            for group, scenario in result['scenarios'].items():
                store = self.store(group)
                state = store.load() if store.state_path.exists() else None
                for task in scenario['tasks'].values():
                    task['display_status'] = task['status']
                    if state and task['run_id']:
                        run = state['runs'][task['run_id']]
                        if run.get('budget_exhausted'):
                            task['display_status'] = 'budget_exhausted'
                        elif run['status'] == 'running':
                            statuses = [t['status'] for t in state['tasks'].values() if t['run'] == run['id']]
                            if 'waiting_for_human' in statuses:
                                task['display_status'] = 'waiting_for_human'
            return self.redact(result)
