"""Real document sandbox controls and bounded context proof; optional online PDF."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collab.config import Config
from collab.engine import Engine
from collab.store import Store,dump
from collab.tools import Tools
from collab.documents import extract,fetch_document


def pdf(pages):
    count=len(pages)
    font=3+2*count
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',
             ('<< /Type /Pages /Count '+str(count)+' /Kids ['+' '.join(f'{3+i} 0 R' for i in range(count))+'] >>').encode()]
    for i in range(count):
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {3+count+i} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>'.encode())
    for text in pages:
        stream=b'BT /F1 12 Tf 72 700 Td ('+text.encode('ascii')+b') Tj ET'
        objects.append(f'<< /Length {len(stream)} >>\nstream\n'.encode()+stream+b'\nendstream')
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    content=b'%PDF-1.4\n'
    offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(content))
        content+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(content)
    content+=f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    content+=b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    content+=f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return content


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--online',action='store_true')
    parser.add_argument('--runtime',default='runtime/development_m3_m4')
    parser.add_argument('--group',default='_controls')
    args=parser.parse_args()
    config=Config.from_env()
    store=Store(args.runtime,args.group)
    if store.state_path.exists():
        raise ValueError('Controls already exist; keep the prior result and choose a new control group before repeating')
    roles={r:{'title':r,'prompt':r} for r in ('lead','dev','qa','release')}
    Engine.create(store,{'roles':roles,'tasks':[]})
    engine=Engine(store,config,None,Tools(store,config))
    tid=engine.submit('Verify document extraction and bounded context')
    task=engine.state['tasks'][tid]
    state=engine.state
    results=[]
    def fixture(name,raw,max_pages=100):
        digest=hashlib.sha256(raw).hexdigest()
        folder=store.path/'documents'
        folder.mkdir(exist_ok=True)
        (folder/digest).write_bytes(raw)
        state.setdefault('documents',{})[name]={'id':name,'sha256':digest,'url':'https://example.invalid/offline-control',
            'retrieved_at':'2026-11-21T00:00:00+00:00','private':False,'task':tid,'fixture':True}
        return extract(store,state,task,config,name,max_pages)
    good=fixture('positive',pdf(['Trusted offline fixture proves page text extraction with exact offsets.']))
    assert good['ok'] and 'Trusted offline fixture' in good['text']
    results.append({'name':'real_pdf_text_and_page_offsets','passed':True,'source_id':good['id'],'quality':good['quality']})
    empty=fixture('scan_control',pdf(['']))
    assert not empty['ok'] and empty['quality']=='no_text'
    results.append({'name':'no_text_does_not_claim_full_verification','passed':True})
    bad=fixture('malformed',b'%PDF-1.4\ninvalid and not a real PDF')
    assert bad['exit_code']!=0 and not bad['ok']
    results.append({'name':'malformed_pdf_is_parse_failure','passed':True})
    over=fixture('page_limit',pdf(['First page with sufficient real text for this fixture.','Second page exceeds the deliberately lowered limit.']),max_pages=1)
    assert over['exit_code']!=0 and not over['ok']
    results.append({'name':'page_limit_enforced_inside_container','passed':True})
    constraint='Do NOT delete cancelled records. Use Asia/Hong_Kong; retain revision and evidence IDs.'
    engine.event('human_message',sender='human',to='group',task=tid,text=constraint)
    for i in range(100):
        engine.event('tool_result',task=tid,agent='lead',name='files.read',result='Long source contents '+str(i)+' X'*6000)
    context=engine.context(task)
    assert constraint in json.dumps(context['working_set'])
    assert context['context_trace']['estimated_tokens']<=config.input_budget_tokens
    results.append({'name':'bounded_context_retains_human_negations_and_references','passed':True,'trace':context['context_trace']})
    if args.online:
        try:
            document=fetch_document(store,state,task,'https://arxiv.org/pdf/2304.02819')
            original=extract(store,state,task,config,document['id'])
            results.append({'name':'real_public_pdf_download_and_extract','passed':bool(original.get('ok')),
                            'document_sha256':document['sha256'],'source_id':original.get('id'),'quality':original.get('quality')})
        except Exception as exc:
            results.append({'name':'real_public_pdf_download_and_extract','passed':False,'error_type':type(exc).__name__,'reason':str(exc)[:200]})
    store.save(state)
    receipt={'passed':all(r['passed'] for r in results),'checks':results,
             'limits':'Synthetic PDFs test the real offline parser; only the explicitly online check is a downloaded primary source.'}
    dump(store.path/'verification.json',receipt)
    print(json.dumps(receipt,ensure_ascii=False))
    return 0 if receipt['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
