from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import socket
import ssl
import urllib.error
from unittest.mock import patch

from collab.config import Config
from collab.engine import Engine
from collab.store import Store
from collab.tools import Tools
from collab.memory import Memory
from collab import data,research
from collab.working_set import update,annotate,estimate


class MemoryResearchTests(unittest.TestCase):
    def test_reading_pages_preserve_interfaces_until_source_version_changes(self):
        def read(result,**args):
            self.store.event(self.state,'agent_action',task=self.task['id'],
                action={'type':'tool','name':'files.read','args':{'path':'analytics.py',**args}})
            self.store.event(self.state,'tool_result',task=self.task['id'],name='files.read',result=result)
            return update(self.store,self.state,self.task)['reading_ledger']['files'][-1]
        source='def compute(rooms, bookings, start, end):\n    return {}\n'
        row=read(source)
        digest=hashlib.sha256(source.encode()).hexdigest()
        row=read({'path':'analytics.py','content':'    return {}\n','text_sha256':digest},offset=40,limit=1500)
        self.assertIn('def compute(rooms, bookings, start, end):',row['signatures'])
        self.assertIn('compute',row['symbols'])
        # Also detect changes made through code.run, where no file write event
        # exists. Observations from the old source version must not survive.
        row=read('def replacement(data):\n    return data\n')
        self.assertNotIn('compute',row['symbols'])
        self.assertEqual(row['signatures'],['def replacement(data):'])

    def test_failure_summary_retains_traceback_tail(self):
        self.store.event(self.state,'tool_result',task=self.task['id'],name='code.run',
            result={'exit_code':1,'output':('test_existing ... ok\n'*200)+'TypeError: missing end_date'})
        failure=update(self.store,self.state,self.task)['progress']['unresolved_latest_error']
        self.assertIn('TypeError: missing end_date',failure['error'])

    def test_only_attributed_maintenance_resolves_the_specific_error(self):
        error=self.store.event(self.state,'action_error',task=self.task['id'],error='Context overflow')
        def resolve(**fields):
            self.store.event(self.state,'operator_test_message',task=self.task['id'],**fields)
            return update(self.store,self.state,self.task)['progress']['unresolved_latest_error']
        self.assertIsNotNone(resolve(purpose='test_assumption',resolved_error_seqs=[error['seq']]))
        self.assertIsNotNone(resolve(purpose='platform_maintenance',resolved_error_seqs=[error['seq']+1]))
        self.assertIsNone(resolve(purpose='platform_maintenance',resolved_error_seqs=[error['seq']]))
        self.assertTrue(any(e['seq']==error['seq'] for e in self.state['events']))

    def test_failed_test_survives_reads_and_clears_only_on_same_check(self):
        def action(name,args,result):
            self.store.event(self.state,'agent_action',task=self.task['id'],action={'type':'tool','name':name,'args':args})
            self.store.event(self.state,'tool_result',task=self.task['id'],name=name,result=result)
        action('code.run',{'argv':['python','test.py']},{'exit_code':1,'output':'AssertionError'})
        action('files.read',{'path':'test.py'},'contents')
        action('code.run',{'argv':['python','other.py']},{'exit_code':0})
        self.assertIn('AssertionError',update(self.store,self.state,self.task)['progress']['unresolved_latest_error']['error'])
        action('code.run',{'argv':['python','test.py']},{'exit_code':0})
        self.assertIsNone(update(self.store,self.state,self.task)['progress']['unresolved_latest_error'])

    def test_source_edit_marks_old_verification_for_recheck(self):
        self.store.event(self.state,'tool_result',task=self.task['id'],name='code.run',
                         result={'exit_code':0},verification_snapshot={'app.py':'old'})
        self.store.event(self.state,'tool_result',task=self.task['id'],name='files.write',
                         result={'path':'app.py','sha256':'new','changed':True})
        working=update(self.store,self.state,self.task)
        self.assertTrue(working['verification_ledger'][0]['needs_recheck'])
        self.assertEqual(working['progress']['recent_output_versions'][0]['sha256'],'new')

    def test_reading_streak_preserves_observations_and_unresolved_error(self):
        for i in range(10):
            self.store.event(self.state,'tool_result',task=self.task['id'],agent='lead',name='files.read',result='Page '+str(i))
        self.store.event(self.state,'action_error',task=self.task['id'],error='Invalid search time range')
        progress=update(self.store,self.state,self.task)['progress']
        self.assertGreaterEqual(progress['tool_calls_since_written_output'],10)
        self.assertEqual(len(progress['recent_distinct_observations']),3)
        self.assertIn('Invalid search',progress['unresolved_latest_error']['error'])
        self.store.event(self.state,'tool_result',task=self.task['id'],agent='lead',name='files.write',result={'path':'draft.md'})
        progress=update(self.store,self.state,self.task)['progress']
        self.assertEqual(progress['tool_calls_since_written_output'],0)
        self.assertIn('Invalid search',progress['unresolved_latest_error']['error'])

    def test_identical_pdf_extraction_does_not_create_new_source_progress(self):
        from collab.documents import extract
        raw=b'%PDF-1.4 fixture';sha=hashlib.sha256(raw).hexdigest()
        folder=self.store.path/'documents';folder.mkdir();(folder/sha).write_bytes(raw)
        self.state['documents']={'d1':{'id':'d1','url':'https://example.org/paper.pdf','sha256':sha,
            'task':self.task['id'],'private':False,'retrieved_at':'2026-11-21T00:00:00Z'}}
        def parse(work,*args,**kwargs):
            (work/'extracted.json').write_text(json.dumps({'quality':'text','text':'A sufficiently long actual extracted passage.',
                'pages':[{'page':1,'start':0,'end':44}]}),encoding='utf-8')
            return {'exit_code':0,'output':''}
        with patch('collab.documents.Sandbox') as sandbox:
            sandbox.return_value.run.side_effect=parse
            first=extract(self.store,self.state,self.task,self.config,'d1')
            second=extract(self.store,self.state,self.task,self.config,'d1')
        self.assertEqual(first['id'],second['id'])
        self.assertEqual(len(self.state['sources']),1)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name,'software')
        self.config=Config('hkgai','https://example.org','test','')
        roles={r:{'title':r,'prompt':'Original role '+r} for r in ('lead','dev','qa','release')}
        Engine.create(self.store,{'roles':roles,'tasks':[]})
        self.tools=Tools(self.store,self.config)
        self.engine=Engine(self.store,self.config,None,self.tools)
        tid=self.engine.submit('Verify browser booking cancellation')
        self.task=self.engine.state['tasks'][tid]
        self.state=self.engine.state
        self.memory=Memory(self.store,self.state)
        self.ev=self.event(self.task)

    def event(self,task,**fields):
        return self.store.event(self.state,'tool_result',task=task['id'],agent=task['owner'],
            name='code.run',result={'exit_code':0,**fields})['seq']

    def proposal(self,task=None,**kwargs):
        return self.memory.propose(task or self.task,'Verify browser booking cancellation independently',
            'procedural',[self.ev],**kwargs)

    def active(self,**kwargs):
        row=self.proposal(**kwargs)
        return self.memory.transition(row['id'],'active','human',[],'Explicitly confirmed reusable method')

    def source(self,text='Original study finds false positives in student writing.',kind='fetched_text',task=None):
        task=task or self.task
        old=getattr(self.store,'_task_scope',None)
        self.store._task_scope=task
        try: row=self.tools.source(self.state,task['owner'],'https://example.org/study','Study',text,kind)
        finally: self.store._task_scope=old
        research.read_source(self.store,self.state,task,row['id'])
        return row

    def dataset(self,rows,name='sample',task=None,**kwargs):
        task=task or self.task
        p=self.store.workspace(task['owner'])/(name+'.json')
        p.write_text(json.dumps(rows),encoding='utf-8')
        return data.register(self.store,self.state,task,p.name,name,{'input_kind':'simulation'},**kwargs)

    def test_candidate_does_not_become_active_after_success(self):
        row=self.proposal(outcome='human_accepted')
        self.assertEqual(self.memory.search(self.task,'browser'),[])
        self.assertEqual(self.memory.search(self.task,'browser',include_candidates=True)[0]['status'],'candidate')

    def test_no_unrelated_memory_filler(self):
        self.active()
        self.assertEqual(self.memory.search(self.task,'semiconductor earnings'),[])

    def test_private_origin_is_filtered_before_keyword_matching(self):
        tid=self.engine.submit('Private browser booking detail',owner='lead',private=True)
        private=self.state['tasks'][tid]
        ev=self.event(private)
        row=self.memory.propose(private,'Private browser booking cancellation preference','preference',[ev])
        self.memory.transition(row['id'],'active','human',[],'Only within private conversation')
        self.assertEqual(self.memory.search(self.task,'private browser cancellation'),[])
        self.assertEqual(len(self.memory.search(private,'private browser')),1)
        with self.assertRaises(ValueError): self.memory.inspect(row['id'],self.task)

    def test_role_isolation_even_for_public_origin(self):
        row=self.active()
        other={**self.task,'id':'other','owner':'qa'}
        self.assertEqual(self.memory.search(other,'browser'),[])
        with self.assertRaises(ValueError): self.memory.inspect(row['id'],other)

    def test_explicit_public_share_and_private_share_approval(self):
        row=self.active()
        self.memory.share(self.task,row['id'])
        other={**self.task,'id':'other','owner':'qa'}
        self.assertEqual(len(self.memory.search(other,'browser')),1)
        private={**self.task,'private':True}
        ev=self.event(private)
        row=self.memory.propose(private,'Sensitive private preference','preference',[ev])
        with self.assertRaises(ValueError): self.memory.share(private,row['id'])
        shared=self.memory.share(private,row['id'],content='Check inputs before browser tests',private_approval=True)
        self.assertEqual(shared['support_refs'],[])
        self.assertIsNone(shared['task'])
        self.assertNotIn('Sensitive',json.dumps(shared))

    def test_expired_dynamic_facts_are_not_current_guidance(self):
        past=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        self.active(valid_until=past)
        self.assertEqual(self.memory.search(self.task,'browser'),[])

    def test_dynamic_fact_gets_default_ttl(self):
        row=self.proposal(scope={'dynamic':True})
        self.assertIsNotNone(row['valid_until'])
        with self.assertRaises(ValueError): self.proposal(valid_until='2026-01-01')

    def test_challenge_excludes_wrong_memory_and_retains_history(self):
        row=self.active()
        self.memory.challenge(self.task,row['id'],'Browser evidence contradicts prior rule',[self.ev])
        self.assertEqual(self.memory.search(self.task,'browser',include_candidates=True),[])
        self.assertEqual(self.memory.inspect(row['id'],self.task)['status'],'disputed')
        self.assertGreater(len(self.memory.operations()),1)

    def test_self_promotion_disallowed(self):
        row=self.proposal()
        with self.assertRaises(ValueError):
            self.memory.transition(row['id'],'peer_validated','lead',[self.ev],'Self says yes',task=self.task)

    def test_independent_memory_validation_requires_own_visible_evidence(self):
        row=self.proposal()
        self.memory.share(self.task,row['id'])
        tid=self.engine.new_task('qa','Review memory',['Validate rule'],'r1',self.task['id'])
        reviewer=self.state['tasks'][tid]
        ev=self.event(reviewer)
        validated=self.memory.transition(row['id'],'peer_validated','qa',[ev],'Independently reproduced',task=reviewer)
        self.assertEqual(validated['status'],'peer_validated')

    def test_dedup_does_not_double_count_support(self):
        one=self.proposal()
        two=self.proposal()
        self.assertEqual(one,two)
        self.assertEqual(len(self.memory.records()),1)

    def test_outbox_crash_before_append_replays_once(self):
        record=self.proposal()
        record={**record,'version':2,'status':'disputed'}
        with self.assertRaises(RuntimeError): self.memory.write(record,'crash1',fault='after_outbox')
        restarted=Memory(self.store,self.store.load())
        restarted.flush()
        self.assertEqual(len([o for o in restarted.operations() if o['operation_id']=='crash1']),1)
        self.assertEqual(restarted.records()[record['id']]['status'],'disputed')

    def test_outbox_crash_after_append_does_not_duplicate(self):
        record={**self.proposal(),'version':2,'status':'disputed'}
        with self.assertRaises(RuntimeError): self.memory.write(record,'crash2',fault='after_append')
        restarted=Memory(self.store,self.store.load())
        restarted.flush()
        self.assertEqual(len([o for o in restarted.operations() if o['operation_id']=='crash2']),1)

    def test_torn_tail_quarantined_and_recovered(self):
        row=self.proposal()
        with self.memory.journal.open('ab') as f: f.write(b'{"operation_id":')
        self.assertEqual(self.memory.records()[row['id']]['id'],row['id'])
        self.assertTrue(list(self.memory.root.glob('torn-tail-*.bin')))

    def test_index_rebuild_and_physical_purge(self):
        row=self.active()
        self.memory.index.unlink()
        self.assertEqual(self.memory.rebuild()['records'],1)
        self.memory.purge(row['id'],'User privacy erasure')
        self.assertNotIn(row['content'],self.memory.journal.read_text(encoding='utf-8'))
        self.assertNotIn(row['content'],json.dumps(self.state['memory_outbox']))
        self.assertEqual(self.memory.search(self.task,'browser',include_candidates=True),[])

    def test_legacy_import_does_not_infer_trust(self):
        self.store.remember('lead','Verify browser before use',self.task['id'],'human_accepted')
        self.memory.import_legacy()
        self.assertEqual(next(iter(self.memory.records().values()))['status'],'legacy_unverified')
        self.assertEqual(self.memory.search(self.task,'browser'),[])

    def test_secrets_and_invisible_evidence_rejected(self):
        with self.assertRaises(ValueError): self.memory.propose(self.task,'api_key=ABC123','semantic',[self.ev])
        with self.assertRaises(ValueError): self.memory.propose(self.task,'Unknown evidence','semantic',[9999])

    def test_working_set_preserves_old_human_constraints_and_restart(self):
        constraint='Do NOT delete cancelled rows; timezone Asia/Hong_Kong; HKD 1,000,000; revision 3.'
        self.store.event(self.state,'human_message',task=self.task['id'],sender='human',to='group',text=constraint)
        for i in range(85): self.event(self.task,output='X'*12000)
        context=self.engine.context(self.task)
        self.assertIn(constraint,json.dumps(context['working_set']))
        self.assertLessEqual(context['context_trace']['estimated_tokens'],self.config.input_budget_tokens)
        restarted=Engine(self.store,self.config,None,self.tools)
        self.assertIn(constraint,json.dumps(restarted.context(restarted.state['tasks'][self.task['id']])['working_set']))

    def test_unverified_assumptions_cannot_be_promoted_by_summary(self):
        annotate(self.store,self.state,self.task,assumptions=[{'text':'Assume a fixed timezone until confirmed','evidence':[self.ev]}])
        working=update(self.store,self.state,self.task)
        self.assertEqual(len(working['assumptions']),1)
        self.assertNotIn('Assume a fixed timezone',json.dumps(working['confirmed_inputs']))

    def test_claim_quote_and_opposition_are_preserved(self):
        source=self.source()
        quote='false positives'
        text=research.source_text(self.store,source)
        start=text.index(quote)
        claim=research.record_claim(self.store,self.state,self.task,'Detector reliability is limited',[
            {'source_id':source['id'],'start':start,'end':start+len(quote),'quote':quote,'relation':'opposes'}])
        self.assertTrue(claim['citations'][0]['body_verified'])
        self.assertEqual(research.list_claims(self.state,self.task)[0]['citations'][0]['relation'],'opposes')

    def test_search_snippet_cannot_verify_a_claim(self):
        source=self.source('False positive warning','search_snippet')
        citation={'source_id':source['id'],'start':0,'end':5,'quote':'False','relation':'supports'}
        with self.assertRaises(ValueError): research.record_claim(self.store,self.state,self.task,'Finding',[citation])

    def test_wrong_offsets_unread_passages_and_pdf_page_rejected(self):
        source=self.source('A'*13000+'Important result')
        citation={'source_id':source['id'],'start':13000,'end':13016,'quote':'Important result','relation':'supports'}
        with self.assertRaises(ValueError): research.record_claim(self.store,self.state,self.task,'Finding',[citation])
        research.read_source(self.store,self.state,self.task,source['id'],offset=13000)
        citation['page']=9
        with self.assertRaises(ValueError): research.record_claim(self.store,self.state,self.task,'Finding',[citation])

    def test_private_source_cannot_be_used_by_public_task(self):
        private={**self.task,'private':True,'id':'private1'}
        source=self.source('Private finding',task=private)
        with self.assertRaises(ValueError): research.read_source(self.store,self.state,self.task,source['id'])

    def test_source_hash_mismatch_is_detected(self):
        source=self.source()
        (self.store.path/'sources'/source['sha256']).write_text('Changed',encoding='utf-8')
        with self.assertRaises(ValueError): research.read_source(self.store,self.state,self.task,source['id'])

    def test_booking_clipping_cancellation_adjacent_and_empty_rooms(self):
        item=self.dataset({'rooms':[{'id':1,'name':'Room A'},{'id':2,'name':'Room B'}],'bookings':[
            {'room_id':1,'start_dt':'2026-11-20T23:00','end_dt':'2026-11-21T01:30','status':'active','booker':'PERSON'},
            {'room_id':1,'start_dt':'2026-11-21T01:30','end_dt':'2026-11-21T02:30','status':'active'},
            {'room_id':1,'start_dt':'2026-11-21T03:00','end_dt':'2026-11-21T04:00','status':'cancelled'},
            {'room_id':1,'start_dt':'2026-11-22T00:00','end_dt':'2026-11-22T01:00','status':'active'}]},timezone='Asia/Hong_Kong')
        result=data.analyze(self.store,self.state,self.task,item['id'],{'op':'booking_summary','start_date':'2026-11-21','end_date':'2026-11-21'})['result']
        self.assertEqual(result['rooms'][0]['reserved_hours'],'2.5')
        self.assertEqual(result['rooms'][0]['bookings'],3)
        self.assertEqual(result['rooms'][1]['reserved_hours'],'0')
        _,payload=data.load(self.store,self.state,self.task,item['id'])
        self.assertNotIn('booker',payload['records'][0])

    def test_decimal_allocation_cash_fees_and_hypothetical_stress(self):
        item=self.dataset([{'asset':'AAA','weight':'0.6'},{'asset':'CASH','weight':'0.4'}])
        result=data.analyze(self.store,self.state,self.task,item['id'],{'op':'allocation','capital_hkd':'1000000','hkd_per_usd':'7.8','fx_as_of':'2026-11-21','fee_rate':'0.001','shocks':{'AAA':'-0.3'}})['result']
        self.assertEqual(result['fees_hkd'],'600.00')
        self.assertEqual(result['positions'][1]['gross_hkd'],'400000.00')
        self.assertEqual(result['scenario_pnl_hkd'],'-179820.00')

    def test_weights_and_arbitrary_expressions_rejected(self):
        item=self.dataset([{'asset':'AAA','weight':'0.8'}])
        with self.assertRaises(ValueError): data.analyze(self.store,self.state,self.task,item['id'],{'op':'allocation','capital_hkd':'1000','hkd_per_usd':'7.8','fx_as_of':'2026-11-21'})
        with self.assertRaises(ValueError): data.analyze(self.store,self.state,self.task,item['id'],{'op':'eval','code':'__import__("os")'})
        with self.assertRaises(ValueError): data.number('NaN')

    def test_detector_base_rate_computation(self):
        item=self.dataset([{'prevalence':'0.01','sensitivity':'0.9','specificity':'0.95'}])
        result=data.analyze(self.store,self.state,self.task,item['id'],{'op':'detector_base_rate'})['result']
        self.assertLess(float(result['positive_predictive_value']),0.16)

    def test_private_dataset_and_fake_sqlite_snapshot_rejected(self):
        private={**self.task,'private':True,'id':'private1'}
        item=self.dataset([{'a':'1'}],task=private)
        with self.assertRaises(ValueError): data.load(self.store,self.state,self.task,item['id'])
        with self.assertRaises(ValueError): data.register(self.store,self.state,self.task,'anything.sqlite3','bad',{'input_kind':'operator_snapshot'})

    def test_dataset_version_invalidates_schema_memory(self):
        one=self.dataset([{'n':'1'}])
        self.active(scope={'dataset_key':one['key'],'dataset_digest':one['sha256']})
        self.assertTrue(self.memory.search(self.task,'browser'))
        two=self.dataset([{'n':'2'}])
        self.assertEqual(two['version'],2)
        self.assertEqual(self.memory.search(self.task,'browser'),[])

    def test_document_download_rejects_mime_magic_and_oversize(self):
        from collab.documents import fetch_document
        from email.message import Message
        class Response:
            url='https://example.org/paper.pdf'
            def __init__(self,body,mime,length=None):
                self.body=body
                self.headers=Message()
                self.headers['Content-Type']=mime
                if length: self.headers['Content-Length']=str(length)
            def read(self,limit): return self.body[:limit]
            def __enter__(self): return self
            def __exit__(self,*args): pass
        for response in (Response(b'<html>Not PDF</html>','application/pdf'),
                         Response(b'%PDF-1.4','text/html'),
                         Response(b'%PDF-1.4','application/pdf',30*1024*1024)):
            with self.subTest(headers=str(response.headers)),patch('collab.tools.public_url',side_effect=lambda u:u),patch('urllib.request.build_opener') as opener:
                opener.return_value.open.return_value=response
                with self.assertRaises(ValueError): fetch_document(self.store,self.state,self.task,response.url)

    def test_complete_journal_tail_without_newline_recovers(self):
        self.proposal()
        self.memory.journal.write_bytes(self.memory.journal.read_bytes().rstrip(b'\n'))
        self.memory.records()
        self.assertTrue(self.memory.journal.read_bytes().endswith(b'\n'))

    def test_propose_idempotency_survives_replay(self):
        first=self.proposal(operation_id='proposal-one')
        second=self.proposal(operation_id='proposal-one')
        self.assertEqual(first,second)
        with self.assertRaises(ValueError):
            self.memory.propose(self.task,'Different content','procedural',[self.ev],operation_id='proposal-one')

    def gate_fixture(self,passed=True):
        from collab.store import dump
        self.state['evaluation_mode']='automated_selftest'
        p=self.store.workspace('lead')/'report.md'
        p.write_text('Unit-test delivery fixture',encoding='utf-8')
        artifact=self.tools.execute(self.state,'lead','artifact.publish',{'path':'report.md'})
        review_id=self.engine.new_task('qa','Independent review',['Review all conditions'],'r1',kind='review',target=self.task['id'])
        self.state['tasks'][review_id]['status']='completed'
        self.task['review_task']=review_id
        self.task['submission']={'artifacts':[artifact['id']],'lesson':'Review versioned evidence before reuse.'}
        request=self.engine.request(self.task,'acceptance',summary='Fixture',artifacts=[artifact['id']])
        folder=self.store.path/'selftest-evidence'
        dump(folder/'observation.json',{'kind':'unit_test_fixture','passed':passed})
        evidence=folder/'observation.json'
        receipt={'request_id':request,'artifact_hashes':{artifact['id']:artifact['sha256']},
                 'checks':[{'name':'unit fixture','passed':passed,'evidence':'observation.json'}],
                 'evidence_files':[{'path':'observation.json','sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
        dump(folder/'verdict.json',receipt)
        return request,folder/'verdict.json'

    def test_automated_test_gate_cannot_count_as_human_acceptance(self):
        from collab.selftest import record_verdict
        from collab.report import metrics
        request,path=self.gate_fixture()
        self.assertTrue(record_verdict(self.engine,request,path))
        self.assertEqual(self.state['runs']['r1']['status'],'test_passed')
        self.assertEqual(metrics(self.state)['completed_runs'],0)
        self.assertIsNone(self.state['requests'][request]['approved'])
        self.assertEqual(next(iter(self.memory.records().values()))['outcome'],'automated_test_passed')
        self.engine.submit('Second original task in this same test group')

    def test_test_gate_refuses_production_mode_and_changed_delivery(self):
        from collab.selftest import record_verdict
        request,path=self.gate_fixture()
        self.state.pop('evaluation_mode')
        with self.assertRaises(ValueError): record_verdict(self.engine,request,path)
        self.state['evaluation_mode']='automated_selftest'
        receipt=json.loads(path.read_text(encoding='utf-8'))
        receipt['artifact_hashes']={}
        path.write_text(json.dumps(receipt),encoding='utf-8')
        with self.assertRaises(ValueError): record_verdict(self.engine,request,path)

    def test_failed_operator_check_revokes_draft_not_human_rejection(self):
        from collab.selftest import record_verdict
        request,path=self.gate_fixture(passed=False)
        self.assertFalse(record_verdict(self.engine,request,path))
        self.assertEqual(self.state['requests'][request]['status'],'invalidated')
        self.assertNotIn('approved',self.state['requests'][request])
        self.assertEqual(self.task['status'],'running')

    def test_test_assumptions_never_impersonate_human_reply(self):
        from collab.selftest import answer_clarification
        self.state['evaluation_mode']='automated_selftest'
        request=self.engine.request(self.task,'clarification',question='What is the simulated investment horizon?')
        answer_clarification(self.engine,request,'Assume a five-year horizon for this test fixture.')
        self.assertEqual(self.state['requests'][request]['answer_authority'],'test_operator_assumption')
        self.assertEqual(self.state['events'][-1]['kind'],'operator_test_message')
        self.assertFalse(any(e['kind']=='human_answer' for e in self.state['events']))

    def test_verified_curl_fallback_pins_ip_and_rejects_private_redirect(self):
        from collab.public_http import curl_get
        calls=[]
        def addresses(host,*args,**kwargs):
            ip='127.0.0.1' if host=='127.0.0.1' else '93.184.216.34'
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))]
        def process(argv,**kwargs):
            calls.append(argv)
            Path(argv[argv.index('--dump-header')+1]).write_bytes(b'HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1/secret\r\n\r\n')
            from unittest.mock import MagicMock
            p=MagicMock()
            p.__enter__.return_value=p
            p.stdout.read.return_value=b''
            p.wait.return_value=0
            return p
        with patch('socket.getaddrinfo',side_effect=addresses),patch('shutil.which',return_value='curl'),patch('subprocess.Popen',side_effect=process):
            with self.assertRaises(ValueError): curl_get('https://example.org/paper.pdf',1000)
        self.assertEqual(len(calls),1)
        self.assertIn('example.org:443:93.184.216.34',calls[0])
        self.assertNotIn('--insecure',calls[0])
        self.assertNotIn('--location',calls[0])

    def test_tls_fallback_only_for_verified_public_get(self):
        from collab.public_http import get
        from unittest.mock import MagicMock
        opener=MagicMock()
        opener.open.side_effect=urllib.error.URLError(ssl.SSLCertVerificationError('Missing intermediate'))
        with patch('collab.tools.public_url',side_effect=lambda u:u),patch('urllib.request.build_opener',return_value=opener),patch('shutil.which',return_value='curl'),patch('collab.public_http.curl_get',return_value=(b'ok',None,'https://example.org')) as fallback:
            self.assertEqual(get('https://example.org',1000)[0],b'ok')
            fallback.assert_called_once_with('https://example.org',1000)

    def test_event_read_does_not_expand_private_visibility(self):
        tid=self.engine.submit('Secret direct task',owner='lead',private=True)
        private=self.state['tasks'][tid]
        ev=self.event(private,output='PRIVATE_EVENT')
        with self.assertRaises(ValueError): research.read_event(self.store,self.state,self.task,ev)
        self.assertIn('PRIVATE_EVENT',research.read_event(self.store,self.state,private,ev)['text'])

    def test_revision_change_keeps_assumption_but_requires_revalidation(self):
        annotate(self.store,self.state,self.task,assumptions=[{'text':'Old schema might be stable','evidence':[self.ev]}])
        self.task['revision']=2
        working=update(self.store,self.state,self.task)
        self.assertTrue(working['assumptions'][0]['needs_revalidation'])

    def test_js_false_lesson_exits_guidance_after_real_counterevidence(self):
        old=self.memory.propose(self.task,'Static JavaScript syntax alone proves buttons work','procedural',[self.ev])
        self.memory.transition(old['id'],'active','human',[],'Seed an intentionally incorrect test memory')
        failure=self.event(self.task,exit_code=1,output='Browser button did not change the visible state')
        self.memory.challenge(self.task,old['id'],'Button interaction disproves the old lesson',[failure])
        success=self.event(self.task,exit_code=0,output='Static parsing and actual button click both pass')
        corrected=self.memory.propose(self.task,'Verify JavaScript syntax and actual buttons independently','procedural',[success],supersedes=old['id'])
        self.memory.transition(corrected['id'],'active','human',[],'Confirm the corrected scope and fresh evidence')
        hits=self.memory.search(self.task,'JavaScript buttons')
        self.assertEqual([m['id'] for m in hits],[corrected['id']])
        self.assertEqual(self.memory.inspect(old['id'],self.task)['status'],'disputed')

    def test_quote_location_returns_exact_readable_offsets(self):
        source=self.source('A short introduction. Exact study conclusion. End.')
        hit=research.find_quote(self.store,self.state,self.task,source['id'],'Exact study conclusion')['matches'][0]
        citation={k:hit[k] for k in ('source_id','start','end','quote')}
        claim=research.record_claim(self.store,self.state,self.task,'Study conclusion',[{**citation,'relation':'supports'}])
        self.assertEqual(claim['citations'][0]['quote'],'Exact study conclusion')
        self.assertEqual(research.find_quote(self.store,self.state,self.task,source['id'],'Imaginary quotation')['matches'],[])

    def test_latest_requested_page_is_not_blindly_truncated(self):
        text='A'*3500+'IMPORTANT MIDDLE REPAIR TARGET'+'B'*5000
        self.store.event(self.state,'tool_result',task=self.task['id'],agent='lead',name='files.read',
                         result={'path':'app.py','offset':0,'content':text,'truncated':False})
        context=self.engine.context(self.task)
        latest=context['recent_events'][-1]['result']['content']
        self.assertEqual(latest,text)
        self.assertLessEqual(context['context_trace']['estimated_tokens'],self.config.input_budget_tokens)

    def test_long_failing_output_preserves_traceback_tail(self):
        text='test name ... ok\n'*2200+'\nTraceback: UNIQUE_REPAIR_EXCEPTION\nFAILED (errors=1)'
        self.event(self.task,exit_code=1,output=text)
        context=self.engine.context(self.task)
        result=context['recent_events'][-1]['result']
        self.assertIn('UNIQUE_REPAIR_EXCEPTION',json.dumps(result))

    def test_windows_atomic_replace_retries_keep_complete_state(self):
        from collab.store import dump
        target=Path(self.temp.name)/'atomic.json'
        dump(target,{'old':True})
        original=Path.replace
        calls=[]
        def transient(path,destination):
            calls.append(1)
            if len(calls)<3: raise PermissionError('Simulated reader lock')
            return original(path,destination)
        with patch.object(Path,'replace',transient),patch('collab.store.time.sleep'):
            dump(target,{'new':True})
        self.assertEqual(json.loads(target.read_text()),{'new':True})
        self.assertEqual(len(calls),3)
        with patch.object(Path,'replace',side_effect=PermissionError('Persistent lock')),patch('collab.store.time.sleep'):
            with self.assertRaises(PermissionError): dump(target,{'partial':True})
        self.assertEqual(json.loads(target.read_text()),{'new':True})

    def test_uncompressed_ablation_is_explicit_and_still_budgeted(self):
        self.config.compaction_enabled=False
        context=self.engine.context(self.task)
        self.assertEqual(context['context_trace']['strategy'],'uncompressed-ablation')
        self.assertIn('prompt',context['roles']['lead'])
        self.config.input_budget_tokens=100
        with self.assertRaises(ValueError): self.engine.context(self.task)

    def test_long_review_goal_is_not_used_as_an_unbounded_retrieval_query(self):
        self.task['description']='Review this published delivery and all requirements: '+'Detailed review context. '*200
        context=self.engine.context(self.task)
        self.assertEqual(self.task['working_set']['goal'],self.task['description'])
        self.assertEqual(context['working_set']['goal_ref'],'current_task.description')
        self.assertGreater(len(context['current_task']['description']),3000)
        self.assertLessEqual(context['context_trace']['estimated_tokens'],self.config.input_budget_tokens)

    def test_successful_verification_survives_many_file_reads(self):
        proof=self.event(self.task,output='Ran 8 tests in 0.01s\nOK')
        for i in range(60):
            self.store.event(self.state,'tool_result',task=self.task['id'],agent='lead',name='files.read',result='File section '+str(i))
        context=self.engine.context(self.task)
        self.assertIn(proof,[row['seq'] for row in context['working_set']['verification_ledger']])
        self.assertIn(proof,[row['seq'] for row in context['current_task_evidence_index']])

    def test_verification_command_hint_is_bounded_without_recursive_wrapping(self):
        self.store.event(self.state,'agent_action',task=self.task['id'],agent='lead',
            action={'type':'tool','name':'code.run','args':{'argv':['python','-c','print(1)\n'*1000]}})
        self.event(self.task,output='A bounded command observation')
        first=self.engine.context(self.task)
        second=self.engine.context(self.task)
        for context in (first,second):
            hint=context['working_set']['verification_ledger'][0]['input_hint']
            self.assertIsInstance(hint,str)
            self.assertLessEqual(len(hint),400)

    def test_completed_dependency_delivery_is_a_versioned_input_reference(self):
        dep=self.engine.new_task('dev','Build the fixture',['Publish'],'r1',self.task['id'])
        p=self.store.workspace('dev')/'result.md';p.write_text('Fixture output',encoding='utf-8')
        artifact=self.tools.execute(self.state,'dev','artifact.publish',{'path':'result.md'})
        node=self.state['tasks'][dep]
        node.update(status='completed',submission={'artifacts':[artifact['id']],'summary':'Fixture output'})
        peer=self.engine.new_task('qa','Check the dependency',['Inspect'],'r1',self.task['id'],depends_on=[dep])
        working=update(self.store,self.state,self.state['tasks'][peer])
        self.assertEqual(working['dependency_deliveries'][0]['artifact_ids'],[artifact['id']])

    def test_wide_structured_tool_result_is_paged_without_losing_original(self):
        original=[{'claim':str(i),'citations':[{'quote':'Evidence '*90} for _ in range(8)]} for i in range(25)]
        event=self.store.event(self.state,'tool_result',task=self.task['id'],agent='lead',name='evidence.list_claims',result=original)
        context=self.engine.context(self.task)
        result=context['recent_events'][-1]['result']
        self.assertTrue(result['truncated'])
        self.assertEqual(result['read_ref']['seq'],event['seq'])
        self.assertEqual(self.state['events'][event['seq']-1]['result'],original)
        self.assertLessEqual(context['context_trace']['estimated_tokens'],self.config.input_budget_tokens)

    def test_long_term_memory_has_a_separate_context_quota(self):
        for i in range(6):
            record=self.memory.propose(self.task,('Verify browser booking cancellation independently. '*40)+str(i),'procedural',[self.ev])
            self.memory.transition(record['id'],'active','operator',[],'Declared memory packing fixture')
        context=self.engine.context(self.task)
        self.assertLessEqual(sum(estimate(row) for row in context['memory']),int(self.config.input_budget_tokens*.15))
        self.assertTrue(context['memory_overflow_refs']['guidance'])
        self.assertEqual(len(self.memory.records()),6)

    def test_continuity_baseline_needs_explicit_reverification(self):
        from scripts.run_scenario_selftests import ready
        self.assertFalse(ready({'status':'completed'}))
        self.assertFalse(ready({'status':'completed','baseline_inherited':True}))
        self.assertTrue(ready({'status':'completed','baseline_inherited':True,'baseline_reverification':{'passed':True}}))
        self.assertTrue(ready({'status':'test_passed'}))
        from scripts.run_scenario_selftests import predecessor_ready
        self.assertFalse(predecessor_ready({'status':'failed'}))
        self.assertTrue(predecessor_ready({'status':'failed'},allow_failed=True))
        self.assertFalse(ready({'status':'failed'}))
        from scripts.run_scenario_selftests import skip_finished
        self.assertTrue(skip_finished({'status':'failed'},0,True))
        self.assertFalse(skip_finished({'status':'failed'},1,True))
        self.assertFalse(skip_finished({'status':'failed'},0,False))

    def test_large_previous_delivery_does_not_crowd_out_next_task(self):
        for i in range(35):
            self.state['artifacts']['a'+str(i)]={'id':'a'+str(i),'owner':'dev',
                'path':'previous/delivery/'+('long_directory/'*12)+str(i)+'.md','sha256':str(i).zfill(64)}
        text='Exact next-task requirement with cancellation history preserved.'
        self.task['description']=text
        self.task['criteria']=['Keep cancellation history. '+str(i) for i in range(7)]
        self.store.event(self.state,'agent_action',task=self.task['id'],agent='lead',
                         action={'type':'plan','text':'Detailed plan. '*100,'criteria':self.task['criteria']})
        self.config.input_budget_tokens=18000
        context=self.engine.context(self.task)
        self.assertEqual(context['current_task']['description'],text)
        self.assertEqual(context['current_task']['criteria'],self.task['criteria'])
        self.assertEqual(len(context['artifacts'])+len(context.get('archived_artifact_ids',[])),35)
        self.assertLessEqual(context['context_trace']['estimated_tokens'],18000)

    def test_rebuildable_verification_previews_yield_before_human_constraints(self):
        requirement='Never delete cancellations; timezone Asia/Hong_Kong. '+('确认要求。'*100)
        self.store.event(self.state,'human_message',sender='human',to='group',text=requirement)
        for i in range(6):
            self.store.event(self.state,'agent_action',task=self.task['id'],
                action={'type':'tool','name':'code.run','args':{'argv':['python','-c','检查'*200+str(i)]}})
            self.store.event(self.state,'tool_result',task=self.task['id'],name='code.run',
                result={'exit_code':0,'output':'Ran 3 tests\n'+('检验输出。'*200)},verification_snapshot={'app.py':str(i)})
        self.config.input_budget_tokens=17000
        context=self.engine.context(self.task)
        self.assertLessEqual(context['context_trace']['estimated_tokens'],17000)
        self.assertIn(requirement,[row['text'] for row in context['working_set']['confirmed_inputs']])
        ledger=context['working_set']['verification_ledger']
        self.assertEqual(len(ledger),6)
        self.assertTrue(all('source_snapshot_digest' in row for row in ledger))
        self.assertTrue(all('read_ref' in row and 'output_tail' not in row for row in ledger))

    def test_recent_error_cannot_hide_the_last_tool_page(self):
        page='Verified page beginning. '+('MIDDLE EVIDENCE. '*250)+' Verified page end.'
        event=self.store.event(self.state,'tool_result',task=self.task['id'],agent='lead',
                              name='files.read',result={'content':page})
        self.store.event(self.state,'action_error',task=self.task['id'],error='Transient context assembly issue')
        context=self.engine.context(self.task)
        self.assertEqual(context['recent_events'][-1]['seq'],event['seq'])
        self.assertEqual(context['recent_events'][-1]['result']['content'],page)

    def test_reading_ledger_retains_file_ranges_and_source_quotes(self):
        self.store.event(self.state,'agent_action',task=self.task['id'],agent='lead',
                         action={'type':'tool','name':'files.read','args':{'path':'service.py','offset':150}})
        self.store.event(self.state,'tool_result',task=self.task['id'],agent='lead',
                         name='files.read',result='def availability():\n    pass\n')
        source=self.source('A verified passage at an exact location.')
        result=research.find_quote(self.store,self.state,self.task,source['id'],'verified passage')
        self.store.event(self.state,'tool_result',task=self.task['id'],agent='lead',name='evidence.find_quote',result=result)
        ledger=update(self.store,self.state,self.task)['reading_ledger']
        self.assertEqual(ledger['files'][0]['last_range'][0],150)
        self.assertEqual(ledger['files'][0]['symbols'],['availability'])
        self.assertEqual(ledger['exact_quote_refs'][0]['start'],2)
        self.assertEqual(ledger['sources'][0]['source_id'],source['id'])

    def test_host_maintenance_is_referenced_without_dropping_user_constraints(self):
        user=self.store.event(self.state,'human_message',sender='human',to='group',text='Never delete cancellation history.')
        repair=self.store.event(self.state,'operator_test_message',sender='test_operator',to='group',
                                text='Observed platform repair. '*1000,purpose='platform_maintenance')
        working=update(self.store,self.state,self.task)
        self.assertIn(user['seq'],[e['seq'] for e in working['confirmed_inputs']])
        self.assertNotIn(repair['seq'],[e['seq'] for e in working['confirmed_inputs']])
        self.assertIn(repair['seq'],[e['seq'] for e in working['maintenance_refs']])
        self.assertEqual(self.state['events'][repair['seq']-1]['text'],'Observed platform repair. '*1000)

    def test_new_run_claim_view_requires_explicit_history(self):
        source=self.source('Earlier equity research is not semiconductor evidence.')
        match=research.find_quote(self.store,self.state,self.task,source['id'],'Earlier equity research')['matches'][0]
        claim=research.record_claim(self.store,self.state,self.task,'Old equity research',
            [{k:match[k] for k in ('source_id','start','end','quote')}|{'relation':'supports'}])
        self.state['runs']['r2']={'id':'r2','status':'running'}
        tid=self.engine.new_task('lead','New semiconductor research',['New sources'],'r2')
        task=self.state['tasks'][tid]
        self.assertEqual(research.list_claims(self.state,task),[])
        self.assertEqual(research.list_claims(self.state,task,True)[0]['id'],claim['id'])


if __name__=='__main__': unittest.main()
