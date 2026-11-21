"""Explicit operator self-test gates, separate from real human acceptance.

Not exposed as an agent tool/action. A test can unlock its next original task
after independently recorded checks, but can never increment human success.
"""
from copy import deepcopy
import hashlib
import json
from .store import now


def require_mode(engine):
    if engine.state.get('evaluation_mode')!='automated_selftest':
        raise ValueError('Test gates are only available in explicitly isolated self-test groups')


def answer_clarification(engine,request_id,text):
    require_mode(engine)
    request=engine.state['requests'][request_id]
    if request['status']!='pending' or request['kind']!='clarification' or not text.strip():
        raise ValueError('Expected a pending test clarification and explicit operator assumptions')
    task=engine.state['tasks'][request['task']]
    previous_errors=task.get('errors',0)
    maintenance=request.get('question','').startswith('Repeated agent/tool errors.')
    resolved=[e['seq'] for e in engine.state['events']
              if e.get('task')==task['id'] and e['kind']=='action_error'][-previous_errors:] if maintenance and previous_errors else []
    task['errors']=0
    task['repeated_actions']=0
    task['recent_result_signatures']=[]
    request.update(status='test_answered',answered_at=now(),answer=text,
                   answer_authority='test_operator_assumption',approved=None)
    engine.event('operator_test_message',sender='test_operator',to=task['owner'] if task.get('private') else 'group',
                 task=task['id'],text='SELF-TEST ASSUMPTION, NOT A HUMAN REPLY: '+text,request=request_id,
                 purpose='platform_maintenance' if request.get('question','').startswith('Repeated agent/tool errors.') else 'test_assumption',
                 resolved_error_seqs=resolved,
                 previous_error_episode=previous_errors,budget_refunded=False)
    task['status']='running'
    engine.enqueue(task['id'])
    engine.store.save(engine.state)


def record_verdict(engine,request_id,receipt_path):
    require_mode(engine)
    state=engine.state
    request=state['requests'][request_id]
    if request['kind']!='acceptance' or request['status']!='pending':
        raise ValueError('Expected pending draft delivery acceptance')
    task=state['tasks'][request['task']]
    if task['status']!='waiting_for_human':
        raise ValueError('Delivery is no longer waiting for acceptance')
    reviewer=state['tasks'].get(task.get('review_task'))
    if not reviewer or reviewer['status']!='completed':
        raise ValueError('Independent delivery review must complete before operator tests')
    path=receipt_path.resolve()
    root=(engine.store.path/'selftest-evidence').resolve()
    if not path.is_relative_to(root) or path.suffix!='.json' or not path.is_file() or path.stat().st_size>256000:
        raise ValueError('Operator receipt must be in the protected selftest-evidence directory')
    raw=path.read_bytes()
    receipt=json.loads(raw)
    selected={a:state['artifacts'][a]['sha256'] for a in request['artifacts']}
    if receipt.get('artifact_hashes')!=selected or receipt.get('request_id')!=request_id:
        raise ValueError('Operator test receipt targets a different delivery version')
    checks=receipt.get('checks')
    if not isinstance(checks,list) or not checks or any(
        not isinstance(c,dict) or not isinstance(c.get('passed'),bool) or not c.get('name') or not c.get('evidence') for c in checks):
        raise ValueError('Operator checks need explicit booleans, names and evidence')
    if not isinstance(receipt.get('evidence_files'),list) or not receipt['evidence_files']:
        raise ValueError('At least one protected independent operator evidence file is required')
    for evidence in receipt['evidence_files']:
        file=(root/evidence['path']).resolve()
        if not file.is_relative_to(root) or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=evidence['sha256']:
            raise ValueError('Operator evidence file missing or changed')
    engine.tools.validate_artifacts(state,request['artifacts'])
    passed=all(c['passed'] for c in checks)
    engine.event('operator_test_verdict',task=task['id'],request=request_id,passed=passed,
                 receipt_sha256=hashlib.sha256(raw).hexdigest(),receipt=str(path.relative_to(engine.store.path)),
                 checks=deepcopy(checks),human_acceptance=False)
    if not passed:
        engine.invalidate_delivery(task['run'],'Operator self-test found unmet conditions: '+
                                   '; '.join(c['name'] for c in checks if not c['passed']),
                                   [str(path.relative_to(engine.store.path))])
        return False
    request.update(status='test_evaluated',answered_at=now(),test_passed=True,
                   verdict_authority='operator_automation',approved=None)
    task['completion_authority']='automated_test'
    engine.complete(task)
    engine.end_run(task['run'],'test_passed')
    engine.store.save(state)
    return True
