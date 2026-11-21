"""Task-local output checkpoints and bounded public-source failure handling."""
import hashlib
import re
import urllib.error

INSPECTION_BUDGET = 12
CHECKPOINT_TOOLS = {'files.write','files.append','files.replace','artifact.publish',
                    'artifact.publish_bundle','memory.working_set','evidence.record_claim'}
WRITTEN_OUTPUTS = CHECKPOINT_TOOLS - {'memory.working_set'} | {'evidence.record_claim'}


class ContextBudgetError(ValueError):
    """Local context cannot fit; no customer answer or model retry can fix it."""


def output_progress(state,task):
    """Replay content identities, so no-op writes/republication cannot buy reads.

    Legacy events without identities retain their historical success semantics.
    Byte changes are observable progress, never a quality or acceptance claim.
    """
    streak=0
    seen=set()
    outputs=[]
    for event in state['events']:
        if event.get('task')!=task['id'] or event['kind']!='tool_result':
            continue
        result=event.get('result')
        success=not isinstance(result,dict) or (result.get('ok') is not False and result.get('exit_code',0)==0)
        name=event.get('name')
        progress=success and name in WRITTEN_OUTPUTS
        if progress and isinstance(result,dict):
            identity=result.get('sha256')
            if identity:
                # Publication identities ignore the newly allocated artifact ID.
                key=(name if not name.startswith('files.') else 'file',
                     result.get('path') if name.startswith('files.') else None,identity)
                progress=result.get('changed') is not False and key not in seen
                seen.add(key)
            elif result.get('changed') is False:
                progress=False
        streak=0 if progress else streak+1
        if progress:
            outputs.append({'seq':event.get('seq'),'name':name,
                            **({k:result[k] for k in ('path','sha256','id','artifact_ids') if k in result}
                               if isinstance(result,dict) else {})})
    return streak,outputs[-8:]


def checkpoint(state,task):
    streak,outputs=output_progress(state,task)
    last_output=outputs[-1]['seq'] if outputs else 0
    forced=any(e.get('task')==task['id'] and e['kind']=='output_checkpoint_triggered'
               and e['seq']>(last_output or 0) for e in state['events'])
    required=task['kind']=='work' and task.get('planned',False) and (streak>=INSPECTION_BUDGET or forced)
    action={}
    repair=None
    for event in state['events']:
        if event.get('task')!=task['id'] or event.get('seq',0)<=(last_output or 0):
            continue
        if event['kind']=='agent_action':
            action=event.get('action',{})
        if (event['kind']=='action_error' and action.get('name')=='files.replace'
                and re.match(r'^Expected \d+ exact matches, found \d+;',event.get('error',''))):
            repair={'path':action['args']['path'],'conflict_seq':event['seq'],'max_limit':1500}
        result=event.get('result')
        if (repair and event['kind']=='tool_result' and event.get('name')=='files.read'
                and isinstance(result,dict) and result.get('path')==repair['path']):
            repair=None
    return {'required':required,'inspection_calls':streak,'limit':INSPECTION_BUDGET,
            'repair_read':repair if required else None,
            'instruction':'Write a small implementation increment or an evidence-attributed partial findings document before further inspection. State missing evidence and blockers; this checkpoint does not satisfy final acceptance. Alternatively ask a material clarification. Original criteria and budget are unchanged.'}


def checkpoint_access(task,name,args):
    if name!='files.read' or not task.get('output_checkpoint',{}).get('required'):
        return True
    repair=task['output_checkpoint'].get('repair_read')
    return bool(repair and args.get('path')==repair['path'] and 1<=args.get('limit',0)<=1500)


def public_source_call(state,task,name,args,execute):
    if name not in {'web.fetch','web.fetch_document'}:
        return execute()
    url=args['url']
    scope=task['id'] if task.get('private') else task['run']
    key=hashlib.sha256((scope+'\n'+url).encode()).hexdigest()
    failures=state.setdefault('source_failures',{})
    old=failures.get(key)
    if old and old['attempts']>=old['attempt_limit']:
        return {**old['result'],'cached_failure':True}
    try:
        result=execute()
        failures.pop(key,None)
        return result
    except (urllib.error.URLError,TimeoutError,ConnectionError) as exc:
        if isinstance(exc,urllib.error.HTTPError):
            if exc.code not in {403,404,408,410,429,500,502,503,504}:
                raise
            permanent=exc.code in {403,404,410}
            reason='HTTP '+str(exc.code)
        else:
            permanent=False
            reason=type(exc).__name__
    except ValueError as exc:
        status=re.fullmatch(r'Public HTTPS status (403|404|408|410|429|500|502|503|504)',str(exc))
        if str(exc)=='Public source exceeds byte limit':
            permanent=True
            reason=str(exc)
        elif status:
            permanent=int(status[1]) in {403,404,410}
            reason='HTTP '+status[1]
        else:
            raise
    except RuntimeError as exc:
        if not re.fullmatch(r'Verified public HTTPS download failed \(curl exit (6|7|28|35|60)\)',str(exc)):
            raise
        permanent=False
        reason=str(exc)
    attempts=old['attempts']+1 if old else 1
    result={'ok':False,'url':url,'error_category':'source_unavailable','reason':reason,
            'attempts':attempts,'attempt_limit':1 if permanent else 2,
            'instruction':'This source was not read or verified. Choose an accessible alternative, narrow the URL, or write the evidence gap in a draft. Do not repeat a blocked URL; no protection or size limit is bypassed.'}
    failures[key]={'task':task['id'],'run':task['run'],'private':bool(task.get('private')),
                   'attempts':attempts,'attempt_limit':result['attempt_limit'],'result':result}
    return result
