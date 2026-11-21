"""Bounded public PDF acquisition; parsing only inside a dedicated offline image."""
import hashlib
import json
import os
import uuid
import urllib.request
from .store import now
from .sandbox import Sandbox
from .research import permitted


def fetch_document(store,state,task,url):
    from .public_http import get
    data,headers,final=get(url,20*1024*1024)
    content_type=headers.get_content_type()
    if content_type not in {'application/pdf','application/octet-stream'} or not data.startswith(b'%PDF-'):
        raise ValueError('Expected PDF MIME and magic bytes; disguised documents rejected')
    digest=hashlib.sha256(data).hexdigest()
    folder=store.path/'documents'
    folder.mkdir(exist_ok=True)
    (folder/digest).write_bytes(data)
    documents=state.setdefault('documents',{})
    item={'id':'d'+str(len(documents)+1),'url':url,'final_url':final,'sha256':digest,
          'bytes':len(data),'retrieved_at':now(),'kind':'pdf_download_unread',
          'owner':task['owner'],'task':task['id'],'private':bool(task.get('private'))}
    documents[item['id']]=item
    return item


def extract(store,state,task,config,document_id,max_pages=100):
    document=state.get('documents',{}).get(document_id)
    if not document or not permitted(document,task):
        raise ValueError('Document unavailable in this scope')
    if not 1<=max_pages<=100:
        raise ValueError('Page limit must be 1-100')
    data=(store.path/'documents'/document['sha256']).read_bytes()
    if len(data)>20*1024*1024 or hashlib.sha256(data).hexdigest()!=document['sha256']:
        raise ValueError('Document snapshot invalid')
    work=store.path/'document-work'/uuid.uuid4().hex
    work.mkdir(parents=True)
    if os.name != 'nt':
        work.chmod(0o777)
    (work/'input.pdf').write_bytes(data)
    result=Sandbox(config.document_image).run(work,['python','/opt/collab/document_worker.py',str(max_pages)],timeout=60)
    if result['exit_code']!=0:
        return {**result,'ok':False,'document_id':document_id,'quality':'parse_failed'}
    path=work/'extracted.json'
    if not path.is_file() or path.stat().st_size>5*1024*1024:
        raise ValueError('Parser output missing or over limit')
    parsed=json.loads(path.read_text(encoding='utf-8'))
    if parsed.get('quality')=='no_text':
        return {'ok':False,'document_id':document_id,'quality':'no_text','pages':parsed['pages'],
                'note':'No usable text; OCR not implemented. Full text has not been verified.'}
    body=parsed['text']
    digest=hashlib.sha256(body.encode()).hexdigest()
    # Repeated parsing must not manufacture apparent progress via new IDs.
    # Parsing still runs independently; reuse only an identical task-local
    # body/page map from the same immutable document snapshot.
    for existing in state['sources'].values():
        if (existing.get('kind')=='document_text' and existing.get('task')==task['id']
                and existing.get('document_sha256')==document['sha256']
                and existing.get('sha256')==digest and existing.get('pages')==parsed['pages']):
            from .research import read_source
            return {'ok':True,**read_source(store,state,task,existing['id'])}
    source_id='s'+str(len(state['sources'])+1)
    folder=store.path/'sources'
    folder.mkdir(exist_ok=True)
    (folder/digest).write_text(body,encoding='utf-8',newline='')
    source={'id':source_id,'agent':task['owner'],'task':task['id'],'private':bool(task.get('private')),
            'url':document['url'],'kind':'document_text','title':'PDF '+document_id,
            'sha256':digest,'content_ref':digest,'characters':len(body),'pages':parsed['pages'],
            'retrieved_at':document['retrieved_at'],'extracted_at':now(),'quality':parsed['quality'],
            'document_id':document_id,'document_sha256':document['sha256']}
    state['sources'][source_id]=source
    from .research import read_source
    return {'ok':True,**read_source(store,state,task,source_id)}
