import json
import hashlib
from datetime import datetime
from pathlib import Path
from .store import now, dump
from .tools import safe_path


def seconds(start, end):
    return max(0., (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def metrics(state):
    rows = []
    for run in state['runs'].values():
        if run.get('private'):
            continue
        end = run.get('finished_at') or now()
        tasks = [t for t in state['tasks'].values() if t['run'] == run['id'] and t['kind'] == 'work' and t['parent']]
        intervals = []
        for request in state['requests'].values():
            if state['tasks'][request['task']]['run'] == run['id']:
                intervals.append((request['created_at'], request.get('answered_at', end)))
        merged = []
        for start, stop in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(stop, merged[-1][1]))
            else:
                merged.append((start, stop))
        wait = sum(seconds(a, b) for a, b in merged)
        completed = sum(t['status'] == 'completed' for t in tasks)
        rows.append({'run_id': run['id'], 'status': run['status'],
                'success': run['status'] == 'completed' and not run['fatal_error'],
                'duration_seconds': round(seconds(run['started_at'], end), 3),
                'human_wait_seconds': round(wait, 3), 'subtasks_total': len(tasks),
                'subtasks_completed': completed,
                'subtask_completion_rate': completed / len(tasks) if tasks else None,
                'fatal_error': run['fatal_error'], 'model_calls': run['model_calls'],
                'actions_used': run.get('actions_used', run['model_calls']),
                'max_actions': run.get('max_actions'),
                'budget_exhausted': run.get('budget_exhausted', False),
                'reported_tokens': run['tokens'], 'provider': run['provider'], 'model': run['model'],
                'memory_before': run['memory_before'], 'memory_after': run.get('memory_after', {})})
    return {'runs': rows, 'task_completion_rate': sum(r['success'] for r in rows) / len(rows) if rows else None,
            'measurement': 'Overall success requires independent review and human acceptance. Unfinished runs count as not completed.'}


def export(store, state):
    output = store.path / 'export'
    output.mkdir(exist_ok=True)
    dump(output / 'metrics.json', metrics(state))
    dump(output / 'sources.json', state['sources'])
    dump(output / 'state.json', state)
    with (output / 'dialogue.jsonl').open('w', encoding='utf-8') as handle:
        for event in state['events']:
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')
    lines = ['# Complete dialogue and execution trace', '', 'Includes private messages; share only with authorized evaluators.', '']
    for event in state['events']:
        lines += [f"## {event['seq']} · {event['time']} · {event['kind']}", '', '```json',
                  json.dumps(event, ensure_ascii=False, indent=2), '```', '']
    (output / 'dialogue.md').write_text('\n'.join(lines), encoding='utf-8')
    artifacts = output / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    for item in state['artifacts'].values():
        data = (store.path / 'artifacts' / item['sha256']).read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Artifact integrity check failed during export')
        destination = artifacts / item['id'] / Path(item['path']).name
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(data)
    for run in state['runs'].values():
        if run.get('private'):
            continue
        task = state['tasks'][run['root']]
        selected = task.get('submission', {}).get('artifacts', [])
        if not selected:
            continue
        delivery = output / 'delivery' / run['id']
        delivery.mkdir(parents=True, exist_ok=True)
        manifest = {'run': run['id'], 'human_accepted': run['status'] == 'completed', 'files': []}
        for artifact_id in selected:
            item = state['artifacts'][artifact_id]
            destination = safe_path(delivery, item['path'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((store.path / 'artifacts' / item['sha256']).read_bytes())
            manifest['files'].append({'path': item['path'], 'id': artifact_id, 'sha256': item['sha256']})
        dump(delivery / 'delivery_manifest.json', manifest)
    report = metrics(state)
    lines = ['# Performance report', '', 'Success is human-accepted delivery after peer review; this is not an official instructor score.', '',
             '| Run | Status | Success | Elapsed seconds | Human wait | Subtasks | Actions | Budget exhausted |',
             '|---|---|---|---|---|---|---|---|']
    for row in report['runs']:
        lines.append(f"| {row['run_id']} | {row['status']} | {row['success']} | {row['duration_seconds']} | {row['human_wait_seconds']} | {row['subtasks_completed']}/{row['subtasks_total']} | {row['actions_used']}/{row['max_actions']} | {row['budget_exhausted']} |")
    lines += ['', '## Failures and review feedback', '']
    for event in state['events']:
        if event['kind'] in {'action_error', 'model_error', 'review_result'}:
            lines += [f"- Event {event['seq']}: {json.dumps(event, ensure_ascii=False)}"]
    lines += ['', '## Memory evolution', '', 'Before/after SHA256 snapshots are recorded per run in metrics.json.',
              'Lessons live in agents/<id>/memory.jsonl with outcome labels; peer approval is not objective truth.', '',
              '## Limits', '', 'No inference about model quality is possible from offline harness tests.',
              'The action budget is persisted per run; resuming cannot reset it.',
              'Reported token totals cover only API usage fields returned by completed calls, not failed requests/retries.',
              'Human wait is the union of pending-request intervals; it can overlap productive work by other agents.']
    (output / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return output
