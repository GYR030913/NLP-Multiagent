"""Explicit, backed-up v1 -> v2 migration. Caller must hold the group lock."""
from copy import deepcopy
import hashlib
import json
from .store import dump, now

VERSION = 2


def validate_state(state):
    tasks, runs, roles = state.get('tasks', {}), state.get('runs', {}), state.get('roles', {})
    queue = state.get('queue', [])
    if len(queue) != len(set(queue)) or any(tid not in tasks for tid in queue):
        raise ValueError('Invalid persisted task queue')
    for tid, task in tasks.items():
        if task.get('id') != tid or task.get('owner') not in roles or task.get('run') not in runs:
            raise ValueError('Invalid persisted task identity')
        for ref in task.get('children', []) + task.get('depends_on', []):
            if ref not in tasks or tasks[ref]['run'] != task['run']:
                raise ValueError('Invalid persisted task relationship')
        if task.get('parent') and (task['parent'] not in tasks or tid not in tasks[task['parent']]['children']):
            raise ValueError('Invalid persisted task parent')
    visited, active = set(), set()
    def visit(tid):
        if tid in active: raise ValueError('Persisted task dependency cycle')
        if tid in visited: return
        active.add(tid)
        for ref in tasks[tid].get('children', []) + tasks[tid].get('depends_on', []): visit(ref)
        active.remove(tid); visited.add(tid)
    for tid in tasks: visit(tid)
    for run in runs.values():
        if run.get('root') not in tasks or run.get('actions_used', run.get('model_calls', 0)) < 0:
            raise ValueError('Invalid persisted run root/budget')
    for request in state.get('requests', {}).values():
        if request.get('task') not in tasks: raise ValueError('Invalid persisted human request')
    for seq, event in enumerate(state.get('events', []), 1):
        if event.get('seq') != seq: raise ValueError('Invalid evidence event sequence')


def upgrade(state):
    version = state.get('version', 1)
    if version not in (1, VERSION):
        raise ValueError(f'Unsupported state version {version}; refusing to write')
    validate_state(state)
    result = deepcopy(state)
    result['version'] = VERSION
    result.setdefault('bundles', {})
    result.setdefault('previews', {})
    for task in result.get('tasks', {}).values():
        task.setdefault('schema_version', VERSION)
        task.setdefault('revision', 1)
        task.setdefault('planned', False)
        task.setdefault('inputs', [])
        task.setdefault('outputs', {})
        task.setdefault('skill_refs', [])
        task.setdefault('required_capabilities', [])
        task.setdefault('allowed_tools', ['*'])
        task.setdefault('context_scope', 'private' if task.get('private') else 'group_public_and_self')
        task.setdefault('budget', {'max_actions': None})
        task.setdefault('actions_used', 0)
        task.setdefault('imported_files', {})
    for run in result.get('runs', {}).values():
        run.setdefault('actions_used', run.get('model_calls', 0))
        run.setdefault('max_actions', 360)
        run.setdefault('budget_exhausted', False)
        run.setdefault('transport_attempts', 0)
        if version == 1:
            run['legacy_transport_attempts_unknown'] = True
    return result


def migrate(store, dry_run=True):
    original = store.load()
    result = upgrade(original)
    report = {'from_version': original.get('version', 1), 'to_version': VERSION,
              'tasks': len(result.get('tasks', {})), 'dry_run': dry_run,
              'changed': result != original, 'backup': None}
    report['requires_private_workspace_review'] = original.get('version', 1) == 1 and any(
        task.get('private') for task in original.get('tasks', {}).values())
    if dry_run or result == original:
        return report
    if report['requires_private_workspace_review']:
        raise ValueError('Legacy private tasks shared a role workspace; review/separate private files in a copy before migration')
    raw = store.state_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    backup = store.path / 'backups' / ('state-v1-' + digest + '.json')
    backup.parent.mkdir(exist_ok=True)
    if backup.exists() and backup.read_bytes() != raw:
        raise ValueError('Backup integrity conflict')
    backup.write_bytes(raw)
    # Runtime data stays in place; only the atomic state snapshot is replaced.
    dump(backup.with_suffix('.manifest.json'), {'sha256': digest, 'time': now(),
                                              'original_ids_preserved': True})
    store.save(result)
    report['backup'] = str(backup)
    return report
