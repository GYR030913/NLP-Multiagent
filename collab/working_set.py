"""Deterministic short-term memory and bounded model input, never a proof source."""
from copy import deepcopy
import hashlib
import json
from .context import visible_event
from .store import now
from .recovery import ContextBudgetError


def estimate(value):
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    return (len(text.encode('utf-8'))+1)//2


def compact(value, limit=2000):
    if isinstance(value,str) and len(value)>limit:
        return {'head':value[:limit//4], 'tail':value[-3*limit//4:],
                'truncated':True, 'characters':len(value),
                'instruction':'Omitted middle: use files.read/evidence.read_event/evidence.read_source with limit<=1500 and advancing offsets. Do not repeat an oversized read.'}
    if isinstance(value,dict):
        if value.get('kind')=='tool_result' and 'result' in value:
            result=compact(value['result'],limit)
            if len(json.dumps(result,ensure_ascii=False))>2*limit:
                serialized=json.dumps(value['result'],ensure_ascii=False)
                result={'truncated':True,'serialized_head':serialized[:limit//4],
                        'serialized_tail':serialized[-3*limit//4:],'original_characters':len(serialized),
                        'read_ref':{'tool':'evidence.read_event','seq':value['seq'],'limit':1500},
                        'note':'Structured result exceeds context block budget; original result is unchanged. Page the original event for missing facts.'}
            return {**{k:compact(v,limit) for k,v in value.items() if k not in {'result','verification_snapshot','envelope'}},'result':result}
        if len(value)>80:
            return {'items_preview':{k:compact(v,limit) for k,v in list(value.items())[:40]},
                    'truncated':True,'total_keys':len(value),'instruction':'Read the original event with pagination.'}
        return {k:compact(v,limit) for k,v in value.items() if k not in {'verification_snapshot','envelope'}}
    if isinstance(value,list):
        if len(value)>80:
            return {'items_preview':[compact(v,limit) for v in value[:40]],'truncated':True,'total_items':len(value)}
        return [compact(v,limit) for v in value]
    return value


def verification_ledger(state,task):
    """Latest executions per actual input; reading more files cannot evict them."""
    import re
    last_action={}
    entries={}
    for event in state['events']:
        if event.get('task')!=task['id']:
            continue
        if event['kind']=='agent_action' and event['action'].get('type')=='tool':
            last_action=event['action']
        if event['kind']!='tool_result' or event.get('name') not in {'code.run','browser.verify','ui.check','artifact.validate_bundle','data.analyze'}:
            continue
        result=event['result']
        if not isinstance(result,dict):
            continue
        args=last_action.get('args',{}) if last_action.get('name')==event['name'] else {}
        identity=hashlib.sha256(json.dumps([event['name'],args],sort_keys=True).encode()).hexdigest()
        output=str(result.get('output',''))
        test_like=bool(re.search(r'(?m)^\[PASS\]|^\[FAIL\]|Ran \d+ tests|ALL TESTS PASSED|FAILED \(',output))
        priority=3 if event['name']=='browser.verify' else (2 if test_like or event['name']!='code.run' else 1)
        row={'seq':event['seq'],'name':event['name'],'exit_code':result.get('exit_code'),
             'execution_cwd':result.get('cwd'),
             'execution_ok':result.get('exit_code',0)==0 and result.get('ok') is not False and not result.get('validation_warnings'),
             'validation_warnings':result.get('validation_warnings',[]),'input_digest':identity,
             'input_hint':json.dumps(args,ensure_ascii=False)[:400],'output_tail':output[-600:],
             'subject_digest':result.get('subject_digest'),
             'note':'Execution observation, not automatic criterion approval.'}
        snapshot=event.get('verification_snapshot')
        if snapshot is not None:
            row['source_snapshot_digest']=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()
            row['needs_recheck']=any(
                later.get('task')==task['id'] and later['kind']=='tool_result'
                and later.get('name') in {'files.write','files.append','files.replace'}
                and isinstance(later.get('result'),dict)
                and later['result'].get('path','').endswith(('.py','.js','.html','.css','.sql'))
                and later['result'].get('sha256')
                and snapshot.get(later['result'].get('path'))!=later['result']['sha256']
                for later in state['events'] if later['seq']>event['seq'])
        entries[identity]=(priority,event['seq'],row)
    selected=sorted(entries.values(),key=lambda x:x[:2],reverse=True)[:6]
    return [x[2] for x in sorted(selected,key=lambda x:x[1])]


def reading_ledger(state,task):
    """Retain what was opened, without treating reading as understanding."""
    action={}
    files={}
    quotes=[]
    import re
    for event in state['events']:
        if event.get('task')!=task['id']:
            continue
        if event['kind']=='agent_action':
            action=event.get('action',{})
        if event['kind']!='tool_result':
            continue
        result=event.get('result')
        if (event.get('name') in {'files.write','files.append','files.replace'} and isinstance(result,dict)
                and result.get('changed',True) and result.get('ok') is not False):
            files.pop(result.get('path'),None)
        if event.get('name')=='files.read' and action.get('name')=='files.read':
            args=action.get('args',{});path=args.get('path','')
            text=result if isinstance(result,str) else str(result.get('content','')) if isinstance(result,dict) else ''
            offset=args.get('offset',0)
            symbols=re.findall(r'(?m)^\s*(?:async\s+)?(?:def|class|function)\s+([\w.]+)',text)
            signatures=re.findall(r'(?m)^[ \t]*(?:(?:async[ \t]+)?def|class|function)[ \t]+[^\r\n]{1,180}',text)
            identity=(result.get('text_sha256') if isinstance(result,dict) else
                      hashlib.sha256(text.encode('utf-8')).hexdigest() if not {'offset','limit'}&set(args) else None)
            previous=files.pop(path,{})
            if identity and previous.get('text_sha256') and identity!=previous['text_sha256']:
                previous={}
            known=list(dict.fromkeys(previous.get('signatures',[])+[line.strip() for line in signatures]))
            # Public interfaces first; these are source observations, not API
            # compatibility guarantees. Pages from one version accumulate.
            known.sort(key=lambda line:bool(re.match(r'(?:async )?def _',line)))
            files[path]={'path':path,'last_seq':event['seq'],'last_range':[offset,offset+len(text)],
                         'symbols':list(dict.fromkeys(previous.get('symbols',[])+symbols))[:12],
                         'signatures':known[:8],'text_sha256':identity or previous.get('text_sha256'),
                         'page_sha256':hashlib.sha256(text.encode()).hexdigest()}
        if event.get('name')=='evidence.find_quote' and isinstance(result,dict):
            for match in result.get('matches',[])[:1]:
                quotes.append({'seq':event['seq'],**{k:match[k] for k in ('source_id','start','end','quote')},
                               'quote_truncated':len(match['quote'])>300})
                quotes[-1]['quote']=match['quote'][:300]
    sources=[]
    for sid,ranges in state.get('source_reads',{}).get(task['id'],{}).items():
        sources.append({'source_id':sid,'read_ranges':[list(row) for row in sorted(set(tuple(r) for r in ranges))[-8:]]})
    return {'files':list(files.values())[-8:],'sources':sources[-8:],'exact_quote_refs':quotes[-3:],
            'note':'Pages already opened in this task, not verified understanding. Reuse event/quote IDs; advance offsets for missing sections. Truncated quotes require the original event before citing.'}


def progress_summary(state,task):
    """Bounded observations for choosing the next action, never acceptance."""
    from .recovery import output_progress
    streak,outputs=output_progress(state,task)
    counts={}
    recent={}
    failure=None
    action={}
    failure_key=None
    for event in state['events']:
        if event.get('task')!=task['id']:
            continue
        if (event['kind']=='operator_test_message' and event.get('purpose')=='platform_maintenance'
                and failure and failure['seq'] in event.get('resolved_error_seqs',[])):
            failure=None
            failure_key=None
        if event['kind']=='agent_action':
            action=event.get('action',{})
        if event['kind']=='action_error':
            failure={'seq':event['seq'],'error':event.get('error','')[:500]}
            if action.get('name')=='files.replace' and event.get('error','').startswith('Expected '):
                failure.update(category='edit_conflict',path=action.get('args',{}).get('path'))
            failure_key=(action.get('name'),json.dumps(action.get('args',{}),sort_keys=True))
        if event['kind']!='tool_result':
            continue
        name=event.get('name','')
        counts[name]=counts.get(name,0)+1
        result=event.get('result')
        success=not isinstance(result,dict) or (result.get('ok') is not False and result.get('exit_code',0)==0)
        key=(name,json.dumps(action.get('args',{}),sort_keys=True))
        # An unrelated successful read does not resolve a failed test or action.
        if not success:
            failure={'seq':event['seq'],'name':name,'error':str(result.get('reason',result.get('output','Tool execution failed')))[-500:]}
            failure_key=key
        elif key==failure_key:
            failure=None
            failure_key=None
        elif (failure and failure.get('category')=='edit_conflict' and isinstance(result,dict)
              and name in {'files.write','files.replace','files.append'} and result.get('changed')
              and result.get('path')==failure.get('path')):
            failure=None
            failure_key=None
        text=result if isinstance(result,str) else json.dumps(result,ensure_ascii=False)
        row={'seq':event['seq'],'name':name,'preview':text[:350],
             'read_ref':{'tool':'evidence.read_event','seq':event['seq'],'limit':1500}}
        key=hashlib.sha256((name+text).encode()).hexdigest()
        recent.pop(key,None)
        recent[key]=row
    return {'tool_result_counts':counts,'tool_calls_since_written_output':streak,
            'recent_output_versions':outputs,
            'recent_distinct_observations':list(recent.values())[-3:],
            'unresolved_latest_error':failure,
            'error_note':'Latest failure remains until the same tool/arguments succeed or an explicit operator maintenance event resolves that exact error sequence. Original failures remain auditable. Unrelated reads/writes do not resolve it.',
            'next_step_guidance':('Reading has not produced a written output for at least 8 calls. Use the evidence already obtained to implement a small increment or write a sourced draft; then verify it. If a specific fact is still missing, identify it and use a targeted read or report the blocker. Do not repeat inventories or claim completion without evidence.'
                                  if streak>=8 and task['kind']=='work' else
                                  'Continue the assigned task using these attributed observations; successful tools do not imply acceptance.')}


def update(store,state,task):
    visible=[e for e in state['events'] if visible_event(state,e,task)]
    def maintenance(event):
        return event['kind']=='operator_test_message' and (
            event.get('purpose')=='platform_maintenance' or
            state.get('operator_event_classes',{}).get(str(event['seq']))=='platform_maintenance')
    human=[{'seq':e['seq'],'kind':e['kind'],'text':e.get('text',''),
            'approved':e.get('approved'),
            'authority':'test_operator_assumption' if e['kind']=='operator_test_message' else 'human'} for e in visible
           if e['kind'] in {'human_message','human_answer','operator_test_message'} and not maintenance(e)]
    maintenance_refs=[{'seq':e['seq'],'purpose':'platform_maintenance'} for e in visible if maintenance(e)]
    # These are exact confirmed inputs, not a model-written paraphrase. Preserve
    # negations, dates, currencies and corrections even outside the recent window.
    previous=task.get('working_set',{})
    assumptions=previous.get('assumptions',[])
    issues=previous.get('open_questions',[])
    if previous and (previous.get('task_revision')!=task.get('revision',1) or previous.get('inputs')!=task.get('inputs',[])):
        assumptions=[{**a,'needs_revalidation':True,'source_revision':previous.get('task_revision')} for a in assumptions]
    pending=[{'id':r['id'],'kind':r['kind'],'question':r.get('question'),
              'summary':r.get('summary'),'task':r['task']} for r in state['requests'].values()
             if r['status']=='pending' and (not state['tasks'][r['task']].get('private') or r['task']==task['id'])]
    def delivery(node):
        submitted=node.get('submission',{})
        ids=submitted.get('artifacts',[])
        summary=submitted.get('summary','')
        return {'task_id':node['id'],'owner':node['owner'],'artifact_ids':ids,
                'bundle_ids':[b['id'] for b in state.get('bundles',{}).values()
                              if b['artifact_ids'] and set(b['artifact_ids'])<=set(ids)],
                'summary':summary[:600],'summary_truncated':len(summary)>600,
                'note':'Delivery metadata and attributed summary, not a new verification.'}
    dependency_deliveries=[delivery(state['tasks'][tid]) for tid in task.get('depends_on',[])
                           if state['tasks'][tid]['status']=='completed' and not state['tasks'][tid].get('private')]
    child_deliveries=[delivery(state['tasks'][tid]) for tid in task.get('children',[])
                      if state['tasks'][tid]['status']=='completed' and not state['tasks'][tid].get('private')]
    prior=[]
    if task['parent'] is None:
        for run in state['runs'].values():
            if run['id']==task['run'] or run['status'] not in {'completed','test_passed'} or run.get('private'):
                continue
            node=state['tasks'][run['root']]
            if not node.get('private'):
                prior.append({**delivery(node),'run_id':run['id'],'status':run['status'],
                              'human_accepted':run['status']=='completed'})
    value={'version':1,'task_id':task['id'],'task_revision':task.get('revision',1),
           'goal':task['description'],'criteria':task['criteria'],'confirmed_inputs':human,
           'maintenance_refs':maintenance_refs,
           'dependencies':[{'id':i,'status':state['tasks'][i]['status']} for i in task.get('depends_on',[])],
           'dependency_deliveries':dependency_deliveries,'child_deliveries':child_deliveries,'prior_deliveries':prior,
           'inputs':task.get('inputs',[]),'pending_human_requests':pending,
           'assumptions':assumptions,'open_questions':issues,
           'imported_sources':task.get('imported_files',{}),
           'verification_ledger':verification_ledger(state,task),
           'reading_ledger':reading_ledger(state,task),
           'progress':progress_summary(state,task),
           'recent_evidence_refs':[e['seq'] for e in visible if e['kind']=='tool_result'][-15:],
           'note':'Rebuildable summary; original events and immutable artifacts are the evidence.'}
    value['digest']=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    task['working_set']=value
    return value


def annotate(store,state,task,assumptions=None,open_questions=None):
    from .memory import protect_text
    for entries in (assumptions,open_questions):
        if entries is not None:
            if not isinstance(entries,list) or len(entries)>15:
                raise ValueError('Working-set annotations need at most 15 entries')
            for entry in entries:
                if not isinstance(entry,dict) or set(entry)!={'text','evidence'}:
                    raise ValueError('Annotations require text and evidence IDs')
                protect_text(entry['text'],1000)
                from .memory import Memory
                Memory(store,state).references(entry['evidence'],task)
    working=update(store,state,task)
    if assumptions is not None:
        working['assumptions']=deepcopy(assumptions)
    if open_questions is not None:
        working['open_questions']=deepcopy(open_questions)
    update(store,state,task)
    store.save(state)
    return task['working_set']


def assemble(store,state,task,context,budget=24000,system_text='',enabled=True):
    original_size=estimate({k:v for k,v in context.items() if k!='available_tools'})+estimate(system_text)
    working=update(store,state,task)
    value=deepcopy(context)
    value['working_set']=working
    if not enabled:
        total=estimate({k:v for k,v in value.items() if k!='available_tools'})+estimate(system_text)
        if total>budget-400:
            raise ContextBudgetError('Uncompressed context exceeds configured input budget; enable compaction or explicitly adjust the budget')
        trace={'strategy':'uncompressed-ablation','estimate_method':'UTF-8 bytes / 2 (estimated)',
               'estimated_tokens':total+200,'input_budget_tokens':budget,
               'retrieved_memory_ids':[m['id'] for m in value.get('memory',[])],
               'working_set_digest':working['digest'],'task_revision':task.get('revision',1)}
        value['context_trace']=trace
        task['last_context_trace']=trace
        store.save(state)
        return value
    # Keep one exact copy of the goal/criteria, with explicit references from
    # the context working set. Persisted working sets still contain full text.
    value['working_set']={k:v for k,v in working.items() if k not in {'goal','criteria'}}
    value['working_set'].update(goal_ref='current_task.description',criteria_ref='current_task.criteria')
    value['roles']={k:{'title':v['title']} for k,v in value['roles'].items()}
    def pack(rows,quota):
        kept=[]
        omitted=[]
        used=0
        for row in rows:
            cost=estimate(row)
            if used+cost<=quota:
                kept.append(row);used+=cost
            else:
                omitted.append(row['id'])
        return kept,omitted
    value['memory'],memory_overflow=pack(value.get('memory',[]),int(budget*.15))
    value['memory_candidates'],candidate_overflow=pack(value.get('memory_candidates',[]),int(budget*.05))
    value['memory_overflow_refs']={'guidance':memory_overflow,'candidates':candidate_overflow,
                                   'note':'Scope permitted but omitted for block budget; inspect before use. Stored records are unchanged.'}
    value['available_skills']=[{k:v for k,v in skill.items() if k in {
        'id','version','description','capabilities','required_tools','content_sha256'}}
        for skill in value.get('available_skills',[])]
    value['current_task']={k:v for k,v in task.items() if k not in {
        'working_set','invalidated_submissions','submission','imported_files','last_progress_key',
        'recent_result_signatures','last_context_trace'}}
    if value.get('review_target'):
        # The submission is evidence, not another set of instructions embedded
        # as an escaped JSON string inside the review goal.
        value['current_task']['description']=task['description'].split('\nSubmission:',1)[0]
        value['current_task']['submission_ref']='review_target'
    value['tasks']={k:{f:v[f] for f in ('id','owner','status','kind','parent','run','depends_on','children','revision') if f in v}
                    for k,v in value['tasks'].items()}
    value['artifacts']={k:{f:v[f] for f in ('id','owner','path','sha256') if f in v}
                        for k,v in value.get('artifacts',{}).items()}
    value['bundles']={k:{'id':k,'sha256':v['sha256'],'owner':v['owner'],
                         'version':v['payload']['version'],'artifact_ids':v['artifact_ids'],
                         'entrypoints':v['payload']['entrypoints'],'data_contract':v['payload'].get('data_contract',{})}
                      for k,v in value.get('bundles',{}).items()}
    original_events=value.get('recent_events',[])
    latest=next((event for event in reversed(original_events)
                 if event.get('kind')=='tool_result' and event.get('task')==task['id']),
                original_events[-1] if original_events else None)
    if latest:
        original_events=[event for event in original_events if event['seq']!=latest['seq']]+[latest]
    value['recent_events']=[compact(e,1500) for e in original_events]
    stable=[{k:v for k,v in row.items() if k in {'seq','name','exit_code','execution_ok','execution_cwd','validation_warnings','subject_digest','source_snapshot_digest','needs_recheck'}} for row in working['verification_ledger']]
    normal=[row for row in value.get('current_task_evidence_index',[]) if row['name'] not in {'code.run','browser.verify','ui.check','artifact.validate_bundle','data.analyze'}][-6:]
    value['current_task_evidence_index']=stable+normal
    latest_limit=16000
    if latest:
        # The next action needs the just-returned page/traceback, not another
        # preview of its beginning. Spend the remaining budget on it first.
        value['recent_events'][-1]=compact(latest,latest_limit)
    value['claims']=[{'id':c['id'],'text':c['text'][:300],
                      'source_ids':sorted({q['source_id'] for q in c['citations']}),
                      'as_of':c.get('as_of'),'supersedes':c.get('supersedes')}
                     for c in value.get('claims',[])]
    value['sources']=[{k:v for k,v in s.items() if k not in {'pages','text','query','content_ref'}} for s in value.get('sources',[])]
    value['recent_own_actions']=[compact(e,1200) for e in value.get('recent_own_actions',[])]
    for e in value.get('current_task_evidence_index',[]):
        if 'output_tail' in e:
            e['output_tail']=e['output_tail'][-500:]
    removed=0
    # Preserve the most recent result for the next action. Older results remain
    # addressable by event IDs; constraints never depend on this sliding window.
    def size():
        return estimate({k:v for k,v in value.items() if k!='available_tools'})+estimate(system_text)
    while size()>budget-600 and len(value['recent_events'])>1:
        value['recent_events'].pop(0)
        removed+=1
    if size()>budget-600:
        value['recent_own_actions']=value['recent_own_actions'][-3:]
        value['current_task_evidence_index']=value['current_task_evidence_index'][-10:]
    if size()>budget-600:
        value['recent_own_actions']=value['recent_own_actions'][-1:]
        value['sources']=[{k:s[k] for k in ('id','url','kind','sha256','retrieved_at','published_date') if k in s} for s in value.get('sources',[])]
    if size()>budget-600:
        # A plan action can repeat all criteria, and old artifact paths can
        # outweigh the new task. Keep their identities; page original events
        # or import the immutable bundle for detail. Never trim requirements.
        value['recent_own_actions']=[{'seq':row['seq'],
            'action_preview':compact(json.dumps(row.get('action',{}),ensure_ascii=False),500),
            'read_ref':{'tool':'evidence.read_event','seq':row['seq'],'limit':1500}}
            for row in value.get('recent_own_actions',[])]
        value['artifacts']={key:{field:item[field] for field in ('id','sha256') if field in item}
                            for key,item in value['artifacts'].items()}
    if size()>budget-600:
        selected=set()
        for key in ('dependency_deliveries','child_deliveries','prior_deliveries'):
            for delivery in working[key]: selected.update(delivery['artifact_ids'])
        if value.get('review_target'):
            selected.update(item['id'] for item in value['review_target']['artifacts'])
        selected.update(key for key,item in state['artifacts'].items()
                        if item.get('task') in state['tasks'] and state['tasks'][item['task']]['run']==task['run'])
        value['archived_artifact_ids']=[key for key in value['artifacts'] if key not in selected]
        value['artifact_registry_note']='Older artifact IDs remain importable via artifact.import; bundle hashes and source events remain authoritative.'
        value['artifacts']={key:item for key,item in value['artifacts'].items() if key in selected}
        value['working_set']['verification_ledger']=[{**row,'input_hint':row['input_hint'][:150],
            'output_tail':row['output_tail'][-200:]} for row in working['verification_ledger']]
    if size()>budget-600:
        # Shrink reproducible catalogs before the page just requested. IDs
        # remain addressable through import/read_source/list_claims.
        value['archived_task_ids']=[key for key,item in value['tasks'].items() if item['run']!=task['run']]
        value['tasks']={key:item for key,item in value['tasks'].items() if item['run']==task['run']}
        value['bundles']={key:{k:v for k,v in item.items() if k!='data_contract'} for key,item in value['bundles'].items()}
        value['sources']=[{k:item[k] for k in ('id','kind') if k in item} for item in value['sources']]
        value['claims']=[{k:item[k] for k in ('id','source_ids','as_of')} for item in value['claims']]
        candidate_overflow += [item['id'] for item in value['memory_candidates']]
        value['memory_candidates']=[]
        value['memory_overflow_refs']['candidates']=candidate_overflow
    while size()>budget-600 and latest and latest_limit>1500:
        latest_limit=max(1500,latest_limit//2)
        value['recent_events'][-1]=compact(latest,latest_limit)
    if size()>budget-600:
        progress=value['working_set']['progress']
        progress['recent_distinct_observations']=[{k:row[k] for k in ('seq','name','read_ref')}
                                                  for row in progress['recent_distinct_observations']]
    if size()>budget-600:
        # These ledgers are reproducible observations, not protected human
        # requirements. Keep identity/outcome/version and page original results
        # instead of failing because command previews and tails fill the budget.
        value['working_set']['verification_ledger']=[
            {**{k:v for k,v in row.items() if k in {
                'seq','name','exit_code','execution_ok','execution_cwd','validation_warnings',
                'subject_digest','source_snapshot_digest','needs_recheck'}},
             'read_ref':{'tool':'evidence.read_event','seq':row['seq'],'limit':1500}}
            for row in working['verification_ledger']]
        value['working_set']['reading_ledger']['files']=[
            {**{k:v for k,v in row.items() if k in {'path','last_seq','last_range','text_sha256'}},
             'signatures':row.get('signatures',[])[:2]}
            for row in working['reading_ledger']['files']]
    if size()>budget-600:
        # Do not pay twice for the same verification event under tight budgets.
        verification_ids={row['seq'] for row in value['working_set']['verification_ledger']}
        value['current_task_evidence_index']=[row for row in value['current_task_evidence_index']
                                              if row['seq'] not in verification_ids]
        value['verification_evidence_ref']='working_set.verification_ledger (original tool events remain pageable)'
    if size()>budget-600:
        value['working_set']['maintenance_refs']=[{'seq':row['seq']} for row in working['maintenance_refs']]
        value['working_set']['maintenance_refs_note']='Operator platform maintenance only; original events remain available.'
        progress=value['working_set']['progress']
        progress['older_output_refs']=[{'seq':row['seq'],'path':row.get('path')}
                                        for row in progress['recent_output_versions'][:-3]]
        progress['recent_output_versions']=progress['recent_output_versions'][-3:]
    if size()>budget-600:
        ledger=value['working_set']['reading_ledger']
        ledger['older_file_refs']=[{'path':row['path'],'seq':row['last_seq']} for row in ledger['files'][:-4]]
        ledger['files']=ledger['files'][-4:]
    total=size()
    if total>budget-400:
        raise ContextBudgetError('Context exceeds the input budget after reducing rebuildable previews. Original requirements are preserved; inspect block sizes or explicitly adjust the context budget.')
    trace={'strategy':'deterministic-scoped-v1','estimate_method':'UTF-8 bytes / 2 (estimated, not provider usage)',
           'input_budget_tokens':budget,'estimated_tokens':total+200,'dropped_old_event_previews':removed,
           'uncompressed_estimated_tokens':original_size,
           'latest_event_preview_limit':latest_limit,
           'retrieved_memory_ids':[m['id'] for m in value.get('memory',[])],
           'retrieved_candidate_ids':[m['id'] for m in value.get('memory_candidates',[])],
           'memory_omitted_for_budget':memory_overflow+candidate_overflow,
           'working_set_digest':working['digest'],'task_revision':task.get('revision',1)}
    value['context_trace']=trace
    task['last_context_trace']=trace
    store.save(state)
    return value
