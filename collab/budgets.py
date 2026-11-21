"""Hierarchical action reservations, including unbudgeted intermediary tasks."""


def lineage(state, task_id):
    result, seen = [], set()
    task = state['tasks'].get(task_id)
    starts = [task]
    if task and task.get('kind') == 'review':
        starts.append(state['tasks'].get(task.get('target')))
    for cursor in starts:
        while cursor and cursor['id'] not in seen:
            seen.add(cursor['id'])
            result.append(cursor)
            cursor = state['tasks'].get(cursor.get('parent'))
    return result


def available(state, run_id, task_id=None):
    run = state['runs'][run_id]
    tasks = {k: t for k, t in state['tasks'].items() if t['run'] == run_id}
    ancestors = {t['id'] for t in lineage(state, task_id)}

    def reserved(task):
        if task['status'] in {'completed', 'failed'}:
            return 0
        limit = task.get('budget', {}).get('max_actions')
        if task['id'] not in ancestors and limit is not None:
            return max(0, limit - task.get('actions_used', 0))
        return sum(reserved(t) for t in tasks.values() if t.get('parent') == task['id'])

    remaining = run['max_actions'] - run.get('actions_used', 0)
    remaining -= sum(reserved(t) for t in tasks.values() if not t.get('parent'))
    for key in ancestors:
        t = tasks[key]
        limit = t.get('budget', {}).get('max_actions')
        if limit is not None:
            remaining = min(remaining, limit - t.get('actions_used', 0) - reserved(t))
    return max(0, remaining)
