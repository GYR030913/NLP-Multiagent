"""Independent first-use HTTP concurrency check against an immutable delivery."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collab.store import Store, dump
from collab.config import Config
from collab.bundles import materialize
from collab.sandbox import Sandbox

CHECK = '''import json,threading,tempfile,urllib.request,urllib.error,importlib
from concurrent.futures import ThreadPoolExecutor
web=importlib.import_module(PACKAGE+'.web');BookingStore=importlib.import_module(PACKAGE+'.storage').BookingStore
with tempfile.TemporaryDirectory() as tmp:
 db=tmp+'/records.db';server=web.make_server('127.0.0.1',0,db);threading.Thread(target=server.serve_forever,daemon=True).start()
 url='http://127.0.0.1:'+str(server.server_port)
 payload={'room_id':1,'date':'2026-11-21','start':'10:00','end':'11:00','booker':'integration fixture'}
 def post(path,payload):
  req=urllib.request.Request(url+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
  try:
   with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.load(r)
  except urllib.error.HTTPError as e:return e.code,json.load(e)
 with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(lambda _:post('/api/bookings',payload),range(20)))
 codes=[r[0] for r in results]
 if codes.count(201)!=1 or codes.count(409)!=19:
  print(json.dumps({'statuses':codes,'unexpected_responses':[r for r in results if r[0] not in (201,409)]}));raise AssertionError('Concurrent booking contract failed')
 success=next(r[1] for r in results if r[0]==201);booking=success.get('booking',success)
 assert post('/api/bookings/'+str(booking['id'])+'/cancel',{})[0]==200
 assert post('/api/bookings',payload)[0]==201
 server.shutdown();server.server_close();web.get_store().close();web._STORE=None
 reopened=BookingStore(db);rows=reopened.list_bookings();assert len(rows)==2 and sum(r['status']=='cancelled' for r in rows)==1;reopened.close()
 print(json.dumps({'concurrent_requests':20,'created':1,'conflicts':19,'cancel_rebook':True,'reopen_preserves_history':True}))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime', default='runtime/development_m2')
    p.add_argument('--group', default='software')
    p.add_argument('--bundle', required=True)
    args=p.parse_args()
    store=Store(args.runtime,args.group);state=store.load()
    source,bundle=materialize(store,state,args.bundle,store.path/'checks',args.bundle+'-concurrency')
    candidates=[item['path'][:-len('/web.py')].replace('/','.') for item in bundle['payload']['files'] if item['path'].endswith('meeting_rooms/web.py')]
    if len(candidates)!=1: raise ValueError('Expected one meeting_rooms package in selected bundle')
    result=Sandbox(Config.from_env().image).run(source,['python','-c','PACKAGE='+repr(candidates[0])+'\n'+CHECK],timeout=60)
    result['subject_digest']=bundle['sha256'];result['bundle_id']=bundle['id']
    dump(store.path/('concurrency-'+bundle['id']+'.json'),result)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['exit_code']==0 else 1


if __name__=='__main__':raise SystemExit(main())
