import hashlib
import json
import re
import os
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
    temp.replace(path)


class Store:
    def __init__(self, root, group):
        self.path = Path(root).resolve() / ident(group)
        self.path.mkdir(parents=True, exist_ok=True)
        self.state_path = self.path / 'state.json'

    def load(self):
        return json.loads(self.state_path.read_text(encoding='utf-8'))

    def save(self, state):
        dump(self.state_path, state)

    def event(self, state, kind, **data):
        event = {'seq': len(state['events']) + 1, 'time': now(), 'kind': kind, **data}
        state['events'].append(event)
        self.save(state)
        return event

    def workspace(self, agent):
        path = self.path / 'agents' / ident(agent) / 'workspace'
        path.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt' and os.geteuid() == 0:
            # Sandbox runs as nobody even when the host harness runs as root.
            path.chmod(0o777)
        return path

    def remember(self, agent, text, task, outcome):
        path = self.path / 'agents' / ident(agent) / 'memory.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': now(), 'task': task, 'outcome': outcome, 'lesson': text}, ensure_ascii=False) + '\n')

    def recall(self, agent, query, limit=6):
        path = self.path / 'agents' / ident(agent) / 'memory.jsonl'
        if not path.exists():
            return []
        terms = set(re.findall(r'\w+', query.lower()))
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
        ranked = sorted(enumerate(rows), key=lambda pair: (len(terms & set(re.findall(r'\w+', pair[1]['lesson'].lower()))), pair[0]), reverse=True)
        return [row for _, row in ranked[:limit]]

    def snapshot(self):
        return {str(p.relative_to(self.path)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.path.glob('agents/*/memory.jsonl')}
