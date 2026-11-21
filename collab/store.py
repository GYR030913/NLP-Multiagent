import hashlib
import json
import re
import os
import time
from pathlib import Path
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def ident(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('IDs must contain 1-64 letters, digits, underscores or hyphens.')
    return value


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(6):
        try:
            temp.replace(path)
            break
        except PermissionError:
            # Windows readers/AV may briefly deny delete-sharing on state.json.
            # Keep the old complete state; never fall back to a partial write.
            if attempt == 5:
                raise
            time.sleep(.05 * (attempt + 1))


class Store:
    def __init__(self, root, group):
        self.path = Path(root).resolve() / ident(group)
        self.path.mkdir(parents=True, exist_ok=True)
        self.state_path = self.path / 'state.json'

    def load(self):
        state = json.loads(self.state_path.read_text(encoding='utf-8'))
        if state.get('version', 1) not in (1, 2):
            raise ValueError('Unknown state schema; refusing to load/write it')
        return state

    def save(self, state):
        if state.get('version', 1) not in (1, 2):
            raise ValueError('Unknown state schema; refusing to write it')
        dump(self.state_path, state)

    def event(self, state, kind, **data):
        event = {'seq': len(state['events']) + 1, 'time': now(), 'kind': kind, **data}
        state['events'].append(event)
        self.save(state)
        return event

    def workspace(self, agent):
        base = self.path / 'agents' / ident(agent)
        scope = getattr(self, '_task_scope', None)
        if scope and scope.get('private') and scope['owner'] == agent:
            base = base / 'private' / ident(scope['id'])
        path = base / 'workspace'
        path.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt' and os.geteuid() == 0:
            # Sandbox runs as nobody even when the host harness runs as root.
            path.chmod(0o777)
        return path

    def remember(self, agent, text, task, outcome):
        base = self.path / 'agents' / ident(agent)
        if self.state_path.exists() and self.load().get('tasks', {}).get(task, {}).get('private'):
            base = base / 'private' / ident(task)
        path = base / 'memory.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': now(), 'task': task, 'outcome': outcome, 'lesson': text}, ensure_ascii=False) + '\n')

    def recall(self, agent, query, limit=6, private_task=None):
        state = self.load()
        if state.get('version') == 2 and (self.path / 'memory-v2/events.jsonl').exists():
            from .memory import Memory
            task = state['tasks'].get(private_task, {}) if private_task else {'owner': agent, 'private': False}
            return [{**r, 'lesson': r['content']} for r in Memory(self,state).search(task,query,limit,include_candidates=True)]
        base = self.path / 'agents' / ident(agent)
        if private_task:
            base = base / 'private' / ident(private_task)
        path = base / 'memory.jsonl'
        if not path.exists():
            return []
        terms = set(re.findall(r'\w+', query.lower()))
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
        # Legacy memories may predate separate private storage. Never recall them publicly.
        tasks = self.load().get('tasks', {}) if self.state_path.exists() else {}
        if not private_task:
            rows = [row for row in rows if not tasks.get(row.get('task'), {}).get('private')]
        ranked = sorted(enumerate(rows), key=lambda pair: (len(terms & set(re.findall(r'\w+', pair[1]['lesson'].lower()))), pair[0]), reverse=True)
        return [row for _, row in ranked[:limit]]

    def snapshot(self):
        files = list(self.path.glob('agents/*/memory.jsonl')) + list(self.path.glob('memory-v2/events.jsonl'))
        return {str(p.relative_to(self.path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
