"""Run the original six tasks in isolated groups; pause for operator evidence.

This is an integration test driver, not another production execution command.
It always delegates execution to Engine.run (the /resume controller). It does
not script customer replies, approve human acceptance, or discard failed runs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import shutil

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from collab.cli import locked,observe
from collab.config import Config
from collab.engine import Engine
from collab.llm import Client
from collab.tools import Tools
from collab.store import Store,dump,now
from collab.scenarios import discover,load_scenario
from collab.selftest import answer_clarification,record_verdict
from collab.report import export

GROUPS=('software','newsroom','fund')


def ready(run):
    return run['status']=='test_passed' or (run['status']=='completed' and run.get('baseline_inherited')
        and run.get('baseline_reverification',{}).get('passed') is True)


def predecessor_ready(run,allow_failed=False):
    return ready(run) or (allow_failed and run['status']=='failed')


def skip_finished(run,index,allow_failed=False):
    return ready(run) or (index==0 and allow_failed and run['status']=='failed')


def source_hashes():
    paths=[p for root in ('collab','NLP_Mid_Test','scripts','tests') for p in (BASE/root).rglob('*')
           if p.is_file() and '__pycache__' not in p.parts]
    paths += [BASE/p for p in ('Dockerfile.sandbox','Dockerfile.browser','Dockerfile.document','requirements.browser.txt')]
    return {p.relative_to(BASE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def report(runtime):
    rows=[]
    for group in GROUPS:
        file=runtime/group/'state.json'
        state=json.loads(file.read_text(encoding='utf-8')) if file.exists() else None
        for number in (1,2):
            candidates=[] if not state else [r for r in state['runs'].values() if not r.get('private') and
                state['tasks'][r['root']]['description']==state['scenario']['tasks'][number-1]['text']]
            run=candidates[0] if candidates else None
            rows.append({'group':group,'task':number,'run':run['id'] if run else None,
                         'status':run['status'] if run else 'not_started',
                         'automated_test_passed':bool(run and ready(run)),
                         'human_accepted':bool(run and run['status']=='completed'),
                         'inherited_baseline':bool(run and run.get('baseline_inherited')),
                         'baseline_reverified':bool(run and run.get('baseline_reverification',{}).get('passed')),
                         'predecessor_status':run.get('predecessor_status') if run else None,
                         'failure_reason':run.get('test_failure_reason') if run else None,
                         'actions_used':run.get('actions_used') if run else None,
                         'reported_tokens':run.get('tokens') if run else None,
                         'implementation_epochs':run.get('implementation_epochs',[]) if run else [],
                         'pending_requests':[] if not run else [r['id'] for r in state['requests'].values() if r['status']=='pending' and state['tasks'][r['task']]['run']==run['id']]})
    result={'time':now(),'evaluation_mode':'automated_selftest','fixed_denominator':6,
            'automated_passes':sum(r['automated_test_passed'] for r in rows),
            'human_accepted':sum(r['human_accepted'] for r in rows),'tasks':rows,
            'limits':'Operator self-tests are separate from human acceptance and official course grading. Failures/unfinished tasks stay in the denominator; provider monetary cost may be unknown.'}
    history_file=runtime/'code-history.json'
    history=json.loads(history_file.read_text(encoding='utf-8')) if history_file.exists() else []
    result['code_history']=history
    result['fixed_implementation']=len(history)<=1
    baseline_file=runtime/'baseline.json'
    result['baseline']=json.loads(baseline_file.read_text(encoding='utf-8')) if baseline_file.exists() else None
    result['measurement']='Functional continuity: explicitly reverified accepted baseline may count; new tasks need independent review and protected operator checks. This is not a cold-start or official grading result.' if result['baseline'] else 'Development self-test from original tasks; implementation epochs and failures retained.'
    dump(runtime/'selftest-report.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',required=True,type=Path)
    parser.add_argument('--group',choices=GROUPS)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--verdict',type=Path)
    parser.add_argument('--answer-request')
    parser.add_argument('--answer')
    parser.add_argument('--continue-after-failure',action='store_true',
                        help='Explicitly test task 2 in the same group after a recorded task-1 failure; retain failure and label degraded continuity')
    parser.add_argument('--adopt-code-change',metavar='REASON',help='Explicit development repair: retain the run/budget and label mixed implementation epochs; not a fixed-version evaluation')
    args=parser.parse_args()
    runtime=args.runtime.resolve()
    if runtime in {(BASE/'runtime').resolve(),(BASE/'runtime/development_m2').resolve()}:
        raise ValueError('Use a fresh, dedicated self-test runtime; never a production/development group')
    runtime.mkdir(parents=True,exist_ok=True)
    config=Config.from_env()
    frozen=runtime/'frozen-manifest.json'
    hashes=source_hashes()
    configuration={'provider':config.provider,'model':config.model,'max_actions_per_task':config.max_run_actions,
                   'input_budget_tokens':config.input_budget_tokens,'memory_enabled':config.memory_enabled,
                   'compaction_enabled':config.compaction_enabled,
                   'max_output_tokens':config.max_output_tokens}
    history_file=runtime/'code-history.json'
    history=json.loads(history_file.read_text(encoding='utf-8')) if history_file.exists() else []
    adopted=False
    images={}
    for image in (config.image,config.browser_image,config.document_image):
        inspected=subprocess.run(['docker','image','inspect','--format','{{.Id}}',image],capture_output=True,text=True,timeout=15,check=True)
        images[image]=inspected.stdout.strip()
    if frozen.exists():
        original=json.loads(frozen.read_text(encoding='utf-8'))
        if original['configuration']!=configuration or original['images']!=images:
            raise ValueError('Model/configuration/images changed: create a new test runtime')
        if original['source_hashes']!=hashes:
            if not args.adopt_code_change:
                raise ValueError('Implementation changed. Use a fresh runtime for fixed-version evaluation, or explicitly --adopt-code-change REASON for labelled development self-tests retaining the same budget/history.')
            if not history:
                history=[{'epoch':0,'manifest_sha256':hashlib.sha256(frozen.read_bytes()).hexdigest(),'source_snapshot':'frozen-source','reason':'Original frozen implementation'}]
                dump(runtime/'code-epochs/epoch-0/manifest.json',original)
            epoch=len(history)
            snapshot=runtime/'code-epochs'/('epoch-'+str(epoch))/'source'
            for name,sha in hashes.items():
                source=BASE/name
                if hashlib.sha256(source.read_bytes()).hexdigest()!=sha:
                    raise ValueError('Source changed while archiving development repair')
                target=snapshot/name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,target)
            current={**original,'time':now(),'source_hashes':hashes,'source_snapshot':snapshot.relative_to(runtime).as_posix(),'epoch':epoch,'development_repair_reason':args.adopt_code_change}
            dump(runtime/'code-epochs'/('epoch-'+str(epoch))/'manifest.json',current)
            dump(frozen,current)
            history.append({'epoch':epoch,'manifest_sha256':hashlib.sha256(frozen.read_bytes()).hexdigest(),'source_snapshot':current['source_snapshot'],'reason':args.adopt_code_change,'time':now()})
            dump(history_file,history)
            adopted=True
    else:
        baseline_file=runtime/'baseline.json'
        baseline=json.loads(baseline_file.read_text(encoding='utf-8')) if baseline_file.exists() else None
        dump(frozen,{'time':now(),'source_hashes':hashes,'configuration':configuration,'images':images,
                     'initial_memory':'inherited accepted software checkpoint; other groups empty' if baseline else 'empty per group',
                     'baseline':baseline,'evaluation_mode':'automated_selftest','secrets':'excluded'})
        for name,sha in hashes.items():
            source=BASE/name
            if hashlib.sha256(source.read_bytes()).hexdigest()!=sha:
                raise ValueError('Source changed while creating the frozen snapshot')
            target=runtime/'frozen-source'/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
        history=[{'epoch':0,'manifest_sha256':hashlib.sha256(frozen.read_bytes()).hexdigest(),'source_snapshot':'frozen-source','reason':'Original frozen implementation','time':now()}]
        dump(history_file,history)
    paths=discover(BASE/'NLP_Mid_Test')
    if len(paths)!=3:
        raise ValueError('Expected the original three scenarios')
    for group,path in zip(GROUPS,paths):
        if args.group and group!=args.group:
            continue
        store=Store(runtime,group)
        with locked(store):
            if not store.state_path.exists():
                Engine.create(store,load_scenario(path))
                state=store.load()
                state['evaluation_mode']='automated_selftest'
                store.event(state,'operator_test_message',sender='test_operator',to='group',
                    text='ISOLATED AUTOMATED SELF-TEST. Original tasks and role identities are unchanged. Produce real deliverables with independent verification; this is not a real customer acceptance. Ask material questions as they arise; the operator may supply explicitly labelled simulation assumptions. No external publication/push/trades, no preview leases; use browser.verify. Preserve prior outputs for task 2. Use versioned workflow skills and structured source/claim/dataset tools where applicable. Follow the persistent budget. Never fabricate successful checks or missing evidence.')
            if adopted:
                state=store.load()
                for run in state['runs'].values():
                    if run['status']=='running':
                        run.setdefault('implementation_epochs',[0]).append(history[-1]['epoch'])
                store.event(state,'operator_test_message',sender='test_operator',to='group',
                    purpose='platform_maintenance',
                    text='Observed platform maintenance (not a human reply): '+args.adopt_code_change+'. Existing attempts, evidence, budgets and source versions are retained; this is an explicitly mixed-implementation development self-test.')
    if args.prepare_only:
        print(json.dumps(report(runtime),ensure_ascii=False))
        return 0
    for group in GROUPS:
        if args.group and group!=args.group:
            continue
        store=Store(runtime,group)
        with locked(store):
            engine=Engine(store,config,Client(config),Tools(store,config),observer=observe)
            if args.answer_request:
                if not args.group or not args.answer:
                    raise ValueError('A test clarification answer needs --group, --answer-request and --answer')
                answer_clarification(engine,args.answer_request,args.answer)
            if args.verdict:
                if not args.group:
                    raise ValueError('--verdict needs --group')
                receipt=json.loads(args.verdict.read_text(encoding='utf-8'))
                record_verdict(engine,receipt['request_id'],args.verdict)
            for index in (0,1):
                text=engine.state['scenario']['tasks'][index]['text']
                runs=[r for r in engine.state['runs'].values() if engine.state['tasks'][r['root']]['description']==text and not r.get('private')]
                if runs and skip_finished(runs[0],index,args.continue_after_failure):
                    continue
                if index and not any(predecessor_ready(r,args.continue_after_failure) and engine.state['tasks'][r['root']]['description']==engine.state['scenario']['tasks'][0]['text'] for r in engine.state['runs'].values()):
                    break
                if not runs:
                    engine.submit(text)
                elif runs[0]['status']!='running':
                    break
                active=next(r for r in engine.state['runs'].values() if r['status']=='running' and engine.state['tasks'][r['root']]['description']==text)
                if index:
                    previous=next(r for r in engine.state['runs'].values() if engine.state['tasks'][r['root']]['description']==engine.state['scenario']['tasks'][0]['text'])
                    if previous['status']=='failed' and 'predecessor_status' not in active:
                        active.update(predecessor_status='failed',continuity_degraded=True)
                        engine.event('operator_test_message',sender='test_operator',to='group',
                            text='EXPLICIT DEGRADED CONTINUITY TEST: Original task 1 failed and remains failed. Test original task 2 in the same group using only real available history, sources and artifacts. No prior acceptance or missing deliverable is implied. Report inherited limitations honestly.')
                epoch=history[-1]['epoch'] if history else 0
                epochs=active.setdefault('implementation_epochs',[])
                if not epochs or epochs[-1]!=epoch:
                    epochs.append(epoch)
                store.event(engine.state,'selftest_execution_launch',task=active['root'],
                    implementation_epoch=epoch,manifest_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest())
                engine.run(continuous=True)
                export(store,engine.state)
                pending=[r for r in engine.state['requests'].values() if r['status']=='pending']
                print(json.dumps({'group':group,'task':index+1,'execution':engine.state['execution'],'pending':pending},ensure_ascii=False),flush=True)
                report(runtime)
                return 0
    print(json.dumps(report(runtime),ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
